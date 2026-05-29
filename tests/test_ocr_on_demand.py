"""Tests for on-demand (dwell-triggered) single-frame OCR helper."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import openrelife.screenshot as sc


def test_ocr_one_frame_missing_file_returns_none():
    """If the screenshot for a timestamp is gone, OCR is a no-op returning None
    (the endpoint turns this into a 404 rather than crashing)."""
    with tempfile.TemporaryDirectory() as d:
        with patch.object(sc, 'screenshots_path', d):
            assert sc.ocr_one_frame(99999999, use_apple_vision=False) is None
