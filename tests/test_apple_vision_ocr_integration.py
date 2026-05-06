"""Integration tests for Apple Vision OCR. Skipped on non-arm64-darwin."""
import platform
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ARM64_DARWIN = sys.platform == "darwin" and platform.machine() == "arm64"
pytestmark = pytest.mark.skipif(
    not ARM64_DARWIN,
    reason="Apple Vision only available on Apple Silicon Mac",
)

FIX_DIR = Path(__file__).parent / "fixtures" / "apple_vision"


def _load(name: str) -> np.ndarray:
    img = Image.open(FIX_DIR / name).convert("RGB")
    return np.array(img)


def test_extract_italian_simple():
    from openrelife.apple_vision_ocr import extract_text_with_vision
    img = _load("italian_simple.png")
    text, words = extract_text_with_vision(img)
    lower = text.lower()
    assert "ciao" in lower
    assert "mondo" in lower
    assert len(words) > 0
    for w in words:
        assert 0.0 <= w["x1"] <= 1.0
        assert 0.0 <= w["y1"] <= 1.0
        assert 0.0 <= w["x2"] <= 1.0
        assert 0.0 <= w["y2"] <= 1.0
    # Top-left origin: text rendered at y=40 of 600 -> y1 should be in upper half
    first = words[0]
    assert first["y1"] < 0.5, f"expected y1<0.5 (top-left origin), got {first}"


def test_extract_english_simple():
    from openrelife.apple_vision_ocr import extract_text_with_vision
    img = _load("english_simple.png")
    text, words = extract_text_with_vision(img)
    lower = text.lower()
    assert "hello" in lower
    assert "world" in lower
    assert len(words) > 0


def test_extract_blank_returns_empty():
    from openrelife.apple_vision_ocr import extract_text_with_vision
    img = _load("blank.png")
    text, words = extract_text_with_vision(img)
    assert text == ""
    assert words == []
