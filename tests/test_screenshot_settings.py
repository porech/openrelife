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


# --- OCR compute mode: eco / disabled ---

def _batch_params(mode, *, on_ac, has_bat=True, user_active=True, battery_level=50):
    """Call _get_batch_params with the given mode and a mocked power state."""
    from unittest.mock import patch
    from openrelife import screenshot
    screenshot.set_ocr_compute_mode(mode)
    try:
        with patch.object(screenshot, "_is_on_ac_power", return_value=on_ac), \
             patch.object(screenshot, "_has_battery", return_value=has_bat), \
             patch.object(screenshot, "is_user_active", return_value=user_active), \
             patch.object(screenshot, "_get_battery_level", return_value=battery_level):
            return screenshot._get_batch_params(10)
    finally:
        screenshot.set_ocr_compute_mode("smart")  # don't leak the mode into other tests


def test_compute_mode_accepts_eco_and_disabled():
    from openrelife import screenshot
    screenshot.set_ocr_compute_mode("eco")
    assert screenshot.get_ocr_compute_mode() == "eco"
    screenshot.set_ocr_compute_mode("disabled")
    assert screenshot.get_ocr_compute_mode() == "disabled"
    screenshot.set_ocr_compute_mode("bogus")  # invalid is rejected, mode unchanged
    assert screenshot.get_ocr_compute_mode() == "disabled"
    screenshot.set_ocr_compute_mode("smart")


def test_disabled_never_ocrs_regardless_of_power():
    # max_batch == 0 in every power state => the worker skips OCR entirely.
    assert _batch_params("disabled", on_ac=True, battery_level=100) == (0, 0, 1.0)
    assert _batch_params("disabled", on_ac=False) == (0, 0, 1.0)


def test_eco_skips_on_battery_and_throttles_on_ac():
    # On battery: skip entirely.
    assert _batch_params("eco", on_ac=False) == (0, 0, 1.0)
    # AC + idle: tiny batch, single thread.
    assert _batch_params("eco", on_ac=True, user_active=False) == (5, 1, 1.0)
    # AC + active + fully charged: very small batch, long cooldown.
    assert _batch_params("eco", on_ac=True, user_active=True, battery_level=100) == (3, 1, 2.0)
    # AC + active + still charging: defer until charging completes.
    assert _batch_params("eco", on_ac=True, user_active=True, battery_level=80) == (0, 0, 1.0)
