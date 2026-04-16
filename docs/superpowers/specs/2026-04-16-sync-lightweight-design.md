# Lightweight Sync Design

## Problem

When the app returns to foreground after hours, the sync fetches thousands of entries with text data (~284KB for 4 hours), causing ~30s delay before new screenshots appear.

## Solution

Sync returns only timestamps + cursor. All entry data is fetched on-demand when the user navigates to a specific frame.

## Changes

### Backend: `/api/sync`

**Before:** returns `{timestamps, entries: {ts: {id, text, timestamp, ai_text}}, sync_cursor}`
**After:** returns `{timestamps, sync_cursor}`

Remove `get_entries_updated_since` and `SyncEntry` from database.py — no longer needed. The sync endpoint queries only for new timestamps where `updated_at > since`.

### Frontend: `syncData()`

**Before:** adds timestamps + populates `entriesData` from sync response
**After:** adds timestamps only, updates slider, shows sync indicator

### Frontend: `updateDisplay(timestamp)`

**Before:** two branches — `if (entriesData[ts])` uses cached data, `else` fetches on-demand
**After:** single flow — always fetch via `/api/entry/<ts>` if not in cache, cache after first fetch

### Frontend: sync indicator

When sync detects new timestamps, show a small non-blocking indicator (e.g. "Syncing 430 new screenshots...") that disappears when caught up.

### Cleanup

- Remove `ensureCoords` / `ensureDetails` functions — redundant with unified `updateDisplay`
- Remove `get_entries_updated_since()` and `SyncEntry` from database.py
- Keep `updated_at` column and index — still needed for the timestamp-only sync query

### Not changing

- `/api/entry/<ts>` — already returns full entry data
- `/api/search` — independent, queries DB directly
- Initial page load — keeps `get_timestamps()` + limited entries
- `prefetchNeighbors` — continues to work, caches nearby frames
- OCR backend — no changes
