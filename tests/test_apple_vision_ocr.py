"""Unit tests for openrelife.apple_vision_ocr (mocked, always run)."""
from unittest.mock import patch


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
    assert out == {"x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2}


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
