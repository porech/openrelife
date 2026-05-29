"""Tests for get_new_timestamps: bounded, timestamp-based sync.

Regression coverage for #5/#6: the previous /api/sync used an unbounded
updated_at-based query, so an OCR backlog (which bumps updated_at on thousands
of existing rows) flooded every 2s poll and starved the UI. The timestamp-based
poll only ever returns newly captured frames, capped at a limit, and never
re-surfaces re-OCR'd old frames.
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
        create_db, insert_entry, update_entry_ocr, get_new_timestamps,
    )
    import openrelife.database

EMB = np.array([0.1, 0.2, 0.3], dtype=np.float32)


class TestNewTimestampsSync(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        openrelife.database.db_path = mock_db_path
        create_db()

    def setUp(self):
        openrelife.database.db_path = mock_db_path
        conn = sqlite3.connect(mock_db_path)
        conn.execute("DELETE FROM entries")
        conn.commit()
        conn.close()

    def test_returns_only_newer_than_cursor(self):
        for ts in (100, 200, 300):
            insert_entry(f"t{ts}", ts, EMB, "App", "T")
        timestamps, cursor = get_new_timestamps(since_timestamp=150)
        self.assertEqual(timestamps, [200, 300])
        self.assertEqual(cursor, 300)

    def test_empty_when_nothing_newer(self):
        insert_entry("t", 100, EMB, "App", "T")
        timestamps, cursor = get_new_timestamps(since_timestamp=100)
        self.assertEqual(timestamps, [])
        self.assertEqual(cursor, 100)  # cursor unchanged so it never regresses

    def test_respects_limit_and_drains_oldest_first(self):
        for ts in range(1, 11):
            insert_entry(f"t{ts}", ts, EMB, "App", "T")
        # First poll: oldest 4 above cursor 0.
        page1, cursor1 = get_new_timestamps(since_timestamp=0, limit=4)
        self.assertEqual(page1, [1, 2, 3, 4])
        self.assertEqual(cursor1, 4)
        # Next poll resumes after cursor1 — no skips, no overlap.
        page2, cursor2 = get_new_timestamps(since_timestamp=cursor1, limit=4)
        self.assertEqual(page2, [5, 6, 7, 8])
        self.assertEqual(cursor2, 8)

    def test_reocr_of_old_frame_does_not_resurface(self):
        """Re-OCR'ing an old frame bumps updated_at but not timestamp, so a poll
        positioned past it returns nothing — this is the #5/#6 flood fix."""
        insert_entry("old", 100, EMB, "App", "T")
        insert_entry("recent", 500, EMB, "App", "T")
        # Client has already seen up to ts 500.
        _, cursor = get_new_timestamps(since_timestamp=500)
        self.assertEqual(cursor, 500)
        # OCR worker re-processes the old frame (bumps updated_at to "now").
        update_entry_ocr(100, "ocr text", EMB)
        timestamps, cursor2 = get_new_timestamps(since_timestamp=500)
        self.assertEqual(timestamps, [], "Re-OCR'd old frame must not flood sync")
        self.assertEqual(cursor2, 500)


if __name__ == '__main__':
    unittest.main()
