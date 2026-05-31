"""Tests for the self-capture guard. OpenReLife must never screenshot itself.

The original guard checked only the window TITLE, but the macOS window server
often returns an empty title for the Electron window (stored as "Unknown Title"),
which let OpenReLife self-capture whenever the title came back blank. The fix
keys on the focused APP NAME, which is reported reliably.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from openrelife.utils import is_self_capture


def test_skips_when_app_name_is_openrelife_even_with_empty_title():
    # The regression: title unavailable -> "Unknown Title", but app name is reliable.
    assert is_self_capture("OpenReLife", "Unknown Title") is True
    assert is_self_capture("OpenReLife", "") is True
    assert is_self_capture("OpenReLife", None) is True


def test_skips_when_title_contains_openrelife():
    assert is_self_capture("", "OpenReLife") is True


def test_does_not_skip_other_apps():
    assert is_self_capture("Comet", "Browser Harness") is False
    assert is_self_capture("iTerm2", "xela92@MPB") is False


def test_does_not_skip_workspace_named_openrelife():
    # A Muxy/tmux workspace titled "openrelife — Search" is the user's WORK, not the
    # app — exact app-name match + case-sensitive title keep it recorded.
    assert is_self_capture("Muxy", "openrelife — Search") is False
    assert is_self_capture("Muxy", "openrelife — Multi-Monitor") is False


def test_handles_none_inputs():
    assert is_self_capture(None, None) is False
