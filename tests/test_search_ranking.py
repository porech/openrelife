"""Tests for search_entries_streaming relevance ranking, thresholding and pagination.

Regression coverage for issue #8: previously the recency term (timestamp / 1e10,
~1.8e5 for microsecond timestamps) dwarfed the semantic score (<= 1), so search
was effectively reverse-chronological, capped at 20 results, with no relevance
floor. The new behaviour ranks by semantic similarity, applies a configurable
cosine cutoff (keyword matches bypass it), and supports offset/limit pagination.
"""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

temp_db_file = tempfile.NamedTemporaryFile(delete=False)
mock_db_path = temp_db_file.name
temp_db_file.close()

with patch('openrelife.config.db_path', mock_db_path):
    from openrelife.database import create_db, insert_entry, search_entries_streaming
    import openrelife.database
# NB: do not reassign openrelife.database.db_path at import time — that global is
# shared across test modules and would clobber e.g. test_database's path during
# collection. Each test points it at our temp DB in setUp instead.

# Microsecond timestamps, matching the real schema (capture uses time-based us).
DAY_US = 24 * 60 * 60 * 1_000_000
NOW_US = 1_780_000_000_000_000


def emb(*vals):
    return np.array(vals, dtype=np.float32)


class TestSearchRanking(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        openrelife.database.db_path = mock_db_path
        create_db()

    def setUp(self):
        # Re-point the shared global at our temp DB in case another test module
        # changed it, then start from a clean table.
        openrelife.database.db_path = mock_db_path
        conn = sqlite3.connect(mock_db_path)
        conn.execute("DELETE FROM entries")
        conn.commit()
        conn.close()

    def test_semantic_relevance_beats_recency(self):
        """An old but semantically identical entry must outrank a recent unrelated one."""
        old_relevant_ts = NOW_US - 100 * DAY_US
        recent_unrelated_ts = NOW_US - 1 * DAY_US
        insert_entry("old relevant", old_relevant_ts, emb(1, 0, 0), "App", "T")
        insert_entry("recent unrelated", recent_unrelated_ts, emb(0, 1, 0), "App", "T")

        res = search_entries_streaming(emb(1, 0, 0), query_text="", now_us=NOW_US)
        results = res["results"]
        self.assertEqual(results[0]["timestamp"], old_relevant_ts,
                         "Semantically identical entry should rank first despite being older")

    def test_threshold_excludes_weak_matches(self):
        """With no keyword match, entries below the cosine cutoff are dropped (empty list)."""
        insert_entry("unrelated text", NOW_US, emb(0, 1, 0), "App", "T")
        res = search_entries_streaming(emb(1, 0, 0), query_text="zzz",
                                       min_similarity=0.25, now_us=NOW_US)
        self.assertEqual(res["results"], [])
        self.assertEqual(res["total"], 0)

    def test_keyword_match_bypasses_threshold(self):
        """An entry containing the query text is included even with low semantic similarity."""
        insert_entry("the secret password is hunter2", NOW_US, emb(0, 1, 0), "App", "T")
        res = search_entries_streaming(emb(1, 0, 0), query_text="hunter2",
                                       min_similarity=0.25, now_us=NOW_US)
        self.assertEqual(len(res["results"]), 1)
        self.assertEqual(res["results"][0]["text"], "the secret password is hunter2")

    def test_recency_only_breaks_near_ties(self):
        """Two equally-relevant entries are ordered newest-first, but recency never flips a clear semantic winner."""
        strong_old = NOW_US - 50 * DAY_US
        weak_new = NOW_US
        insert_entry("strong", strong_old, emb(1, 0, 0), "App", "T")       # cosine 1.0
        insert_entry("weak", weak_new, emb(0.7, 0.7, 0), "App", "T")        # cosine ~0.71
        res = search_entries_streaming(emb(1, 0, 0), query_text="", now_us=NOW_US)
        self.assertEqual(res["results"][0]["timestamp"], strong_old)

        # Equal relevance -> newer first
        ts_a = NOW_US - 10 * DAY_US
        ts_b = NOW_US - 5 * DAY_US
        self.setUp()
        insert_entry("a", ts_a, emb(1, 0, 0), "App", "T")
        insert_entry("b", ts_b, emb(1, 0, 0), "App", "T")
        res = search_entries_streaming(emb(1, 0, 0), query_text="", now_us=NOW_US)
        self.assertEqual(res["results"][0]["timestamp"], ts_b)

    def test_pagination_reaches_beyond_20(self):
        """More than 20 relevant entries are all reachable via offset/limit."""
        for i in range(25):
            insert_entry(f"entry {i}", NOW_US - i * DAY_US, emb(1, 0, 0), "App", "T")

        page1 = search_entries_streaming(emb(1, 0, 0), limit=10, offset=0, now_us=NOW_US)
        self.assertEqual(len(page1["results"]), 10)
        self.assertEqual(page1["total"], 25)
        self.assertTrue(page1["has_more"])

        page3 = search_entries_streaming(emb(1, 0, 0), limit=10, offset=20, now_us=NOW_US)
        self.assertEqual(len(page3["results"]), 5)
        self.assertFalse(page3["has_more"])

        # No overlap between pages
        ids1 = {r["timestamp"] for r in page1["results"]}
        ids3 = {r["timestamp"] for r in page3["results"]}
        self.assertEqual(ids1 & ids3, set())

    def test_adaptive_threshold_relative_to_top(self):
        """The floor adapts to the best match for the query, not a fixed cutoff.

        With query [1,0,0] and unit embeddings, cosine == the x component:
        top match 0.40, a 0.35 match is within the 0.12 margin (cutoff 0.28) and
        survives, a 0.20 match is above the absolute floor but below the adaptive
        cutoff and is dropped.
        """
        def unit_x(c):
            return emb(c, float(np.sqrt(1 - c * c)), 0)

        insert_entry("top", NOW_US, unit_x(0.40), "App", "T")
        insert_entry("mid", NOW_US - 1, unit_x(0.35), "App", "T")
        insert_entry("low", NOW_US - 2, unit_x(0.20), "App", "T")

        # dedup disabled: this test isolates the adaptive threshold, and the three
        # synthetic rows share app/title so dedup would otherwise collapse them.
        res = search_entries_streaming(emb(1, 0, 0), query_text="",
                                       relevance_margin=0.12, min_similarity=0.15,
                                       dedupe_window_us=0, now_us=NOW_US)
        texts = {r["text"] for r in res["results"]}
        self.assertEqual(texts, {"top", "mid"})
        self.assertEqual(res["total"], 2)

    def test_keyword_matches_rank_above_semantic_then_by_recency(self):
        """Tier 1 (literal matches) ranks above Tier 2 (semantic-only), and within
        the keyword tier the most recent wins — the recall-tool ordering."""
        old_kw = NOW_US - 30 * DAY_US
        recent_kw = NOW_US - 1 * DAY_US
        # Two entries containing "invoice" with weak semantic similarity...
        insert_entry("old invoice receipt", old_kw, emb(0, 1, 0), "App", "T")
        insert_entry("recent invoice receipt", recent_kw, emb(0, 1, 0), "App", "T")
        # ...and one with NO literal match but maximal semantic similarity.
        insert_entry("unrelated screen", NOW_US, emb(1, 0, 0), "App", "T")

        res = search_entries_streaming(emb(1, 0, 0), query_text="invoice", now_us=NOW_US)
        texts = [r["text"] for r in res["results"]]
        self.assertEqual(texts, [
            "recent invoice receipt",  # keyword tier, most recent first
            "old invoice receipt",     # keyword tier, older
            "unrelated screen",        # semantic-only tier, ranked last despite cosine 1.0
        ])

    def test_keyword_matching_is_whole_word(self):
        """A query word matches only whole words, not substrings: "cani" must not
        match "meccanici" (so it falls below threshold and drops out)."""
        insert_entry("officina meccanici aperto", NOW_US, emb(0, 1, 0), "A", "X")
        insert_entry("i miei cani dormono", NOW_US - 60_000_000, emb(0, 1, 0), "B", "Y")

        res = search_entries_streaming(emb(1, 0, 0), query_text="cani", now_us=NOW_US)
        texts = [r["text"] for r in res["results"]]
        self.assertEqual(texts, ["i miei cani dormono"])
        self.assertEqual(res["total"], 1)

    def test_dedupes_consecutive_same_window_captures(self):
        """A run of same app+title captures close in time collapses to one result;
        a different window, and the same window after a long gap, are kept."""
        SEC = 1_000_000
        # A continuous run of the same window (app A / title X), ~10s apart.
        insert_entry("report alpha", NOW_US, emb(0, 1, 0), "A", "X")
        insert_entry("report alpha", NOW_US - 10 * SEC, emb(0, 1, 0), "A", "X")
        insert_entry("report alpha", NOW_US - 20 * SEC, emb(0, 1, 0), "A", "X")
        # A different window in between (kept).
        insert_entry("report beta", NOW_US - 25 * SEC, emb(0, 1, 0), "B", "Y")
        # Same window as the run, but an hour earlier (separate session, kept).
        insert_entry("report alpha", NOW_US - 3600 * SEC, emb(0, 1, 0), "A", "X")

        res = search_entries_streaming(emb(1, 0, 0), query_text="report",
                                       dedupe_window_us=120 * SEC, now_us=NOW_US)
        ts = [r["timestamp"] for r in res["results"]]
        self.assertEqual(ts, [NOW_US, NOW_US - 25 * SEC, NOW_US - 3600 * SEC])
        self.assertEqual(res["total"], 3)

        # With dedup disabled, all five rows come back.
        res2 = search_entries_streaming(emb(1, 0, 0), query_text="report",
                                        dedupe_window_us=0, now_us=NOW_US)
        self.assertEqual(res2["total"], 5)

    def test_zero_query_norm_returns_empty(self):
        insert_entry("x", NOW_US, emb(1, 0, 0), "App", "T")
        res = search_entries_streaming(emb(0, 0, 0), now_us=NOW_US)
        self.assertEqual(res["results"], [])
        self.assertEqual(res["total"], 0)


if __name__ == '__main__':
    unittest.main()
