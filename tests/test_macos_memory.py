"""Check that native window values are converted before their pool drains."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("lookup", ["app", "title", "browsers"])
def test_window_lookups_return_python_values_after_pool_drains(monkeypatch, lookup):
    import sys
    from openrelife import utils

    active = 0

    @contextmanager
    def pool():
        nonlocal active
        active += 1
        try:
            yield
        finally:
            active -= 1

    class NativeText:
        def __str__(self):
            assert active > 0
            return "Example window"

    workspace = SimpleNamespace(activeApplication=lambda: {"NSApplicationName": NativeText()})
    monkeypatch.setattr(utils.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "objc", SimpleNamespace(autorelease_pool=pool))
    monkeypatch.setattr(utils, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
    monkeypatch.setattr(utils, "kCGNullWindowID", 0)
    monkeypatch.setattr(utils, "kCGWindowListOptionOnScreenOnly", 1)
    owner = "Google Chrome" if lookup == "browsers" else "Example window"
    monkeypatch.setattr(utils, "CGWindowListCopyWindowInfo", lambda *_: [{
        "kCGWindowOwnerName": owner, "kCGWindowOwnerPID": 123,
        "kCGWindowName": NativeText(),
    }])
    monkeypatch.setattr(utils, "AppServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: object(),
        AXUIElementCopyAttributeValue=lambda obj, attr, out:
            (0, [object()] if attr == "AXWindows" else NativeText()),
    ))
    if lookup == "app":
        result = utils.get_active_app_name_osx()
    elif lookup == "title":
        result = utils.get_active_window_title_osx()
    else:
        result = utils._get_all_visible_browser_windows_osx()
    assert active == 0
    if lookup == "browsers":
        assert result == [("Google Chrome", "Example window")]
        assert all(type(value) is str for value in result[0])
    else:
        assert result == "Example window" and type(result) is str
