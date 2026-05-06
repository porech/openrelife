"""Tests for the use_apple_vision setting in openrelife.screenshot."""


def test_use_apple_vision_default_is_false():
    """Default must be False (we don't want to flip behavior just by importing)."""
    import importlib
    from openrelife import screenshot
    importlib.reload(screenshot)
    assert screenshot.get_use_apple_vision() is False


def test_set_use_apple_vision_roundtrip():
    from openrelife import screenshot
    screenshot.set_use_apple_vision(True)
    assert screenshot.get_use_apple_vision() is True
    screenshot.set_use_apple_vision(False)
    assert screenshot.get_use_apple_vision() is False


def test_set_use_apple_vision_coerces_truthy():
    from openrelife import screenshot
    screenshot.set_use_apple_vision(1)
    assert screenshot.get_use_apple_vision() is True
    screenshot.set_use_apple_vision(0)
    assert screenshot.get_use_apple_vision() is False
