# Apple Vision OCR Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace doctr CPU OCR with Apple Vision on Apple Silicon Macs, keeping doctr as automatic fallback and as the only backend on other platforms.

**Architecture:** `openrelife/ocr.py` becomes a dispatcher with two private backends (`_extract_with_doctr`, `_extract_with_vision`); doctr is lazy-loaded so it's never instantiated when Vision works; new module `openrelife/apple_vision_ocr.py` wraps the PyObjC Vision bridge with platform detection cached per-process.

**Tech Stack:** Python 3.11+, PyObjC (Vision + Quartz frameworks), pytest with skipif markers for arm64-darwin tests, Flask for settings API, electron-builder for the app bundle.

**Spec:** `docs/superpowers/specs/2026-04-29-apple-vision-ocr-design.md`

**Tracking issue:** https://github.com/porech/openrelife/issues/4

---

## File Structure

### New files
| Path | Responsibility |
|---|---|
| `openrelife/apple_vision_ocr.py` | PyObjC Vision bridge: availability detection, language helper, Y-flip helper, `extract_text_with_vision`. All `Vision`/`Quartz` imports lazy. |
| `tests/test_apple_vision_ocr.py` | Unit tests, always run (mocked platform/imports). |
| `tests/test_apple_vision_ocr_integration.py` | Integration tests, skipif non-arm64-darwin. Uses fixtures. |
| `tests/test_ocr_dispatcher.py` | Dispatcher routing + fallback tests. |
| `tests/fixtures/apple_vision/generate.py` | Reproducible PIL script that renders the 3 fixture PNGs. |
| `tests/fixtures/apple_vision/italian_simple.png` | Generated fixture: "Ciao mondo, questa è una prova" |
| `tests/fixtures/apple_vision/english_simple.png` | Generated fixture: "Hello world, this is a test" |
| `tests/fixtures/apple_vision/blank.png` | Generated fixture: 800×600 white background |
| `tests/manual/benchmark_apple_vision.py` | Side-by-side per-phase timing on real frames. Not run by pytest. |

### Modified files
| Path | What changes |
|---|---|
| `openrelife/ocr.py` | Eager `ocr_predictor(...)` removed. Becomes dispatcher with lazy doctr (`_get_doctr_predictor`) and `_extract_with_vision` delegating to `apple_vision_ocr`. Public `extract_text_from_image(image)` signature unchanged. |
| `openrelife/screenshot.py` | New `_use_apple_vision` global with `set_use_apple_vision`/`get_use_apple_vision`. `_process_ocr_batch` signature gains `use_apple_vision: bool` argument. `ocr_worker_thread` reads setting and passes to subprocess. |
| `openrelife/app.py` | `load_settings` reads `use_apple_vision` (default-True only when key absent and platform supports). Two new routes `GET`/`POST /api/settings/apple_vision`. Unified `POST /api/settings` handler learns the new key. New "OCR Engine" section in modal HTML, rendered only when `available === true`. |
| `pyproject.toml` | `[project.optional-dependencies].macos` pins `pyobjc-framework-Vision==10.3` and `pyobjc-framework-Quartz==10.3` alongside the existing `pyobjc==10.3`. |

---

## Chunk 1: Foundation

This chunk lands the dispatcher refactor with **zero behavior change**: doctr stays the only backend, but is now lazy-loaded and routed through a dispatcher. After this chunk, every existing call path still uses doctr, but the architecture is ready for Vision wiring in Chunk 2/3.

### Task 1: Create worktree and feature branch

**Files:** none (workspace setup)

- [ ] **Step 1: Verify clean working tree**

```bash
cd /Users/xela92/pj/openrelife
git status --short
```

Expected: empty output (no uncommitted changes). The spec + plan commits should already be on `dev`.

- [ ] **Step 2: Create worktree using superpowers:using-git-worktrees skill**

Invoke the skill. It will create a worktree at `~/.claude-work/worktrees/openrelife-apple-vision` (or similar) on a new branch `feat/apple-vision-ocr` based off `dev`. From here on, all work happens in the worktree. The original `/Users/xela92/pj/openrelife` checkout stays on `dev` and untouched.

Expected result: a new directory with the repo checked out on `feat/apple-vision-ocr`, current working directory switched to the worktree.

- [ ] **Step 3: Verify worktree state**

```bash
git branch --show-current
git status --short
```

Expected: `feat/apple-vision-ocr`, clean tree.

---

### Task 2: Pin pyobjc Vision and Quartz frameworks

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit `pyproject.toml`**

Locate the `[project.optional-dependencies]` block (around the bottom of the file). Replace:

```toml
[project.optional-dependencies]
windows = ["pywin32", "psutil"]
macos = ["pyobjc==10.3"]
linux = []
```

with:

```toml
[project.optional-dependencies]
windows = ["pywin32", "psutil"]
macos = [
    "pyobjc==10.3",
    "pyobjc-framework-Vision==10.3",
    "pyobjc-framework-Quartz==10.3",
]
linux = []
```

- [ ] **Step 2: Regenerate the lockfile**

```bash
uv lock
```

Expected: `uv.lock` is updated with the two new entries (no extra wheel downloads — they were already pulled in transitively by the umbrella).

- [ ] **Step 3: Install into the OpenReLife venv**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pip install -e ".[macos]"
```

Expected: pip installs the framework wheels into the running app's venv (so we can run the new tests against it).

- [ ] **Step 4: Smoke-import Vision and Quartz**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -c "import Vision; import Quartz; print('ok')"
```

