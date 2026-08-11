# Permission UX Implementation Plan

> **For agentic workers:** implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the infinite Screen-Recording prompt loop and give clear, always-visible UI for missing macOS permissions, while opening the app fullscreen regardless of permission state.

**Architecture:** Backend-driven. The Flask backend detects permissions via pyobjc (no prompt) and exposes them over `/api/*`; the inline web UI polls and renders red chips top-right with a modal that deep-links to System Settings; the capture loop preflights before calling `screencapture` so it never spams prompts; `main.js` always goes fullscreen.

**Tech Stack:** Python/Flask (`openrelife/`), pyobjc (Quartz + ApplicationServices), Electron (`electron-app/main.js`), inline HTML/JS in `openrelife/app.py`.

## Global Constraints

- All code, comments, docs in English.
- Follow existing patterns (`/api/settings/*` endpoints; inline HTML/JS in `app.py`).
- macOS-only behavior must be guarded (`sys.platform == "darwin"`); non-mac returns "granted"/no-op.
- Keep `hardenedRuntime: false`; no new entitlements.
- Verification is manual (TCC is not unit-testable); each task ends with an explicit check.

---

### Task 1: Backend permission helpers + API

**Files:**
- Modify: `openrelife/utils.py` (add helpers)
- Modify: `openrelife/app.py` (add two routes near the other `/api/*` routes)

**Interfaces produced:**
- `utils.has_screen_permission() -> bool`
- `utils.has_accessibility_permission() -> bool`
- `GET /api/permissions -> {"screen": bool, "accessibility": bool}`
- `POST /api/open-settings {"which": "screen"|"accessibility"} -> 200 | 400`

- [ ] **Step 1:** In `utils.py`, add (guarded for darwin; non-mac → `True`):

```python
def has_screen_permission() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True  # never block capture on a detection error

def has_accessibility_permission() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True
```

- [ ] **Step 2:** In `app.py`, add routes:

```python
@app.route("/api/permissions", methods=["GET"])
def api_permissions():
    return jsonify({
        "screen": utils.has_screen_permission(),
        "accessibility": utils.has_accessibility_permission(),
    })

_SETTINGS_PANES = {
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}

@app.route("/api/open-settings", methods=["POST"])
def api_open_settings():
    which = (request.get_json(silent=True) or {}).get("which")
    url = _SETTINGS_PANES.get(which)
    if not url:
        return jsonify({"error": "unknown pane"}), 400
    subprocess.run(["open", url], check=False)
    return jsonify({"ok": True})
```

- [ ] **Step 3: Verify.** With app running: `curl -s localhost:8082/api/permissions` returns both booleans; `curl -s -XPOST localhost:8082/api/open-settings -H 'content-type: application/json' -d '{"which":"screen"}'` opens the Screen Recording pane. Bad `which` → HTTP 400.

- [ ] **Step 4: Commit** `feat(permissions): backend permission detection + /api/permissions and /api/open-settings`

---

### Task 2: Capture-loop preflight (stop prompt spam)

**Files:**
- Modify: `openrelife/screenshot.py` (capture loop ~line 253)

**Interfaces consumed:** `utils.has_screen_permission()`

- [ ] **Step 1:** At the top of each capture iteration, before `take_screenshots()`, skip when the permission is missing so `screencapture` is never invoked:

```python
if sys.platform == "darwin" and not utils.has_screen_permission():
    if not _screen_perm_warned:
        _logger.warning("Screen Recording permission missing — pausing capture until granted")
        _screen_perm_warned = True
    time.sleep(2)
    continue
if _screen_perm_warned and utils.has_screen_permission():
    _logger.info("Screen Recording permission granted — resuming capture")
    _screen_perm_warned = False
```

Add module-level `_screen_perm_warned = False`.

- [ ] **Step 2: Verify.** `tccutil reset ScreenCapture com.openrelife.app`; relaunch. `capture.log` shows the single "pausing capture" line and **no** repeated `screencapture failed` errors; no repeating OS prompt. Grant → "resuming capture" appears and frames are captured.

- [ ] **Step 3: Commit** `fix(capture): preflight Screen Recording before screencapture to stop the prompt loop`

---

### Task 3: Permission chips + modal (web UI)

**Files:**
- Modify: `openrelife/app.py` (inline HTML/CSS/JS of the main overlay template — the `/` / `/timeline-v2` view)

**Interfaces consumed:** `GET /api/permissions`, `POST /api/open-settings`

- [ ] **Step 1:** Add a fixed-position container top-right of the overlay: `#perm-chips` (CSS: `position:fixed; top; right; z-index above content; flex column; gap`). Each chip is a red pill (icon + label), styled to match existing UI tokens.

- [ ] **Step 2:** Add JS that polls `GET /api/permissions` every 4 s and reconciles chips: render a chip for each `false` permission (`screen` → "Screen Recording", `accessibility` → "Accessibility"); remove chips that became `true`. Debounce so nothing flickers.

- [ ] **Step 3:** Clicking a chip opens a modal (reuse the existing Settings-modal markup/classes) containing: title, one-line purpose, the fixed note *"If OpenReLife is already listed and enabled here, remove it (–) and add it again — after an update macOS may require this."*, and an "Open Settings ›" button that does `fetch('/api/open-settings', {method:'POST', headers, body: JSON.stringify({which})})`.

- [ ] **Step 4: Verify.** With Screen Recording revoked, a red "Screen Recording" chip is visible top-right; clicking opens the modal; "Open Settings" opens the pane; after granting, the chip disappears within ~4 s. Same for Accessibility (`tccutil reset Accessibility com.openrelife.app`).

- [ ] **Step 5: Commit** `feat(ui): red permission chips top-right with Open-Settings modal`

---

### Task 4: Fullscreen regardless of permission (`main.js`)

**Files:**
- Modify: `electron-app/main.js` (window creation ~line 94-140; `showWindow`/`ready-to-show`/`loadApp` fullscreen gates)

- [ ] **Step 1:** Create the window with `simpleFullscreen: true` unconditionally (not `hasAccess`). In `ready-to-show`, `loadApp`, and `showWindow`, call `setSimpleFullScreen(true)` without the `hasScreenAccess()` guard. Remove the 900×600 windowed fallback in `showWindow` and the `setInterval` block (lines ~121-139) that polled the permission only to switch to fullscreen. Keep the single `desktopCapturer.getSources({types:['screen']})` startup request.

- [ ] **Step 2: Verify.** With Screen Recording revoked, launching the app opens it **fullscreen** (not a 900×600 window), with the red chip visible.

- [ ] **Step 3: Commit** `fix(ui): always open fullscreen regardless of Screen Recording permission`

---

## Self-Review

- **Spec coverage:** detection (T1), prompt-spam fix (T2), chips+modal+remove/re-add note (T3), fullscreen decoupling (T4), endpoints (T1). All spec sections covered.
- **Placeholders:** none — code shown for backend/API/capture/main.js; UI task specifies exact behavior + strings against the existing template (finalized to the real markup during implementation).
- **Type consistency:** `has_screen_permission`/`has_accessibility_permission` names consistent across T1→T2; `/api/permissions` shape (`screen`, `accessibility`) consistent T1→T3; `which` values (`screen`,`accessibility`) consistent T1→T3.
