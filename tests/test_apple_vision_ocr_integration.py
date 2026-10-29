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


def test_repeated_worker_ocr_releases_native_image_buffers():
    """Catch image-sized growth in a fresh worker without an external pool."""
    import json
    import subprocess

    probe = Path(__file__).parent / "manual" / "probe_macos_memory.py"
    result = subprocess.run(
        [sys.executable, str(probe), "vision", "--pool", "none", "--count", "80"],
        capture_output=True, text=True, check=True, timeout=120,
    )
    samples = [json.loads(line) for line in result.stdout.splitlines()]
    after_warmup = next(row for row in samples if row.get("stage") == 20)
    last = next(row for row in samples if row.get("stage") == 80)
    # Without draining, 60 additional 1080p RGB buffers retain ~356 MiB.
    # Allow native caches substantial headroom while rejecting that accumulation.
    assert last["footprint_MiB"] - after_warmup["footprint_MiB"] < 96, samples
