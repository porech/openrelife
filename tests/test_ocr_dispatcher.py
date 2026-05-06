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


def test_extract_text_uses_doctr_when_vision_disabled():
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


def test_dispatcher_calls_vision_when_enabled_and_available():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock(return_value=("hello", [{"text": "hello", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]))
    fake_doctr = MagicMock()
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr), \
         patch("openrelife.apple_vision_ocr.is_apple_vision_available", return_value=True):
        import numpy as np
        text, words = ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=True,
        )
    fake_vision.assert_called_once()
    fake_doctr.assert_not_called()
    assert text == "hello"


def test_dispatcher_falls_back_to_doctr_on_vision_exception():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock(side_effect=RuntimeError("Vision boom"))
    fake_doctr = MagicMock(return_value=("fallback", []))
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr), \
         patch("openrelife.apple_vision_ocr.is_apple_vision_available", return_value=True):
        import numpy as np
        text, words = ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=True,
        )
    fake_vision.assert_called_once()
    fake_doctr.assert_called_once()
    assert text == "fallback"


def test_dispatcher_uses_doctr_when_setting_disabled():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock()
    fake_doctr = MagicMock(return_value=("d", []))
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr):
        import numpy as np
        ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=False,
        )
    fake_vision.assert_not_called()
    fake_doctr.assert_called_once()


def test_dispatcher_uses_doctr_when_vision_unavailable():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock()
    fake_doctr = MagicMock(return_value=("d", []))
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr), \
         patch("openrelife.apple_vision_ocr.is_apple_vision_available", return_value=False):
        import numpy as np
        ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=True,
        )
    fake_vision.assert_not_called()
    fake_doctr.assert_called_once()