Expected: `ok` printed, no ImportError.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: pin pyobjc-framework-Vision and pyobjc-framework-Quartz"
```

---

### Task 3: Refactor `ocr.py` to lazy doctr (no dispatcher logic yet)

The goal of this task is to remove the eager doctr load and replace it with a lazy getter, keeping the public function `extract_text_from_image(image)` working with doctr. **No Vision dispatching yet** — this is a pure refactor that should not change behavior.

**Files:**
- Modify: `openrelife/ocr.py`
- Create: `tests/test_ocr_dispatcher.py`

- [ ] **Step 1: Write failing test for lazy doctr (predictor not loaded at import)**

Create `tests/test_ocr_dispatcher.py`:

```python
"""Tests for the OCR dispatcher in openrelife.ocr."""
from unittest.mock import MagicMock, patch


def test_doctr_predictor_is_not_loaded_at_import_time():
    """Importing openrelife.ocr must NOT trigger doctr model loading.
    On Apple Silicon Macs with Vision enabled, doctr should never be
    instantiated; this test guards against accidental eager loads.
    """
    # Re-import in isolation: clear any cached module first
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    # Patch ocr_predictor BEFORE the module is imported, so if it's called
    # at import time we'd see it.
    with patch("doctr.models.ocr_predictor") as mock_predictor:
        import openrelife.ocr  # noqa: F401
    mock_predictor.assert_not_called()


def test_extract_text_uses_doctr_when_vision_disabled(tmp_path):
    """With Vision setting OFF, dispatcher must route to doctr."""
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    fake_predictor = MagicMock()
    fake_pages = MagicMock()
    fake_pages.pages = []
    fake_predictor.return_value = fake_pages
    with patch.object(ocr_mod, "_get_doctr_predictor", return_value=fake_predictor):
        import numpy as np
        text, words = ocr_mod.extract_text_from_image(np.zeros((10, 10, 3), dtype=np.uint8))
    fake_predictor.assert_called_once()
    assert text == ""
    assert words == []
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/xela92/pj/openrelife  # use the worktree path here
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_ocr_dispatcher.py -v
```

Expected: FAIL — `_get_doctr_predictor` does not exist yet, and `mock_predictor.assert_not_called()` will fail because the current `ocr.py` calls `ocr_predictor(...)` at import time.

- [ ] **Step 3: Refactor `openrelife/ocr.py` to lazy load**

Replace the entire content of `openrelife/ocr.py` with:

```python
"""OCR dispatcher.

Public API: extract_text_from_image(image: np.ndarray) -> (str, List[Dict])
Currently dispatches to doctr only. Vision wiring is added in a later task.

doctr is lazy-loaded: the predictor is constructed on first call. On systems
where Vision works without errors (added later), doctr is never instantiated.
"""
from typing import Dict, List, Tuple

import numpy as np

_doctr_predictor = None


def _get_doctr_predictor():
    """Lazy-load the doctr OCR predictor. Cached after first call."""
    global _doctr_predictor
    if _doctr_predictor is None:
        from doctr.models import ocr_predictor
        _doctr_predictor = ocr_predictor(
            pretrained=True,
            det_arch="db_mobilenet_v3_large",
            reco_arch="crnn_mobilenet_v3_large",
        )
    return _doctr_predictor


def _extract_with_doctr(image: np.ndarray) -> Tuple[str, List[Dict]]:
    """OCR via doctr (PyTorch CPU). Returns (text, words_with_coords)."""
    predictor = _get_doctr_predictor()
    result = predictor([image])
    text = ""
    words_with_coords: List[Dict] = []
    for page in result.pages:
        for block in page.blocks:
            for line in block.lines:
                for word in line.words:
                    text += word.value + " "
                    x1, y1 = word.geometry[0]
                    x2, y2 = word.geometry[1]
                    words_with_coords.append({
                        "text": word.value,
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                    })
                text += "\n"
            text += "\n"
    return text, words_with_coords


def extract_text_from_image(image: np.ndarray) -> Tuple[str, List[Dict]]:
    """Run OCR on an RGB image and return (text, words_with_coords).

    words_with_coords: list of {text, x1, y1, x2, y2} with normalized
    coordinates in [0, 1], top-left origin.
    """
    return _extract_with_doctr(image)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_ocr_dispatcher.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the existing test suite to confirm no regressions**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/ -v
```

Expected: all existing tests pass (test_config, test_database, test_incognito, test_nlp, test_window_attrs).

- [ ] **Step 6: Commit**

```bash
git add openrelife/ocr.py tests/test_ocr_dispatcher.py
git commit -m "refactor(ocr): lazy-load doctr predictor"
```

---

### Task 4: Add `use_apple_vision` setting to `screenshot.py`

**Files:**
- Modify: `openrelife/screenshot.py`
- Create/append: `tests/test_screenshot_settings.py`

- [ ] **Step 1: Write failing tests for the new setting**

Create `tests/test_screenshot_settings.py`:

```python
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
    screenshot.set_use_apple_vision(1)  # truthy non-bool
    assert screenshot.get_use_apple_vision() is True
    screenshot.set_use_apple_vision(0)
    assert screenshot.get_use_apple_vision() is False
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_screenshot_settings.py -v
```

Expected: FAIL — `get_use_apple_vision`/`set_use_apple_vision` don't exist.

- [ ] **Step 3: Add the setting to `openrelife/screenshot.py`**

After the existing `ocr_compute_mode` block (around line 331-340), add:

```python
# OCR engine selection: True -> Apple Vision (M-series only); False -> doctr
_use_apple_vision: bool = False


def set_use_apple_vision(enabled) -> None:
    """Enable/disable Apple Vision backend. Coerces truthy/falsy values to bool."""
    global _use_apple_vision
    _use_apple_vision = bool(enabled)


