"""Tests for the per-query ranked-id cache behind search_entries_streaming.

The full ranking scan costs ~18s, so the cache is what makes pagination ("load
more") instant and must invalidate correctly: stable across the continuous OCR
text-fill workload, but refreshed on new captures and cleared on deletes.
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
    from openrelife.database import (
        create_db, insert_entry, update_entry_ocr, delete_entries,
        search_entries_streaming,
    )
    import openrelife.database as DB

EMB = np.array([1, 0, 0], dtype=np.float32)


class TestSearchCache(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        DB.db_path = mock_db_path
        create_db()

    def setUp(self):
        DB.db_path = mock_db_path
        conn = sqlite3.connect(mock_db_path)
        conn.execute("DELETE FROM entries")
        conn.commit()
        conn.close()
        DB._invalidate_rank_cache()

    def _spy(self):
        """Patch the scan with a call-counting wrapper around the real one."""
        return patch.object(DB, '_ranked_ids_for_query', side_effect=DB._ranked_ids_for_query)

    def test_pagination_served_from_cache_without_rescan(self):
        for i in range(5):
            insert_entry(f"report {i}", 1000 + i, EMB, "App", f"T{i}")
        with self._spy() as spy:
            p1 = search_entries_streaming(EMB, query_text="report", limit=2, offset=0)
            p2 = search_entries_streaming(EMB, query_text="report", limit=2, offset=2)
            self.assertEqual(spy.call_count, 1, "second page must be served from cache, not rescanned")
        self.assertEqual(p1["total"], 5)
        self.assertEqual(len(p1["results"]), 2)
        self.assertEqual(len(p2["results"]), 2)
        s1 = {r["timestamp"] for r in p1["results"]}
        s2 = {r["timestamp"] for r in p2["results"]}
        self.assertEqual(s1 & s2, set(), "pages must not overlap")

    def test_ocr_fill_keeps_cache_hot(self):
        """Filling OCR text (update_entry_ocr) changes text/updated_at but not
        timestamp/count, so the freshness token holds and pagination stays instant."""
        insert_entry("report a", 2000, EMB, "App", "T1")
        insert_entry("report b", 2001, EMB, "App", "T2")
        with self._spy() as spy:
            search_entries_streaming(EMB, query_text="report", limit=10)
            update_entry_ocr(2000, "report a updated text", EMB)
            search_entries_streaming(EMB, query_text="report", limit=10)
            self.assertEqual(spy.call_count, 1, "OCR text-fill must NOT invalidate the ranked cache")

    def test_new_capture_invalidates_cache(self):
        insert_entry("report a", 3000, EMB, "App", "T1")
        with self._spy() as spy:
            search_entries_streaming(EMB, query_text="report", limit=10)
            insert_entry("report b", 9_000_000_000, EMB, "App", "T2")  # new capture: MAX + COUNT move
            DB._token_cache["value"] = None  # bypass the 1s token TTL for the test
            search_entries_streaming(EMB, query_text="report", limit=10)
            self.assertEqual(spy.call_count, 2, "a new capture must invalidate the cache")

    def test_delete_clears_cache(self):
        insert_entry("report a", 4000, EMB, "App", "T1")
        insert_entry("report b", 4001, EMB, "App", "T2")
        with self._spy() as spy:
            search_entries_streaming(EMB, query_text="report", limit=10)
            delete_entries([4000])
            search_entries_streaming(EMB, query_text="report", limit=10)
            self.assertEqual(spy.call_count, 2, "delete_entries must clear the ranked cache")

    def test_build_snippet_centers_on_match_and_inlines(self):
        text = "line one\nsome preface words then the invoice total is due\ntrailing"
        snip = DB.build_snippet(text, "invoice", width=40)
        self.assertIn("invoice", snip)
        self.assertNotIn("\n", snip, "snippet must collapse newlines to read inline")


if __name__ == '__main__':
    unittest.main()
