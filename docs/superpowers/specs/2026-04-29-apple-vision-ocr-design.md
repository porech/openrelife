# Apple Vision OCR — Design Spec

**Status:** Draft (pending review)
**Date:** 2026-04-29
**Author:** Alex Florenti (with Claude)
**Scope:** Add Apple Vision (`VNRecognizeTextRequest`) as the preferred OCR backend on Apple Silicon Macs, replacing doctr in the hot path while keeping doctr as automatic fallback.

---

## 1. Background and motivation

### 1.1 Observed problem

Profiling a single OCR frame on the live system (macOS 26.4, Mac M-series, screen at 3283×2122 downscaled to 1670×1080) showed:

| Phase | Time | % of frame |
|---|---|---|
| Image load + resize | 0.1–0.2 s | ~1% |
| **doctr OCR** (`db_mobilenet_v3_large` + `crnn_mobilenet_v3_large` on PyTorch CPU) | **8.7–29.3 s** | **95–99%** |
| sentence-transformer embedding (`all-MiniLM-L6-v2`, 39–44 lines) | 0.2–0.5 s | ~2% |
| Cold-start subprocess (torch + doctr + ST imports) | ~9 s | amortized |

Time scales linearly with the number of recognized words (~37 ms/word). Dense screens (e.g. code editors with ~650 words detected) take ~30 s per frame.

### 1.2 Root cause

doctr runs on PyTorch CPU without using Metal/MPS or the Apple Neural Engine. Per-word recognition is sequential. On Apple Silicon, this leaves the Neural Engine — which is purpose-built for exactly this workload — idle.

### 1.3 Proposal

Add Apple Vision (`VNRecognizeTextRequest`) as a new OCR backend, default-on for new installs on Apple Silicon Macs running macOS ≥ 13. Apple Vision uses the Neural Engine, supports word-level bounding boxes, multilingual recognition, and is part of the OS (no model download).

Expected speedup: **30×–60×** per frame (target: 0.1–0.4 s vs current 8–30 s).

---

## 2. Goals and non-goals

### 2.1 Goals

- Reduce per-frame OCR time on Apple Silicon Macs by at least one order of magnitude.
- Preserve the existing public OCR API: `extract_text_from_image(np.ndarray) -> (str, List[Dict])` with words coords in `{text, x1, y1, x2, y2}` normalized 0–1, top-left origin.
- Keep doctr available as an automatic fallback, so the existing search-by-word UX never silently degrades on a single failed frame.
- Provide a settings toggle (visible only on supported platforms) for the user to opt out and revert to doctr.
- No new build artifacts (no Swift binaries, no codesigning extras).

### 2.2 Non-goals

- No support for Intel Macs, Windows, or Linux. The new backend is gated to Apple Silicon + macOS ≥ 13. Other platforms keep doctr unchanged.
- No replacement of `ai_ocr.py` AI providers (Gemini/OpenAI/Claude). Those remain a separate, post-OCR refinement layer.
- No changes to `screenshot_path` storage format, database schema, or `words_coords` consumer code (overlay UI, search).
- No removal of doctr — it stays as fallback and as the only backend on non-supported platforms.

---

## 3. Architecture

### 3.1 Module layout

```
openrelife/
├── ocr.py                      # Public API entry point, becomes a dispatcher
├── apple_vision_ocr.py         # NEW — PyObjC + Vision bridge (lazy imports)
├── screenshot.py               # +set_use_apple_vision / get_use_apple_vision
├── app.py                      # +settings persistence + UI toggle in modal
└── ...
```

Public API contract (`extract_text_from_image(image: np.ndarray) -> Tuple[str, List[Dict]]`) is unchanged. All callers (`screenshot.py:_process_ocr_batch`, `ai_ocr.py` providers, etc.) are agnostic of the backend.

### 3.2 Detection logic

`apple_vision_ocr.is_apple_vision_available()` returns `True` only when **all** of:

- `sys.platform == "darwin"`
- `platform.machine() == "arm64"`
- `platform.mac_ver()[0]` major ≥ 13
- `import Vision` and `import Quartz` (PyObjC frameworks) succeed

Result is `lru_cache`'d (one resolution per process). On non-supported platforms, the imports are never attempted; the function short-circuits before `import Vision`.

### 3.3 Setting

