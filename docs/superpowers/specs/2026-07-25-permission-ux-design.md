# Permission UX — design

## Problem

macOS builds are now signed with a self-signed certificate (`porech`), which fixes
the "app is damaged" install dead-end and makes TCC permissions persist across
signed→signed updates. But the app's handling of *missing* permissions is a bad
experience, independent of signing:

- The capture loop (`openrelife/screenshot.py`) calls `screencapture` every
  ~3 s with **no permission preflight**. When Screen Recording is not granted,
  every call re-triggers the macOS prompt → an **infinite loop of permission
  prompts**.
- There is **no visible UI state** telling the user a permission is missing or
  how to fix it.
- On the unsigned→signed transition, an existing user's old Screen Recording
  grant (bound to the unsigned identity) does not match the new `porech`
  identity, so the app sees "not granted" even though OpenReLife still appears
  enabled in System Settings. The user has no way to know they must remove and
  re-add it.
- The window opens as a resizable 900×600 window instead of fullscreen when the
  permission is missing (fullscreen is gated on `hasScreenAccess`).

This must be fixed before shipping the signing change.

## Scope

Two macOS permissions are surfaced:

- **Screen Recording** — *critical*; without it `screencapture` fails and nothing
  is captured.
- **Accessibility** — *enhancing*; used by `utils.py` to read the active window's
  AXTitle for richer context/search. Missing it degrades results but does not
  stop capture.

Input Monitoring is **not** needed: the Cmd+Shift+Space hotkey uses Electron
`globalShortcut`, which does not require it.

## Design

### 1. Permission detection (backend, no prompt)

Add two helpers (in `openrelife/utils.py`) that return a bool **without**
prompting:

- `has_screen_permission()` → `Quartz.CGPreflightScreenCaptureAccess()`
- `has_accessibility_permission()` → `AXIsProcessTrusted()`

`pyobjc-framework-Quartz` is already a macOS dependency.

### 2. Prompt-spam fix (core)

In the capture loop (`screenshot.py`, `while True` at ~line 253), before calling
`take_screenshots()` on macOS: if `has_screen_permission()` is `False`, **skip
the cycle** — do not invoke `screencapture` at all — then wait and retry. Log the
"waiting for Screen Recording permission" state **once** per transition, not every
cycle. This alone stops the prompt spam and the error-log flood.

The single explicit permission *request* stays where it is today: Electron's
`desktopCapturer.getSources({types:['screen']})` in `main.js`, fired once at
startup. The backend never requests — it only preflights.

### 3. API endpoints (existing `/api/*` pattern)

- `GET /api/permissions` → `{"screen": bool, "accessibility": bool}`
- `POST /api/open-settings` with `{"which": "screen" | "accessibility"}` →
  runs `open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"`
  (or `?Privacy_Accessibility`). Returns 400 on an unknown `which`.

### 4. Permission chips (top-right, always visible while missing)

The web UI polls `GET /api/permissions` every ~4 s. For **each missing**
permission it renders a **red chip** anchored top-right of the overlay (icon +
short label, e.g. "⚠ Screen Recording"). When a permission is granted its chip
disappears; when all are granted, nothing is shown.

Clicking a chip opens a **modal** with:
- what is missing and what it is for (one line);
- a fixed note: *"If OpenReLife is already listed and enabled here, remove it (–)
  and add it again — after an update macOS may require this."*;
- a primary button **"Open Settings ›"** → `POST /api/open-settings`.

The chips are styled to match the existing UI; the modal reuses the existing
Settings-modal styling.

### 5. Fullscreen decoupled from permissions (`main.js`)

Remove the `hasScreenAccess` gate on fullscreen: the window always goes
`setSimpleFullScreen(true)` at startup regardless of permission state. Remove the
900×600 windowed fallback and the `setInterval` that polled the permission only
to switch to fullscreen. The one-time screen-permission request at startup stays.

## Testing (manual — TCC is not unit-testable)

1. `tccutil reset ScreenCapture com.openrelife.app`, relaunch. Expect: app opens
   **fullscreen**; a **red Screen Recording chip** top-right; **no prompt spam**
   and no repeated `screencapture failed` lines in the logs (the loop skips
   capture); clicking the chip opens the modal; "Open Settings" opens the Screen
   Recording pane.
2. Grant Screen Recording → chip disappears, capture resumes (frames in
   `capture.log`).
3. `tccutil reset Accessibility com.openrelife.app` → red Accessibility chip
   appears; capture still works; granting removes the chip.

## Out of scope

- Auto-updater (electron-updater/Squirrel) — separate follow-up (issue #13's
  second half).
- Detecting the "already-enabled-but-stale-identity" case programmatically — not
  possible via public API; handled by always showing the remove-&-re-add note.
