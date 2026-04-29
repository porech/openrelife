"""Tests for the OCR dispatcher in openrelife.ocr."""
from unittest.mock import MagicMock, patch


def test_doctr_predictor_is_not_loaded_at_import_time():
    """Importing openrelife.ocr must NOT trigger doctr model loading."""
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    with patch("doctr.models.ocr_predictor") as mock_predictor:
        import openrelife.ocr  # noqa: F401
    mock_predictor.assert_not_called()


def test_extract_text_uses_doctr_when_called():
    """Dispatcher routes to doctr currently (no Vision wiring yet)."""
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    fake_predictor = MagicMock()
    fake_pages = MagicMock()
    fake_pages.pages = []
    fake_predictor.return_value = fake_pages
    with patch.object(ocr_mod, "_get_doctr_predictor", return_value=fake_predictor):
        import numpy as np
        text, words = ocr_mod.extract_text_from_image(np.zeros((10, 10, 3), dtype=np.uint8))
    fake_predictor.assert_called_once()
    assert text == ""
    assert words == []