A new module-level boolean `_use_apple_vision` in `screenshot.py` with `set_use_apple_vision(bool)` / `get_use_apple_vision() -> bool` getters mirroring the existing pattern (`ocr_compute_mode`, `ocr_cooldown`, etc.).

Default value:
- On supported platforms (`is_apple_vision_available() == True`): `True`
- On all other platforms: `False`

The setting is initialized in `app.py:load_settings()` after reading `appdata_folder/settings.json`:

```python
if 'use_apple_vision' in settings:
    set_use_apple_vision(bool(settings['use_apple_vision']))
elif is_apple_vision_available():
    set_use_apple_vision(True)
# else: leave default False
```

This honors explicit `false` overrides while defaulting to `True` only when the key is absent and the platform supports it.

### 3.4 Subprocess parameter passing

`_process_ocr_batch(timestamps, threads)` becomes `_process_ocr_batch(timestamps, threads, use_apple_vision)`. The parent reads `get_use_apple_vision()` once when spawning the subprocess and passes it as an argument. No file reads from inside the subprocess (avoids race conditions with concurrent settings updates).

---

## 4. Data flow

### 4.1 Per-frame dispatch

```
ocr.extract_text_from_image(img)
  │
  ├─ if use_apple_vision and is_apple_vision_available():
  │    try:
  │       return apple_vision_ocr.extract_text_with_vision(img)
  │    except Exception as e:
  │       _logger.warning("Vision failed: %s, falling back to doctr", e)
  │       _vision_failure_count += 1
  │       # falls through
  │
  └─ # doctr branch (always available as fallback)
     return _extract_with_doctr(img)   # lazy-loads doctr the first time
```

### 4.2 Lazy doctr loading

`_extract_with_doctr` keeps a module-level `_doctr_predictor = None`. First call constructs `ocr_predictor(pretrained=True, det_arch="db_mobilenet_v3_large", reco_arch="crnn_mobilenet_v3_large")`. Subsequent calls reuse the cached predictor.

On a system where Vision works without errors, doctr is **never** instantiated → ~3.5 s saved per subprocess cold start, ~500 MB RAM saved.

### 4.3 Vision bridge internals (`extract_text_with_vision`)

1. **Image conversion**: `np.ndarray (H, W, 3) uint8 RGB` → `CGImage`. Use `Quartz.CGImageCreate(width, height, ...)` with a `CGDataProvider` over the raw bytes (RGB, 8 bits/component, 24 bits/pixel, byte order default, color space sRGB).
2. **Request handler**: `VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)`.
3. **Request configuration**:
   ```python
   request = Vision.VNRecognizeTextRequest.alloc().init()
   request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
   request.setUsesLanguageCorrection_(True)
   request.setRecognitionLanguages_(_system_recognition_languages())
   ```
4. **Execution**: `success, error = handler.performRequests_error_([request], None)`. On `success == False`, raise `RuntimeError(error.localizedDescription())`.
5. **Result extraction**: iterate `request.results()` → list of `VNRecognizedTextObservation`. For each:
   - `top = obs.topCandidates_(1)[0]` → `VNRecognizedText`
   - `full_string = top.string()`
   - Tokenize the string by whitespace; for each token compute its `NSRange` within `full_string`.
   - For each range, call `top.boundingBoxForRange_error_(range, None)` → `VNRectangleObservation`. Extract `topLeft`, `topRight`, `bottomLeft`, `bottomRight` (all in [0, 1] **bottom-left origin**).
   - Convert to top-left-origin `{x1, y1, x2, y2}` via `_normalize_bbox` helper (Y-flip).
6. **Return** `(text, words_with_coords)` matching doctr's output schema exactly.

### 4.4 Languages

`_system_recognition_languages()` returns `unique_preserve_order([NSLocale_preferred_lang(), "en-US"])`:

- Read `Foundation.NSLocale.preferredLanguages()[0]` (e.g. `it-IT` for Italian system).
- Always append `en-US` if not already present.
- Filter against `VNRecognizeTextRequest.supportedRecognitionLanguagesForTextRecognitionLevel_revision_error_(...)` to drop locales Vision does not support; if the system locale is unsupported, fall back to `["en-US"]` only.

---

## 5. Settings UI

### 5.1 Persistence (settings.json)