def get_use_apple_vision() -> bool:
    global _use_apple_vision
    return _use_apple_vision
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_screenshot_settings.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add openrelife/screenshot.py tests/test_screenshot_settings.py
git commit -m "feat(screenshot): add use_apple_vision setting"
```

---

## Chunk 2: Apple Vision backend

This chunk creates `openrelife/apple_vision_ocr.py` with all helpers and the main `extract_text_with_vision` function. After this chunk, the function exists and is unit-tested, but the dispatcher in `ocr.py` still does not call it (wired in Chunk 3).

### Task 5: Create `apple_vision_ocr.py` skeleton with `is_apple_vision_available`

**Files:**
- Create: `openrelife/apple_vision_ocr.py`
- Create: `tests/test_apple_vision_ocr.py`

- [ ] **Step 1: Write failing tests for availability detection**

Create `tests/test_apple_vision_ocr.py`:

```python
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
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: FAIL — `openrelife.apple_vision_ocr` module does not exist.

- [ ] **Step 3: Create `openrelife/apple_vision_ocr.py` with detection only**

```python
"""Apple Vision OCR backend (PyObjC bridge).

This module is safe to import on any platform: heavyweight framework imports
(Vision, Quartz) happen lazily inside is_apple_vision_available() and the
public extract function. On non-arm64-darwin systems, is_apple_vision_available()
short-circuits before attempting any Vision import.
"""
from __future__ import annotations

import functools
import platform
import sys


@functools.lru_cache(maxsize=1)
def is_apple_vision_available() -> bool:
    """True only on Apple Silicon Mac with macOS 13+ AND Vision frameworks
    importable. Cached for the lifetime of the process.
    """
    if sys.platform != "darwin":
        return False
    if platform.machine() != "arm64":
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return False
    if major < 13:
        return False
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401
    except ImportError:
        return False
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add openrelife/apple_vision_ocr.py tests/test_apple_vision_ocr.py
git commit -m "feat(ocr): add Apple Vision availability detection"
```

---

### Task 6: Add Y-flip helper `_normalize_bbox`

Vision returns coordinates with **bottom-left origin**, normalized 0-1. Our schema uses **top-left origin**. This helper converts between them.

**Files:**
- Modify: `openrelife/apple_vision_ocr.py`
- Modify: `tests/test_apple_vision_ocr.py`

- [ ] **Step 1: Append failing tests for `_normalize_bbox`**

Append to `tests/test_apple_vision_ocr.py`:

```python
def test_normalize_bbox_flips_y_axis():
    from openrelife.apple_vision_ocr import _normalize_bbox
    # Vision-style bottom-left origin: a word in the TOP-LEFT corner has
    # top-left at (0.1, 0.9) and bottom-right at (0.2, 0.8).
    # Top-left origin equivalent: (0.1, 0.1) and (0.2, 0.2).
    out = _normalize_bbox(top_left=(0.1, 0.9), bottom_right=(0.2, 0.8))
    assert out == {"x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2}


def test_normalize_bbox_preserves_x():
    from openrelife.apple_vision_ocr import _normalize_bbox
    out = _normalize_bbox(top_left=(0.5, 1.0), bottom_right=(0.7, 0.9))
    assert out["x1"] == 0.5
    assert out["x2"] == 0.7
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: 4 pass + 2 fail (`_normalize_bbox` not defined).

- [ ] **Step 3: Add `_normalize_bbox` to `apple_vision_ocr.py`**

Append to `openrelife/apple_vision_ocr.py`:

```python
from typing import Dict, Tuple


