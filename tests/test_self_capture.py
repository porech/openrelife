"""Tests for the self-capture guard: OpenReLife must not screenshot its own window
(even when visible on a non-focused monitor). is_own_window_visible_osx inspects
the on-screen window list; here we mock Quartz so it runs on any platform."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import openrelife.utils as U


def _patch_windows(wins):
    # Force the three Quartz symbols non-None and CGWindowListCopyWindowInfo -> wins.
    return patch.multiple(
        U,
        CGWindowListCopyWindowInfo=lambda *a, **k: wins,
        kCGNullWindowID=0,
        kCGWindowListOptionOnScreenOnly=1,
    )


def test_true_when_large_own_window_on_screen():
    wins = [
        {"kCGWindowOwnerName": "Code", "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 1200, "Height": 800}},
        {"kCGWindowOwnerName": "OpenReLife", "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 900, "Height": 700}},
    ]
    with _patch_windows(wins):
        assert U.is_own_window_visible_osx() is True


def test_false_when_no_own_window():
    wins = [{"kCGWindowOwnerName": "Safari", "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 1200, "Height": 800}}]
    with _patch_windows(wins):
        assert U.is_own_window_visible_osx() is False


def test_ignores_tray_layer_and_tiny_windows():
    wins = [
        {"kCGWindowOwnerName": "OpenReLife", "kCGWindowLayer": 25, "kCGWindowBounds": {"Width": 900, "Height": 700}},  # tray/menubar layer
        {"kCGWindowOwnerName": "OpenReLife", "kCGWindowLayer": 0, "kCGWindowBounds": {"Width": 48, "Height": 48}},     # tiny popover
    ]
    with _patch_windows(wins):
        assert U.is_own_window_visible_osx() is False


def test_false_when_quartz_unavailable():
    with patch.object(U, 'CGWindowListCopyWindowInfo', None):
        assert U.is_own_window_visible_osx() is False