New key `use_apple_vision: bool`. Persisted via the existing read-modify-write pattern used by other settings in `app.py:3401-3640`.

### 5.2 API endpoints

Two new Flask routes in `app.py`:

- `GET /api/settings/apple_vision` → `{"enabled": bool, "available": bool}`. `available` exposes `is_apple_vision_available()` so the frontend can show/hide the UI.
- `POST /api/settings/apple_vision` body `{"enabled": bool}` → updates `_use_apple_vision` and persists to settings.json.

### 5.3 Modal placement

A new section "OCR Engine" in the settings modal (`app.py:1195-1300`), placed right after "OCR Processing Interval" for logical grouping. The section is **rendered only if `available === true`** — users on Intel Macs / Windows / Linux / macOS < 13 see no new UI.

```
┌─ OCR Engine ─────────────────────────────────────┐
│  ☑ Use Apple Vision (recommended, ~30× faster)  │
│                                                  │
│  Native macOS text recognition. Falls back to    │
│  doctr automatically if a frame fails.           │
│  Available only on Mac with Apple Silicon.       │
└──────────────────────────────────────────────────┘
```

Toggle change → POST to `/api/settings/apple_vision` → in-memory `_use_apple_vision` updated. The next subprocess spawn will read the new value. No app restart needed.

---

## 6. Error handling

| Layer | Failure mode | Behavior |
|---|---|---|
| `is_apple_vision_available()` | ImportError, wrong platform | Returns `False`; dispatcher uses doctr |
| Vision: `np→CGImage` | Corrupt buffer, invalid dims | `raise ValueError`; dispatcher logs warning + falls back to doctr for the frame |
| Vision: `performRequests_error_` | NSError (OOM, invalid request) | Converted to `RuntimeError`; dispatcher fallback |
| Vision: `boundingBoxForRange_error_` per word | Vision can't return bbox for a single word | Skip **that word** (debug log), keep the rest. Not fatal for the frame |
| Subprocess crash (PyObjC segfault) | OS kills the worker | `proc.is_alive() == False` after `join(timeout)`, already handled in `screenshot.py:528-532`. Batch retried on next restart via `get_pending_ocr_timestamps()` orphan recovery |

The setting `use_apple_vision` is **never auto-disabled** by code. If Vision fails 50× in a row, every frame falls back to doctr (verbose logs but functionally correct). The user retains explicit control.

### 6.1 Edge cases

- **Empty / blank frame**: Vision returns 0 observations → `(text="", words_coords=[])`, embedding zeros downstream. Coherent with doctr.
- **Emoji-only / icon-only screens**: same as blank frame.
- **Unsupported system locale**: filtered to `["en-US"]` by `_system_recognition_languages()`.
- **Very large image**: input is already capped at 1080p height by `_process_ocr_batch:396-398`. Vision's documented 8192×8192 limit is far above that.
- **Empty `words_coords` but non-empty `text`**: theoretically rare; return `(text, [])`. Search-by-word degrades but text indexing/embedding still work.

---

## 7. Performance budget (expected, to be confirmed in benchmark)

| Metric | Current (doctr CPU) | Expected (Vision Neural Engine) |
|---|---|---|
| Per-frame OCR | 8.7–29.3 s | 0.1–0.4 s (target) |
| Subprocess cold start | ~9 s (torch + doctr + ST) | ~5 s (only ST + PyObjC, doctr lazy) |
| Peak RAM per subprocess | ~1.2 GB | ~400 MB |
| Peak CPU per 20-frame batch | ~600% for ~167 s | ~150% (single core) for ~6–10 s |

---

## 8. Testing strategy

### 8.1 Unit tests (`tests/test_apple_vision_ocr.py`)

Always-run (mocked):
- `test_is_apple_vision_available_returns_false_on_intel` — mock `platform.machine() == "x86_64"`
- `test_is_apple_vision_available_returns_false_on_old_macos` — mock `platform.mac_ver()` to `("11.0", ...)`
- `test_normalize_bbox_flips_y_axis` — pure function: input `(x1=0.1, y1=0.9, x2=0.2, y2=0.8)` (Vision bottom-left origin) → output `(x1=0.1, y1=0.1, x2=0.2, y2=0.2)` (top-left origin)
- `test_system_recognition_languages_appends_en_us` — mock `NSLocale.preferredLanguages` to `["it-IT"]` → expect `["it-IT", "en-US"]`
- `test_system_recognition_languages_dedupes` — mock to `["en-US"]` → expect `["en-US"]`