def _normalize_bbox(top_left: Tuple[float, float],
                    bottom_right: Tuple[float, float]) -> Dict[str, float]:
    """Convert Vision (bottom-left origin) bbox to top-left origin bbox.

    Args:
        top_left: (x, y) where y is in [0, 1] measured from the bottom of the image.
        bottom_right: (x, y) likewise.

    Returns:
        {x1, y1, x2, y2} in top-left origin.
    """
    tl_x, tl_y = top_left
    br_x, br_y = bottom_right
    return {
        "x1": tl_x,
        "y1": 1.0 - tl_y,
        "x2": br_x,
        "y2": 1.0 - br_y,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add openrelife/apple_vision_ocr.py tests/test_apple_vision_ocr.py
git commit -m "feat(ocr): add Vision bbox Y-flip helper"
```

---

### Task 7: Add `_system_recognition_languages` helper

Returns the recognition languages list `[locale_first, "en-US"]` deduplicated, filtered against Vision's supported list.

**Files:**
- Modify: `openrelife/apple_vision_ocr.py`
- Modify: `tests/test_apple_vision_ocr.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_apple_vision_ocr.py`:

```python
def test_system_recognition_languages_appends_en_us():
    from openrelife import apple_vision_ocr as m
    # Mock the locale + supported-languages helpers; assert composition
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: 6 pass + 3 fail.

- [ ] **Step 3: Add the three helpers**

Append to `openrelife/apple_vision_ocr.py`:

```python
from typing import List


def _preferred_system_language() -> str:
    """First preferred system language as BCP-47 string (e.g. 'it-IT').

    Returns 'en-US' as a safe default if the lookup fails for any reason.
    """
    try:
        import Foundation  # PyObjC umbrella
        langs = Foundation.NSLocale.preferredLanguages()
        if langs and len(langs) > 0:
            return str(langs[0])
    except Exception:
        pass
    return "en-US"


def _vision_supported_languages() -> List[str]:
    """List of BCP-47 language codes supported by Vision text recognition
    at the Accurate level. Empty list on error (caller should fall back).
    """
    try:
        import Vision
        request = Vision.VNRecognizeTextRequest.alloc().init()
        level = Vision.VNRequestTextRecognitionLevelAccurate
        revision = Vision.VNRecognizeTextRequest.currentRevision()
        result, error = Vision.VNRecognizeTextRequest \
            .supportedRecognitionLanguagesForTextRecognitionLevel_revision_error_(
                level, revision, None
            )
        if error is not None or result is None:
            return []
        return [str(lang) for lang in result]
    except Exception:
        return []


def _system_recognition_languages() -> List[str]:
    """Compose the recognitionLanguages list: [system_locale, 'en-US']
    deduplicated, with unsupported entries dropped. If the system locale is
    not supported by Vision, returns ['en-US'] only.
    """
    locale = _preferred_system_language()
    supported = set(_vision_supported_languages())
    out: List[str] = []
    for code in (locale, "en-US"):
        if code in supported and code not in out:
            out.append(code)
    if not out:
        # supported list unexpectedly empty — fall back to en-US
        out = ["en-US"]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add openrelife/apple_vision_ocr.py tests/test_apple_vision_ocr.py
git commit -m "feat(ocr): add Vision recognition languages helper"
```

---

### Task 8: Implement `extract_text_with_vision`

The main entry point. Takes a numpy RGB image, returns `(text, words_with_coords)`.

**Files:**
- Modify: `openrelife/apple_vision_ocr.py`
- Modify: `tests/test_apple_vision_ocr.py` (mock-only sanity test; full integration in Chunk 4)

- [ ] **Step 1: Append a failing structure test**

Append to `tests/test_apple_vision_ocr.py`:

```python
def test_extract_text_with_vision_function_exists_and_signature():
    """Smoke-test that the function is exported with the expected signature.
    Real behavior is covered by integration tests on arm64-darwin only.
    """
    import inspect
    from openrelife.apple_vision_ocr import extract_text_with_vision
    sig = inspect.signature(extract_text_with_vision)
    params = list(sig.parameters.keys())
    assert params == ["image"]
```

- [ ] **Step 2: Run to confirm it fails**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py::test_extract_text_with_vision_function_exists_and_signature -v
```

Expected: FAIL — `extract_text_with_vision` not exported.

- [ ] **Step 3: Implement `extract_text_with_vision` and helpers**

Append to `openrelife/apple_vision_ocr.py`:

```python
import numpy as np


def _np_image_to_cgimage(image: np.ndarray):
    """Convert an HxWx3 RGB uint8 numpy array to a CGImage."""
    import Quartz
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(
            f"Expected HxWx3 uint8 RGB image, got shape={image.shape} dtype={image.dtype}"
        )
    h, w, _ = image.shape
    # Ensure C-contiguous
    if not image.flags["C_CONTIGUOUS"]:
        image = np.ascontiguousarray(image)
    raw_bytes = image.tobytes()
    provider = Quartz.CGDataProviderCreateWithData(None, raw_bytes, len(raw_bytes), None)
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    bits_per_component = 8
    bits_per_pixel = 24
    bytes_per_row = w * 3
    bitmap_info = Quartz.kCGImageAlphaNone | Quartz.kCGBitmapByteOrderDefault
    cg = Quartz.CGImageCreate(
        w, h,
        bits_per_component, bits_per_pixel, bytes_per_row,
        color_space, bitmap_info,
        provider, None, False, Quartz.kCGRenderingIntentDefault,
    )
    if cg is None:
        raise RuntimeError("CGImageCreate returned NULL")
    return cg


def _words_from_observation(observation) -> list:
    """Extract per-word coords from a VNRecognizedTextObservation.
    Returns a list of {text, x1, y1, x2, y2} entries (top-left origin).
    """
    candidates = observation.topCandidates_(1)
    if not candidates or len(candidates) == 0:
        return []
    top = candidates[0]
    full_string = str(top.string())
    if not full_string:
        return []
    out = []
    cursor = 0
    for token in full_string.split():
        idx = full_string.find(token, cursor)
        if idx < 0:
            continue
        # Build NSRange (NSMakeRange equivalent)
        ns_range = (idx, len(token))
        cursor = idx + len(token)
        try:
            bbox_obs, error = top.boundingBoxForRange_error_(ns_range, None)
        except Exception:
            continue
        if error is not None or bbox_obs is None:
            continue
        # bbox_obs is a VNRectangleObservation. Use topLeft / bottomRight.
        tl = bbox_obs.topLeft()
        br = bbox_obs.bottomRight()
        bbox = _normalize_bbox((tl.x, tl.y), (br.x, br.y))
        out.append({"text": token, **bbox})
    return out


def extract_text_with_vision(image: np.ndarray):
    """Run Apple Vision OCR on an RGB image.

    Returns (text, words_with_coords) matching the doctr backend's schema.
    Raises RuntimeError on Vision failure (caller catches and falls back to doctr).
    """
    import Vision
    cg_image = _np_image_to_cgimage(image)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    request.setRecognitionLanguages_(_system_recognition_languages())

    success, error = handler.performRequests_error_([request], None)
    if not success:
        msg = error.localizedDescription() if error is not None else "unknown error"
        raise RuntimeError(f"Vision performRequests failed: {msg}")

    observations = request.results() or []
    text_parts: list = []
    words_with_coords: list = []
    for obs in observations:
        candidates = obs.topCandidates_(1)
        if not candidates or len(candidates) == 0:
            continue
        line_text = str(candidates[0].string())
        if line_text:
            text_parts.append(line_text)
        words_with_coords.extend(_words_from_observation(obs))

    text = "\n".join(text_parts)
    return text, words_with_coords
```

- [ ] **Step 4: Run all unit tests in this file**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add openrelife/apple_vision_ocr.py tests/test_apple_vision_ocr.py
git commit -m "feat(ocr): implement extract_text_with_vision"
```

---

## Chunk 3: Wiring & Settings

This chunk wires the dispatcher to actually call Vision when the setting is on, threads the setting through the subprocess, and adds the settings persistence + Flask routes + modal UI.

### Task 9: Wire dispatcher in `ocr.py`

**Files:**
- Modify: `openrelife/ocr.py`
- Modify: `tests/test_ocr_dispatcher.py`

- [ ] **Step 1: Append failing dispatcher tests**

Append to `tests/test_ocr_dispatcher.py`:

```python
def test_dispatcher_calls_vision_when_enabled_and_available():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock(return_value=("hello", [{"text": "hello", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]))
    fake_doctr = MagicMock()
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr):
        import numpy as np
        text, words = ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=True,
        )
    fake_vision.assert_called_once()
    fake_doctr.assert_not_called()
    assert text == "hello"


def test_dispatcher_falls_back_to_doctr_on_vision_exception():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock(side_effect=RuntimeError("Vision boom"))
    fake_doctr = MagicMock(return_value=("fallback", []))
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr):
        import numpy as np
        text, words = ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=True,
        )
    fake_vision.assert_called_once()
    fake_doctr.assert_called_once()
    assert text == "fallback"


def test_dispatcher_uses_doctr_when_setting_disabled():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock()
    fake_doctr = MagicMock(return_value=("d", []))
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr):
        import numpy as np
        ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=False,
        )
    fake_vision.assert_not_called()
    fake_doctr.assert_called_once()


def test_dispatcher_uses_doctr_when_vision_unavailable():
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("openrelife.ocr")]:
        del sys.modules[mod]
    import openrelife.ocr as ocr_mod
    from unittest.mock import MagicMock, patch
    fake_vision = MagicMock()
    fake_doctr = MagicMock(return_value=("d", []))
    with patch.object(ocr_mod, "_extract_with_vision", fake_vision), \
         patch.object(ocr_mod, "_extract_with_doctr", fake_doctr), \
         patch("openrelife.apple_vision_ocr.is_apple_vision_available", return_value=False):
        import numpy as np
        ocr_mod.extract_text_from_image(
            np.zeros((10, 10, 3), dtype=np.uint8),
            use_apple_vision=True,
        )
    fake_vision.assert_not_called()
    fake_doctr.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_ocr_dispatcher.py -v
```

Expected: 2 passed (from Task 3) + 4 failed.

- [ ] **Step 3: Update `openrelife/ocr.py` to dispatch**

Replace the body of `extract_text_from_image` and add `_extract_with_vision`:

```python
import logging
from openrelife import apple_vision_ocr

_logger = logging.getLogger("openrelife.ocr")


def _extract_with_vision(image: np.ndarray) -> Tuple[str, List[Dict]]:
    """OCR via Apple Vision. Raises on failure (dispatcher catches)."""
    return apple_vision_ocr.extract_text_with_vision(image)


def extract_text_from_image(image: np.ndarray,
                            use_apple_vision: bool = False) -> Tuple[str, List[Dict]]:
    """Run OCR on an RGB image and return (text, words_with_coords).

    If `use_apple_vision` is True AND Apple Vision is available on this
    platform, try Vision first and fall back to doctr on any exception.
    Otherwise, use doctr directly.
    """
    if use_apple_vision and apple_vision_ocr.is_apple_vision_available():
        try:
            return _extract_with_vision(image)
        except Exception as e:
            _logger.warning("Vision OCR failed (%s), falling back to doctr", e)
    return _extract_with_doctr(image)
```

Make sure `import logging` and `from openrelife import apple_vision_ocr` are added to the top of `ocr.py`.

- [ ] **Step 4: Run dispatcher tests to verify they pass**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_ocr_dispatcher.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add openrelife/ocr.py tests/test_ocr_dispatcher.py
git commit -m "feat(ocr): wire dispatcher to Apple Vision with doctr fallback"
```

---

### Task 10: Update `_process_ocr_batch` and `ocr_worker_thread`

**Files:**
- Modify: `openrelife/screenshot.py:378-538`

- [ ] **Step 1: Update `_process_ocr_batch` signature**

In `openrelife/screenshot.py`, change the signature at line 378 from:

```python
def _process_ocr_batch(timestamps_list, num_threads=4):
```

to:

```python
def _process_ocr_batch(timestamps_list, num_threads=4, use_apple_vision=False):
```

And inside the loop body (around line 402), change:

```python
text, words_coords = extract_text_from_image(screenshot)
```

to:

```python
text, words_coords = extract_text_from_image(screenshot, use_apple_vision=use_apple_vision)
```

- [ ] **Step 2: Update `ocr_worker_thread` to pass the setting**

Around line 525, change:

```python
proc = Process(target=_process_ocr_batch, args=(batch, threads))
```

to:

```python
use_av = get_use_apple_vision()  # read once on the parent thread
proc = Process(target=_process_ocr_batch, args=(batch, threads, use_av))
```

- [ ] **Step 3: Update logging line at 523 to include the engine choice**

Change:

```python
_logger.info(f"OCR subprocess starting: {len(batch)} frames, {threads} threads (timeout={batch_timeout}s)")
```

to:

```python
engine = "vision" if use_av else "doctr"
_logger.info(f"OCR subprocess starting: {len(batch)} frames, {threads} threads, engine={engine} (timeout={batch_timeout}s)")
```

- [ ] **Step 4: Run existing tests to ensure no regression**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add openrelife/screenshot.py
git commit -m "feat(capture): thread use_apple_vision through OCR subprocess"
```

---

### Task 11: Settings persistence in `app.py:load_settings`

**Files:**
- Modify: `openrelife/app.py:34-52`

- [ ] **Step 1: Update imports at top of `app.py`**

Add imports near the existing `set_screenshot_interval` block (around line 19-27):

```python
from openrelife.screenshot import (
    # ... existing ...
    set_use_apple_vision,
    get_use_apple_vision,
)
from openrelife.apple_vision_ocr import is_apple_vision_available
```

- [ ] **Step 2: Update `load_settings()` to handle the new key**

Inside `load_settings()` (around line 41-51), add at the end of the if-block parsing the JSON:

```python
if 'use_apple_vision' in settings:
    set_use_apple_vision(bool(settings['use_apple_vision']))
elif is_apple_vision_available():
    # First-run default: enable on supported platforms
    set_use_apple_vision(True)
# else: leave default False (already set in screenshot.py)
```

- [ ] **Step 3: Manually verify load_settings does the right thing**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -c "
from openrelife.app import load_settings
load_settings()
from openrelife.screenshot import get_use_apple_vision
from openrelife.apple_vision_ocr import is_apple_vision_available
print(f'available={is_apple_vision_available()}, use={get_use_apple_vision()}')
"
```

Expected on this Mac (M-series, macOS 26.4):
- If no `use_apple_vision` key in `settings.json`: `available=True, use=True`
- If key is `false`: `available=True, use=False`

- [ ] **Step 4: Commit**

```bash
git add openrelife/app.py
git commit -m "feat(settings): default Apple Vision on for compatible platforms"
```

---

### Task 12: Add Flask routes `GET`/`POST /api/settings/apple_vision`

**Files:**
- Modify: `openrelife/app.py` (add routes near other `/api/settings/*` routes)

- [ ] **Step 1: Add the two routes**

After an existing settings route block (e.g. near the routes for `ocr_compute_mode` around `app.py:3461-3505`), add:

```python
@app.route("/api/settings/apple_vision", methods=["GET"])
def api_get_apple_vision_setting():
    return jsonify({
        "enabled": get_use_apple_vision(),
        "available": is_apple_vision_available(),
    })


@app.route("/api/settings/apple_vision", methods=["POST"])
def api_set_apple_vision_setting():
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data:
        return jsonify({"error": "missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    set_use_apple_vision(enabled)
    # Persist to settings.json (read-modify-write, same pattern as other routes)
    settings_path = os.path.join(appdata_folder, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                content = f.read().strip()
                if content:
                    settings = json.loads(content)
        except Exception:
            settings = {}
    settings["use_apple_vision"] = enabled
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=4)
    return jsonify({"enabled": enabled, "available": is_apple_vision_available()})
```

- [ ] **Step 2: Smoke-test via curl**

Start the dev server in another terminal, or use a one-shot script. Easiest: launch the app via `start.sh` (or run `python -m openrelife.app`) and:

```bash
curl -s http://127.0.0.1:8082/api/settings/apple_vision
curl -s -X POST -H "Content-Type: application/json" -d '{"enabled": false}' http://127.0.0.1:8082/api/settings/apple_vision
curl -s http://127.0.0.1:8082/api/settings/apple_vision
```

Expected: status reflects the toggle. Verify also `~/Library/Application Support/OpenReLife/settings.json` contains `"use_apple_vision": false`.

- [ ] **Step 3: Commit**

```bash
git add openrelife/app.py
git commit -m "feat(api): add /api/settings/apple_vision routes"
```

---

### Task 13: Update unified `POST /api/settings` handler

The modal's "Save Changes" button hits `POST /api/settings` (around `app.py:3621`). It must learn `use_apple_vision`.

**Files:**
- Modify: `openrelife/app.py:~3620-3640`

- [ ] **Step 1: Locate the unified handler**

```bash
grep -n "api_update_settings\|@app.route.*settings.*methods" openrelife/app.py | head -10
```

Identify the unified `POST /api/settings` handler and the surrounding block where it processes individual fields like `screenshot_interval`, `ocr_compute_mode`, etc.

- [ ] **Step 2: Add the new field handling**

Inside the unified handler, alongside the other field processors, add:

```python
if "use_apple_vision" in data:
    set_use_apple_vision(bool(data["use_apple_vision"]))
    settings["use_apple_vision"] = bool(data["use_apple_vision"])
```

(Adapt the variable names `data` / `settings` to match those used in the existing handler.)

- [ ] **Step 3: Smoke-test**

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"use_apple_vision": false}' \
  http://127.0.0.1:8082/api/settings
curl -s http://127.0.0.1:8082/api/settings/apple_vision
```

Expected: response shows `enabled: false`. settings.json updated.

- [ ] **Step 4: Commit**

```bash
git add openrelife/app.py
git commit -m "feat(api): handle use_apple_vision in unified settings endpoint"
```

---

### Task 14: Add modal UI section "OCR Engine"

**Files:**
- Modify: `openrelife/app.py` (HTML around lines 1195-1300, JS around the save handler)

- [ ] **Step 1: Add the HTML section in the modal body**

Locate the "OCR Processing Interval" block in the modal (search for `OCR Processing Interval`). After that block, add:

```html
<div id="ocrEngineSection" style="display: none; margin-top: 16px;">
  <label style="display:block; font-weight:600; margin-bottom:6px;">OCR Engine</label>
  <label style="display:flex; align-items:center; gap:8px;">
    <input type="checkbox" id="useAppleVisionCheckbox">
    <span>Use Apple Vision (recommended, ~30× faster)</span>
  </label>
  <p style="margin-top:6px; color:#666; font-size:12px;">
    Native macOS text recognition. Falls back to doctr automatically if a frame fails.
    Available only on Mac with Apple Silicon.
  </p>
</div>
```

- [ ] **Step 2: Add JS to populate and save the checkbox**

In the modal-open handler (search for `openSettings` or where the modal is shown — around `app.py:2188`), add a fetch to populate the checkbox:

```javascript
fetch('/api/settings/apple_vision')
  .then(r => r.json())
  .then(d => {
    const section = document.getElementById('ocrEngineSection');
    const cb = document.getElementById('useAppleVisionCheckbox');
    if (d.available) {
      section.style.display = '';
      cb.checked = !!d.enabled;
    } else {
      section.style.display = 'none';
    }
  })
  .catch(e => console.warn('apple_vision settings fetch failed', e));
```

In the save handler (search for the function called by the "Save Changes" button), include the new field in the request body:

```javascript
// Inside the existing POST body construction:
use_apple_vision: document.getElementById('useAppleVisionCheckbox').checked,
```

- [ ] **Step 3: Manual UI smoke test**

Restart the app, open the settings modal:
- The "OCR Engine" section appears
- The checkbox is checked (because Apple Vision is the new default on this Mac)
- Toggle off, click Save, reopen modal: still off
- Verify `~/Library/Application Support/OpenReLife/settings.json` contains `"use_apple_vision": false`
- Toggle back on, save, restart the app, reopen modal: still on (persistence verified across restart)

- [ ] **Step 4: Commit**

```bash
git add openrelife/app.py
git commit -m "feat(ui): add Apple Vision toggle to settings modal"
```

---

## Chunk 4: Integration tests + benchmark

This chunk adds the platform-gated integration tests and the manual benchmark tool. After this chunk, we have full functional verification on this Mac.

### Task 15: Create fixture generator and PNGs

**Files:**
- Create: `tests/fixtures/apple_vision/generate.py`
- Create: `tests/fixtures/apple_vision/italian_simple.png`
- Create: `tests/fixtures/apple_vision/english_simple.png`
- Create: `tests/fixtures/apple_vision/blank.png`

- [ ] **Step 1: Write the generator script**

Create `tests/fixtures/apple_vision/generate.py`:

```python
"""Reproducible PNG fixture generator for Apple Vision integration tests.

Run: python tests/fixtures/apple_vision/generate.py

Generates 3 PNGs in this directory. Re-run to regenerate identically.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent
W, H = 800, 600
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
FONT_SIZE = 48
TEXT_POS = (40, 40)


def render(text: str, out_name: str):
    img = Image.new("RGB", (W, H), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()
    if text:
        draw.text(TEXT_POS, text, fill="black", font=font)
    img.save(OUT_DIR / out_name, "PNG", optimize=True)


def main():
    render("Ciao mondo, questa e una prova", "italian_simple.png")
    render("Hello world, this is a test", "english_simple.png")
    render("", "blank.png")
    print(f"Generated 3 fixtures in {OUT_DIR}")


if __name__ == "__main__":
    main()
```

(Note: avoid the `è` accent in Italian fixture text to keep the file ASCII-stable across font rendering quirks; the Apple Vision recognition test still verifies "Ciao" and "mondo".)

- [ ] **Step 2: Run the generator**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" tests/fixtures/apple_vision/generate.py
```

Expected: 3 PNGs created, output `Generated 3 fixtures in ...`.

- [ ] **Step 3: Verify the PNGs**

```bash
ls -la tests/fixtures/apple_vision/
file tests/fixtures/apple_vision/*.png
```

Expected: 3 valid PNG files, each ~5-30 KB.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/apple_vision/
git commit -m "test: add Apple Vision OCR fixture generator and PNGs"
```

---

### Task 16: Write integration tests

**Files:**
- Create: `tests/test_apple_vision_ocr_integration.py`

- [ ] **Step 1: Write the integration test file**

```python
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
```

- [ ] **Step 2: Run the integration tests**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/test_apple_vision_ocr_integration.py -v
```

Expected on this Mac: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_apple_vision_ocr_integration.py
git commit -m "test: add Apple Vision OCR integration tests"
```

---

### Task 17: Adapt manual benchmark script

**Files:**
- Create: `tests/manual/benchmark_apple_vision.py`

- [ ] **Step 1: Create the benchmark script**

```python
"""Manual benchmark: side-by-side Vision vs doctr on real captured frames.

Run: python tests/manual/benchmark_apple_vision.py [N]
where N is the number of recent frames to test (default 3).

Prints per-phase timing and an output sample for visual quality comparison.
Not run by pytest. Kept in-tree as a regression-check tool.
"""
import os
import sys
import time

os.environ["TOKENIZERS_PARALLELISM"] = "false"


def main(n: int = 3):
    appdata = os.path.expanduser("~/Library/Application Support/OpenReLife")
    screens = os.path.join(appdata, "screenshots")
    files = sorted(
        [f for f in os.listdir(screens) if f.endswith(".webp")],
        key=lambda f: os.path.getmtime(os.path.join(screens, f)),
    )[-n:]
    print(f"Benchmarking {len(files)} frames", flush=True)

    from PIL import Image
    import numpy as np
    from openrelife import ocr, apple_vision_ocr

    for fname in files:
        img = Image.open(os.path.join(screens, fname)).convert("RGB")
        w, h = img.size
        if h > 1080:
            scale = 1080 / h
            img = img.resize((int(w * scale), 1080), Image.LANCZOS)
        arr = np.array(img)
        print(f"\n=== {fname} ({arr.shape}) ===")

        if apple_vision_ocr.is_apple_vision_available():
            t = time.perf_counter()
            v_text, v_words = apple_vision_ocr.extract_text_with_vision(arr)
            v_time = time.perf_counter() - t
            print(f"  Vision: {v_time:.2f}s, {len(v_words)} words, sample: {v_text[:80]!r}")
        else:
            print("  Vision: not available on this platform")

        t = time.perf_counter()
        d_text, d_words = ocr._extract_with_doctr(arr)
        d_time = time.perf_counter() - t
        print(f"  doctr:  {d_time:.2f}s, {len(d_words)} words, sample: {d_text[:80]!r}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main(n)
```

- [ ] **Step 2: Run it on real frames**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" tests/manual/benchmark_apple_vision.py 3
```

Expected: Vision time per frame < 1s, doctr time per frame several seconds. **This is the key empirical confirmation of the design's performance claim.**

- [ ] **Step 3: Commit**

```bash
git add tests/manual/benchmark_apple_vision.py
git commit -m "test: add manual benchmark for Apple Vision vs doctr"
```

---

## Chunk 5: Build verification + PR

### Task 18: Run full test suite

- [ ] **Step 1: Run all pytest**

```bash
"$HOME/Library/Application Support/OpenReLife/venv/bin/python" -m pytest tests/ -v
```

Expected: every test green (existing + 6 dispatcher + 10 vision unit + 3 integration + 3 settings = ~22 tests).

If anything is red, **stop, fix, do not proceed to build**.

---

### Task 19: Build the macOS app bundle (no installer)

- [ ] **Step 1: Build the .app**

```bash
cd electron-app
npm run pack
```

Expected: `electron-app/dist/mac-arm64/OpenReLife.app` is created. The `prebuild` step regenerates `uv.lock` (already up to date).

- [ ] **Step 2: Verify bundle contents include the new module**

```bash
find dist/mac-arm64/OpenReLife.app/Contents/Resources/openrelife -name "apple_vision_ocr.py"
```

Expected: prints the path. If empty, `extraResources` glob is wrong.

---

### Task 20: Smoke-test the bundled app

- [ ] **Step 1: Quit the currently-running OpenReLife** (the production-installed one). From the menu bar tray icon, choose "Quit", or:

```bash
pkill -f "Application Support/OpenReLife/venv/bin/python"
pkill -f "Applications/OpenReLife.app/Contents/MacOS/OpenReLife"
```

- [ ] **Step 2: Launch the freshly-built bundle**

```bash
open electron-app/dist/mac-arm64/OpenReLife.app
```

Expected: the app starts; the Python backend logs should appear in `~/Library/Application Support/OpenReLife/logs/backend.log`.

- [ ] **Step 3: Verify settings UI**

Open the settings modal. Check:
- "OCR Engine" section is present
- Checkbox is checked (default-on for this M-series Mac)
- Toggle off, save, reopen — still off
- Toggle back on, save, restart app, reopen — still on (persistence)

- [ ] **Step 4: Verify OCR speed via the live log**

In a separate terminal:

```bash
tail -f ~/Library/Application\ Support/OpenReLife/logs/backend.log | grep -E "OCR (subprocess|batch)"
```

Trigger some screen activity to enqueue OCR. Within ~1 minute of the next batch, watch:
```
INFO ... OCR subprocess starting: N frames, T threads, engine=vision (timeout=...)
INFO ... OCR subprocess done: N frames in <Xs> (<X/N>s/frame) ...
```

Expected: <1s/frame with engine=vision. Toggle off in settings, wait next batch, expect higher s/frame with engine=doctr.

- [ ] **Step 5: Confirm no regression in core flows**

- Search by text still works
- Per-word overlay (click a screenshot, see word highlights) still works
- Existing settings (interval, quality, compute mode) still save/load

If anything regressed, **stop, identify the cause, do not push**.

---

### Task 21: Push branch and open PR

- [ ] **Step 1: Push the worktree branch**

```bash
git push -u origin feat/apple-vision-ocr
```

(`origin` is the user's GitLab fork. The branch goes there.)

- [ ] **Step 2: Open PR on porech/openrelife from the fork**

The user's main remote is GitLab, but the upstream is GitHub `porech/openrelife`. Two flows:

**Flow A — open PR on the GitLab fork** (for personal review/CI):
```bash
glab mr create --target-branch main --title "feat(ocr): Apple Vision backend on Apple Silicon" \
  --description "Closes upstream issue porech/openrelife#4. See docs/superpowers/specs/2026-04-29-apple-vision-ocr-design.md."
```

**Flow B — push a topic branch to GitHub upstream and open PR there**:
```bash
git push prod feat/apple-vision-ocr
gh pr create -R porech/openrelife --base main --head feat/apple-vision-ocr \
  --title "feat(ocr): Apple Vision backend on Apple Silicon" \
  --body "$(cat <<'EOF'
Closes #4.

Replaces doctr CPU OCR with Apple Vision on Apple Silicon Macs (macOS 13+),
keeping doctr as automatic fallback. Settings toggle to opt out.

Performance (measured on a real frame): Vision <1s/frame vs doctr 8-30s/frame.

Design doc: docs/superpowers/specs/2026-04-29-apple-vision-ocr-design.md

## Test plan
- [x] Unit tests green (mocked detection + Y-flip + languages + dispatcher)
- [x] Integration tests green on M-series (Italian, English, blank fixtures)
- [x] Manual benchmark confirms Vision <1s/frame
- [x] Bundled .app smoke-tested locally: settings toggle works, persistence across restart, no regression in search/overlay
EOF
)"
```

The user must choose Flow A or B based on their contribution policy. **Confirm with the user before pushing to `prod`** (it's a public action).

- [ ] **Step 3: Final cleanup**

After PR is merged or while it's in review, the worktree can be removed via the `superpowers:using-git-worktrees` skill (`ExitWorktree` tool).

---

## Notes for the implementer

- **Frequent commits**: each Task above ends in a commit. Don't batch — keep the history as one logical step per commit.
- **Conventional commits, English, single-line, no signatures** (project CLAUDE.md). Examples already used in this plan.
- **TDD discipline**: write the failing test, see it fail, then implement, then see it pass, then commit. Don't skip the "see it fail" step — it's the cheapest sanity check that the test is actually testing something.
- **If a test is hard to write**: that's a signal the API is wrong. Stop and reconsider before forcing the test.
- **If a Vision call segfaults**: that crashes the subprocess but not the parent (already handled by `proc.is_alive()` check). If it crashes the test process, capture the stderr, stop, and investigate the `_np_image_to_cgimage` conversion (most likely culprit: pixel format / byte alignment).
- **Don't add unrequested features**: no telemetry, no metrics, no "while I'm here" refactors. Stay focused on the spec.
- **Build step (Task 19)** uses `npm run pack` (no installer), not `npm run build-mac` (full installer with codesign). Pack is fine for local smoke testing; final release packaging is out of scope here.
