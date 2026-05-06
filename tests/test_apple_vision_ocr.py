"""Unit tests for openrelife.apple_vision_ocr (mocked, always run)."""
from unittest.mock import patch

import pytest


def _reload_module():
    import importlib
    import openrelife.apple_vision_ocr as m
    importlib.reload(m)
    return m


def test_unavailable_when_not_darwin():
    with patch("sys.platform", "linux"):
        m = _reload_module()
        assert m.is_apple_vision_available() is False


def test_unavailable_when_intel_mac():
    with patch("sys.platform", "darwin"), \
         patch("platform.machine", return_value="x86_64"):
        m = _reload_module()
        assert m.is_apple_vision_available() is False


def test_unavailable_when_old_macos():
    with patch("sys.platform", "darwin"), \
         patch("platform.machine", return_value="arm64"), \
         patch("platform.mac_ver", return_value=("11.0", ("", "", ""), "arm64")):
        m = _reload_module()
        assert m.is_apple_vision_available() is False


def test_unavailable_when_vision_import_fails():
    """If Vision framework is not installed, must return False without raising."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("Vision", "Quartz"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    with patch("sys.platform", "darwin"), \
         patch("platform.machine", return_value="arm64"), \
         patch("platform.mac_ver", return_value=("14.0", ("", "", ""), "arm64")), \
         patch.object(builtins, "__import__", side_effect=fake_import):
        m = _reload_module()
        assert m.is_apple_vision_available() is False


def test_normalize_bbox_flips_y_axis():
    from openrelife.apple_vision_ocr import _normalize_bbox
    out = _normalize_bbox(top_left=(0.1, 0.9), bottom_right=(0.2, 0.8))
    # IEEE 754: 1.0 - 0.9 != 0.1 exactly, so use approx for the y values
    assert out == pytest.approx({"x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2})


def test_normalize_bbox_preserves_x():
    from openrelife.apple_vision_ocr import _normalize_bbox
    out = _normalize_bbox(top_left=(0.5, 1.0), bottom_right=(0.7, 0.9))
    assert out["x1"] == 0.5
    assert out["x2"] == 0.7


def test_system_recognition_languages_appends_en_us():
    from openrelife import apple_vision_ocr as m
    with patch.object(m, "_preferred_system_language", return_value="it-IT"), \
         patch.object(m, "_vision_supported_languages",
                      return_value=["en-US", "it-IT", "fr-FR"]):
        langs = m._system_recognition_languages()
    assert langs == ["it-IT", "en-US"]


def test_system_recognition_languages_dedupes_when_locale_is_en_us():
    from openrelife import apple_vision_ocr as m
    with patch.object(m, "_preferred_system_language", return_value="en-US"), \
         patch.object(m, "_vision_supported_languages",
                      return_value=["en-US", "it-IT"]):
        langs = m._system_recognition_languages()
    assert langs == ["en-US"]


def test_system_recognition_languages_drops_unsupported_locale():
    from openrelife import apple_vision_ocr as m
    with patch.object(m, "_preferred_system_language", return_value="zxx-XX"), \
         patch.object(m, "_vision_supported_languages",
                      return_value=["en-US", "it-IT"]):
        langs = m._system_recognition_languages()
    assert langs == ["en-US"]


def test_extract_text_with_vision_function_exists_and_signature():
    """Smoke-test that the function is exported with the expected signature."""
    import inspect
    from openrelife.apple_vision_ocr import extract_text_with_vision
    sig = inspect.signature(extract_text_with_vision)
    params = list(sig.parameters.keys())
    assert params == ["image"]


def test_utf16_offset_ascii_passthrough():
    from openrelife.apple_vision_ocr import _utf16_offset
    s = "Hello world"
    assert _utf16_offset(s, 0) == 0
    assert _utf16_offset(s, 5) == 5
    assert _utf16_offset(s, len(s)) == len(s)


def test_utf16_offset_handles_emoji():
    """Emoji like U+1F600 are 1 Python code point but 2 UTF-16 code units."""
    from openrelife.apple_vision_ocr import _utf16_offset
    s = "a\U0001F600b"
    assert _utf16_offset(s, 0) == 0
    assert _utf16_offset(s, 1) == 1
    assert _utf16_offset(s, 2) == 3
    assert _utf16_offset(s, 3) == 4


def test_extract_text_with_vision_propagates_errors_via_mocks(monkeypatch):
    """Mock-driven: when performRequests_error_ returns (False, error),
    extract_text_with_vision must raise RuntimeError with the localized message.
    Cross-platform: substitutes Vision via monkeypatch, so does not need real Vision.
    """
    import sys
    import numpy as np
    from openrelife import apple_vision_ocr as m

    fake_handler = type("FakeHandler", (), {
        "performRequests_error_": lambda self, reqs, err: (False, type("E", (), {"localizedDescription": lambda self: "boom"})())
    })()
    fake_request = type("FakeReq", (), {
        "setRecognitionLevel_": lambda self, _: None,
        "setUsesLanguageCorrection_": lambda self, _: None,
        "setRecognitionLanguages_": lambda self, _: None,
        "results": lambda self: [],
    })()

    class FakeVision:
        VNRequestTextRecognitionLevelAccurate = 1
        class VNImageRequestHandler:
            @staticmethod
            def alloc():
                return type("A", (), {"initWithCGImage_options_": lambda self, *a: fake_handler})()
        class VNRecognizeTextRequest:
            @staticmethod
            def alloc():
                return type("A", (), {"init": lambda self: fake_request})()

    monkeypatch.setitem(sys.modules, "Vision", FakeVision)
    monkeypatch.setattr(m, "_np_image_to_cgimage",
                        lambda img: (object(), object()))
    monkeypatch.setattr(m, "_system_recognition_languages",
                        lambda: ["en-US"])

    with pytest.raises(RuntimeError, match="boom"):
        m.extract_text_with_vision(np.zeros((10, 10, 3), dtype=np.uint8))
