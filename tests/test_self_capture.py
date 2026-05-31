"""Tests for the self-capture guard. OpenReLife must not screenshot its own
window, but it must keep capturing OTHER monitors while its window is open.
We test the pure decision function (which monitor shows our app frontmost) with
synthetic display/window layouts so it runs on any platform."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from openrelife.utils import _monitor_indices_with_frontmost_own_window as decide


def win(owner, x, y, w, h, layer=0):
    return {"owner": owner, "layer": layer, "x": x, "y": y, "w": w, "h": h}


# Two side-by-side 1000x800 displays: -D1 at x=0, -D2 at x=1000.
TWO_DISPLAYS = [(0, 0, 1000, 800), (1000, 0, 1000, 800)]


def test_skips_only_the_monitor_showing_our_app():
    # OpenReLife frontmost on display 2 (x=1000), real work on display 1.
    windows = [
        win("OpenReLife", 1000, 0, 1000, 800),  # frontmost over display 2 center
        win("Code", 0, 0, 1000, 800),           # display 1
    ]
    assert decide(TWO_DISPLAYS, windows) == {1}


def test_captures_all_when_our_app_not_on_screen():
    windows = [win("Code", 0, 0, 1000, 800), win("Safari", 1000, 0, 1000, 800)]
    assert decide(TWO_DISPLAYS, windows) == set()


def test_occluded_own_window_is_not_skipped():
    # OpenReLife is BEHIND a fullscreen Code window on display 1 — front-to-back
    # order puts Code first, so display 1 shows Code, not us: do NOT skip it.
    windows = [
        win("Code", 0, 0, 1000, 800),         # frontmost on display 1 (covers our window)
        win("OpenReLife", 0, 0, 1000, 800),   # behind
    ]
    assert decide(TWO_DISPLAYS, windows) == set()


def test_ignores_tray_and_tiny_windows():
    windows = [
        win("OpenReLife", 1000, 0, 1000, 800, layer=25),  # tray/menubar layer
        win("OpenReLife", 1000, 0, 40, 40),               # tiny popover
        win("Notes", 1000, 0, 1000, 800),                 # actual frontmost layer-0
    ]
    assert decide(TWO_DISPLAYS, windows) == set()


def test_single_display_frontmost_own_window():
    one = [(0, 0, 1440, 900)]
    assert decide(one, [win("OpenReLife", 0, 0, 1440, 900)]) == {0}
    assert decide(one, [win("Telegram", 0, 0, 1440, 900)]) == set()
