"""Tests for the in-memory embedding index (issue #11): it must return the SAME
ranked results as the reference DB-scan, and stay in sync with OCR-fills and deletes.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

temp_db_file = tempfile.NamedTemporaryFile(delete=False)
mock_db_path = temp_db_file.name
temp_db_file.close()

with patch('openrelife.config.db_path', mock_db_path):
    from openrelife.database import (
        create_db, insert_entry, insert_entry_stub, update_entry_ocr, delete_entries,
        _ranked_ids_for_query_dbscan, _ranked_ids_for_query, fts_backfill_if_needed,
        fts_keyword_strengths,
    )
    import openrelife.database
    import openrelife.embedding_index as EI

# NB: do NOT set openrelife.database.db_path at module level — that global is shared
# across test modules and would clobber e.g. test_database. setUp(Class) points it at
# our temp DB per-test instead.
DIM = 384


def emb(c):
    """384-dim unit vector with cosine `c` to the query direction e0=[1,0,0,...]."""
    v = np.zeros(DIM, dtype=np.float32)
    v[0] = c
    v[1] = float(np.sqrt(max(0.0, 1.0 - c * c)))
    return v


QUERY = emb(1.0)  # points along e0


class TestEmbeddingIndex(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        openrelife.database.db_path = mock_db_path
        create_db()

    def setUp(self):
        openrelife.database.db_path = mock_db_path
        conn = sqlite3.connect(mock_db_path)
        conn.execute("DELETE FROM entries")
        try:
            conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('delete-all')")
        except sqlite3.Error:
            pass
        conn.commit(); conn.close()
        openrelife.database._invalidate_rank_cache()
        # reset module-global index state
        EI._current = None
        EI._matrix_ready.clear(); EI._fts_ready.clear()
        EI._del_pending.clear()

    def _warm_index(self):
        EI._initial_load()
        EI._matrix_ready.set()
        self.assertTrue(fts_backfill_if_needed())
        EI._fts_ready.set()
        self.assertTrue(EI.ready())

    def _seed(self):
        # ts distinct; distinct app/title so dedupe never collapses; controlled cosines.
        rows = [
            ("annual report final", 5000, emb(0.90), "AppA", "T1"),
            ("report draft notes",  4000, emb(0.84), "AppB", "T2"),
            ("budget overview",     3000, emb(0.80), "AppC", "T3"),   # semantic-only, high
            ("totally unrelated",   2000, emb(0.20), "AppD", "T4"),   # below cutoff, no kw
            ("another report here", 1000, emb(0.05), "AppE", "T5"),   # keyword, low semantic
        ]
        for text, ts, v, app, title in rows:
            insert_entry(text, ts, v, app, title)

    def test_matrix_matches_dbscan_for_various_queries(self):
        self._seed()
        self._warm_index()
        for qtext in ["report", "", "report draft", "annual"]:
            expected = _ranked_ids_for_query_dbscan(QUERY, qtext, 0.15, 0.12, 0)
            actual = _ranked_ids_for_query(QUERY, qtext, 0.15, 0.12, 0)  # dispatcher -> matrix
            self.assertEqual(actual, expected, f"divergence for query {qtext!r}")

    def test_dispatcher_uses_dbscan_when_not_ready(self):
        self._seed()
        # index not warmed -> ready() False -> dispatcher must equal dbscan
        self.assertFalse(EI.ready())
        q = "report"
        self.assertEqual(_ranked_ids_for_query(QUERY, q, 0.15, 0.12, 0),
                         _ranked_ids_for_query_dbscan(QUERY, q, 0.15, 0.12, 0))

    def test_ocr_fill_appears_after_poll(self):
        self._seed()
        self._warm_index()
        # a brand-new stub (no embedding) is NOT yet in the matrix
        insert_entry_stub(9000, "AppF", "T6")
        EI._poll_once()
        self.assertIsNone(EI._current.id_to_row.get(_id_of(9000)))  # still absent (zero embedding)
        # OCR fills it -> nonzero embedding + updated_at bump
        update_entry_ocr(9000, "fresh report appears", emb(0.95))
        EI._poll_once()
        ids, _ = _ranked_ids_for_query(QUERY, "report", 0.15, 0.12, 0)
        self.assertIn(_id_of(9000), ids, "OCR-filled frame should be searchable after one poll")

    def test_delete_removes_after_poll(self):
        self._seed()
        self._warm_index()
        ids_before, _ = _ranked_ids_for_query(QUERY, "report", 0.15, 0.12, 0)
        rid = _id_of(1000)  # "another report here"
        self.assertIn(rid, ids_before)
        delete_entries([1000])  # calls notify_delete
        EI._poll_once()
        ids_after, _ = _ranked_ids_for_query(QUERY, "report", 0.15, 0.12, 0)
        self.assertNotIn(rid, ids_after)

    def test_fts_keyword_parity_with_regex(self):
        self._seed()
        self.assertTrue(fts_backfill_if_needed())
        # "report" matches 3 rows containing the whole word; "annual" matches 1.
        kw = fts_keyword_strengths("report")
        texts = {r[0]: r[1] for r in sqlite3.connect(mock_db_path).execute("SELECT id,text FROM entries")}
        for rid, strength in kw.items():
            self.assertIn("report", texts[rid].lower())
        self.assertEqual(len(kw), 3)


def _id_of(timestamp):
    return sqlite3.connect(mock_db_path).execute(
        "SELECT id FROM entries WHERE timestamp=?", (timestamp,)).fetchone()[0]


if __name__ == '__main__':
    unittest.main()
