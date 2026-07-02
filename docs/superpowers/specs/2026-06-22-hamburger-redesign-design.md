# Hamburger menu redesign — implementation spec

Approved design: `Riprogettazione menu hamburger` handoff (HiFi prototype + README).
Target: `openrelife/app.py` (inline HTML/CSS/JS in `timeline_v2()`).

## Goal
Eliminate the left hamburger drawer (a grab-bag of app controls + OCR settings + extracted text) and redistribute its functions onto transient surfaces that never permanently cover the screenshot.

## Surfaces

1. **Search bar (existing) → command palette.** `⌘K`/`Ctrl+K` focuses the search bar and opens its panel. Add an **Actions** section to the panel (below history results) with the global commands the hamburger's "App" section held: Show text of this screen, Extract with AI, Settings, Hide window, Quit. Existing alpha22 search behavior (results, pagination, keyboard nav, snippet highlight) is preserved untouched — Actions is additive and shows when the query is empty.

2. **Action cluster (new), top-right.** Two buttons: `Testo` (`bi-body-text`) and `AI` (`bi-stars`, blue gradient, glow). Both open the text dialog; `AI` opens it in AI mode and triggers Run AI.

3. **Text dialog (new), centered, transient.** Header (title "Testo della schermata" + timestamp badge + close ✕). Toolbar: Base⇄AI switch (reuses `toggleOCRMode`/`currentOCRMode`), `Run AI Text` (reuses `runAIOCR`, label→"Rielabora" after first run), `Copy all` (reuses `copyExtractedText`, "✓ Copiato" feedback). AI banner when an AI transcription exists. Body: the current frame's OCR text (the relocated `#extractedText`, fed by `updateExtractedText`).

4. **Timeline `⋮` menu (existing) — extend.** Add "Vista calendario" (calls existing `toggleCalendar`) above the existing Settings and Delete items. Keep the standalone 🗓 calendar button.

## Removed
- `.sidebar-toggle` + `.sidebar` markup and CSS; `toggleSidebar()`; sidebar branch in `onResetUI`. The `☰` button removal also fixes its collision with the window traffic-lights.
- App-lifecycle items (Hide/Quit) are already in the system tray + native macOS menu; in-UI they now live only in the palette Actions.

## Reuse (no behavior change)
`runAIOCR`, `toggleOCRMode` + `currentOCRMode`, `copyExtractedText`, `updateExtractedText` + `#extractedText`, `hideAppWindow`, `quitAppFromMenu`, `openSettings`, `toggleCalendar`, `renderOverlay`.

## Behavior
- `Esc` closes palette / dialog / `⋮` menu → idle (full screenshot). Scrim click closes palette/dialog.
- One overlay open at a time.

## Deferred — phase 2
**"Evidenzia sullo screenshot" footer toggle** (in-situ OCR highlight). The handoff explicitly allows shipping the dialog without it and adding later. The app already has `renderOverlay()` over `words_coords`, so a follow-up can wire the toggle to that overlay with the design's highlight styling. Not in this pass — keeps the delivery correct and testable.

## Style
Match the handoff tokens (dark, `backdrop-filter` glass, accent `#0d6efd`, gradient `#2b86ff→#0d6efd`) and the just-redesigned settings-modal aesthetic. Bootstrap Icons `bi bi-*`, no emoji.

## Test
Build + reinstall; live-verify each surface in the running app (open dialog from Testo/AI, Base/AI switch, Run AI, Copy, ⌘K palette + Actions, ⋮ Calendar view, Esc/scrim close, hamburger gone). Screenshot the key states.
