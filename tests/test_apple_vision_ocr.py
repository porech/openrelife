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