Skip on non-arm64-darwin (`pytest.mark.skipif`):
- `test_is_apple_vision_available_returns_true_on_mseries` — runs only on supported hardware

### 8.2 Integration tests (`tests/test_apple_vision_ocr_integration.py`, skipif non-arm64-darwin)

Three PNG fixtures in `tests/fixtures/apple_vision/`:
- `italian_simple.png`: rendered "Ciao mondo, questa è una prova"
- `english_simple.png`: rendered "Hello world, this is a test"
- `blank.png`: 800×600 white background

For each non-blank fixture, assert:
- `text` contains the expected words (case-insensitive, whitespace-normalized)
- `len(words_coords) > 0`
- All `x1, y1, x2, y2` ∈ [0, 1]
- Y is top-left origin: the visually-top word has `y1 < 0.5`

For blank fixture:
- `text == ""`
- `words_coords == []`

### 8.3 Dispatcher tests (`tests/test_ocr_dispatcher.py`)

- `test_dispatcher_calls_vision_when_enabled_and_available` — mock both backends, assert Vision called
- `test_dispatcher_falls_back_to_doctr_on_vision_exception` — Vision mock raises, assert doctr called
- `test_dispatcher_uses_doctr_when_setting_disabled` — `set_use_apple_vision(False)`, assert doctr called regardless of availability
- `test_dispatcher_uses_doctr_when_unavailable` — mock `is_apple_vision_available() == False`, assert doctr called
- `test_is_apple_vision_available_is_cached` — call twice, assert detection logic ran once

### 8.4 Manual benchmark script (`tests/manual/benchmark_apple_vision.py`)

Adapted from the throwaway profiling script used during root-cause investigation. Picks N recent frames from `screenshots/`, runs them through both backends, prints per-phase timing and a sample of the OCR output for visual quality comparison. Kept in-tree as a regression-check tool (not run by pytest).

---

## 9. Build and release procedure

### 9.1 Dependencies

Update `pyproject.toml`:

```toml
[project.optional-dependencies]
macos = [
    "pyobjc==10.3",
    "pyobjc-framework-Vision==10.3",
    "pyobjc-framework-Quartz==10.3",
]
```

`electron-app/package.json` already runs `cd .. && uv lock` as `prebuild-mac`, so the lockfile is regenerated at build time.

### 9.2 Pre-publish verification (mandatory)

Performed in a git worktree on a feature branch (e.g. `feat/apple-vision-ocr`):

1. All unit + integration tests pass: `pytest tests/`
2. Manual benchmark script confirms target speedup on real frames: `python tests/manual/benchmark_apple_vision.py`
3. Build the macOS app without producing an installer: `cd electron-app && npm run pack`
4. Launch the unsigned `.app` bundle directly: `open dist/mac-arm64/OpenReLife.app` (or run the binary)
5. Smoke test in the running app:
   - App starts without crash
   - Settings modal shows the new "OCR Engine" section with the checkbox checked
   - Take a screenshot, verify it gets OCR'd in under 1 s (vs ~30 s currently)
   - Toggle the checkbox off, take another screenshot, verify doctr is used (slower)
   - Toggle back on; confirm no other settings/flows regressed

Only after all five steps pass: push the branch and open a PR.

### 9.3 Worktree

Implementation lives in a worktree separate from the current workspace, created via `superpowers:using-git-worktrees` skill. Each milestone (tests green, build green, smoke test passed) is a separate commit with conventional-commit message, English, single-line, no signatures (per project CLAUDE.md).

---

## 10. Open questions / future work

- **Thread safety of `VNImageRequestHandler`**: documentation suggests one handler per request is fine; we create a fresh handler per frame inside the subprocess, so no shared state.
- **Possible future optimization**: batch multiple frames into one `performRequests_error_` call. Vision supports it via multiple requests on the same handler. Out of scope for this spec — current per-frame loop is already adequate at the target speed.
- **Possible future optimization**: drop doctr entirely on Apple Silicon Macs after a stabilization period, removing the fallback branch. Out of scope — fallback A is the chosen path.
- **Telemetry**: not adding any. Logs already record `vision failure → fallback`, which is sufficient diagnostic.
