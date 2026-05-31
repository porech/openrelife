"""In-memory embedding index for sub-second search (issue #11).

The previous search re-read ~230MB of embeddings from SQLite on every query (~12s);
the matmul itself is ~80ms. This module keeps embeddings RESIDENT in RAM as an
L2-normalized float32 matrix and serves each query with a single matmul, so search
drops from ~18s to well under 1s after a one-time background warm-up.

Design (see the #11 design workflow):
- An immutable _Snapshot (matrix + parallel id/timestamp/app/title arrays) published
  via an atomic module-global rebind. Readers grab the current snapshot once and
  operate on it lock-free; numpy releases the GIL during BLAS so 16 waitress threads
  truly parallelize.
- A SINGLE background thread owns all mutation: it loads the matrix once at startup
  (off the request path) then polls every 2s for deltas (new captures, OCR-filled
  embeddings written by the OCR *subprocess*, deletes). Appends go into reserved
  headroom (zero-copy publish); overwrites/deletes copy-then-swap (never mutate a
  published live row → no torn reads).
- Until the matrix AND the FTS index are ready, callers fall back to the verbatim DB
  scan, so behaviour is never worse than before.

Concurrency with the OCR subprocess: it writes embeddings to the shared WAL DB and
bumps updated_at; the poller reads deltas by an updated_at high-watermark with a
trailing re-scan window (tolerates non-monotonic/cross-process clocks) plus a periodic
id-set reconcile. Deletes don't bump updated_at, so delete_entries calls notify_delete.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

EMB_DIM = 384
POLL_INTERVAL_S = 2.0
TRAILING_US = 5_000_000          # re-scan updated_at within this window each poll (clock-skew tolerance)
RECONCILE_EVERY = 30             # full id-set reconcile every N polls (~60s)
DEFAULT_MAX_RESIDENT_ROWS = 600_000
HEADROOM = 1.25                  # over-allocate the matrix so steady-state appends don't realloc


@dataclass(frozen=True)
class _Snapshot:
    mat: np.ndarray              # float32[cap, EMB_DIM], rows [0:n_rows] L2-normalized
    ids: np.ndarray              # int64[cap]
    ts: np.ndarray               # int64[cap]
    apps: np.ndarray             # object[cap]
    titles: np.ndarray           # object[cap]
    n_rows: int
    id_to_row: dict              # id -> row index (live region only)
    ts_to_row: dict              # timestamp -> row index
    hi_updated: int              # updated_at high-watermark covered by this snapshot
    count_seen: int              # COUNT(*) of entries at last sync (delete backstop)


_current: _Snapshot | None = None
_matrix_ready = threading.Event()
_fts_ready = threading.Event()
_writer_lock = threading.Lock()  # guards _del_pending and writer bookkeeping only
_del_pending: set = set()
_started = False
_max_rows = DEFAULT_MAX_RESIDENT_ROWS


def ready() -> bool:
    """True only when BOTH the matrix and the FTS keyword index are warm."""
    return _matrix_ready.is_set() and _fts_ready.is_set()


def current() -> _Snapshot | None:
    return _current  # atomic global read


def notify_delete(timestamps):
    """Record timestamps deleted from the DB so the poller drops them from the matrix
    (deletes don't bump updated_at, so the watermark can't see them)."""
    with _writer_lock:
        _del_pending.update(int(t) for t in timestamps)


def start(max_resident_rows: int = DEFAULT_MAX_RESIDENT_ROWS):
    """Launch the background loader+poller once. Non-blocking; serve() is never held."""
    global _started, _max_rows
    if _started:
        return
    _started = True
    _max_rows = max_resident_rows
    threading.Thread(target=_loader, name="embedding-index", daemon=True).start()


# --------------------------------------------------------------------------- query

def query(query_embedding, query_text, min_similarity, relevance_margin,
          dedupe_window_us, since, until, app, keyword_strengths):
    """Return (ranked_ids, total) from the resident matrix. Mirrors the DB-scan
    candidate set exactly (semantic floor + keyword bypass) then defers to the shared
    ranker, so results match the reference path within float tolerance.

    keyword_strengths: {id: strength>0} for the query's whole-word keyword matches
    (computed by database.fts_keyword_strengths)."""
    from openrelife.database import _rank_candidates  # local import avoids a cycle

    snap = _current
    if snap is None or snap.n_rows == 0:
        return [], 0
    qn = float(np.linalg.norm(query_embedding))
    if qn == 0:
        return [], 0
    n = snap.n_rows
    qhat = (np.asarray(query_embedding, dtype=np.float32) / qn)
    sims = snap.mat[:n] @ qhat            # cosine (rows are pre-normalized)
    ids = snap.ids[:n]
    ts = snap.ts[:n]

    valid = np.ones(n, dtype=bool)
    if since is not None:
        valid &= ts >= since
    if until is not None:
        valid &= ts <= until
    if app:
        valid &= (snap.apps[:n] == app)

    # Keyword candidate rows (within the filter window). Few — look up by id.
    kw_rows = []
    if keyword_strengths:
        for rid in keyword_strengths:
            r = snap.id_to_row.get(int(rid))
            if r is not None and r < n and valid[r]:
                kw_rows.append(r)

    floor_mask = valid & (sims >= min_similarity)
    has_floor = bool(floor_mask.any())
    if not has_floor and not kw_rows:
        return [], 0

    # top_semantic = max similarity among candidates (floor-passing ∪ keyword),
    # exactly as the DB-scan path computes it.
    tops = []
    if has_floor:
        tops.append(float(sims[floor_mask].max()))
    if kw_rows:
        tops.append(float(max(sims[r] for r in kw_rows)))
    top_semantic = max(tops)
    cutoff = top_semantic - relevance_margin
    # Non-keyword kept threshold = DB-scan's (floor AND cutoff) = max(min_similarity, cutoff).
    eff = max(min_similarity, cutoff)

    kept_rows = set(np.nonzero(valid & (sims >= eff))[0].tolist())
    kept_rows.update(kw_rows)

    apps = snap.apps
    titles = snap.titles
    candidates = []
    for r in kept_rows:
        rid = int(ids[r])
        kw = keyword_strengths.get(rid, 0.0) if keyword_strengths else 0.0
        candidates.append((kw > 0.0, kw, float(sims[r]), int(ts[r]), rid, apps[r], titles[r]))
    return _rank_candidates(candidates, relevance_margin, dedupe_window_us)


# ----------------------------------------------------------------------- internals

def _parse_embedding(blob):
    """float32[EMB_DIM] from a blob, or None if missing/wrong-size/zero-norm.
    Returns a COPY (frombuffer is read-only — never normalize it in place)."""
    if not blob or len(blob) != EMB_DIM * 4:
        return None
    e = np.frombuffer(blob, dtype=np.float32)
    nrm = float(np.linalg.norm(e))
    if nrm == 0.0 or not math.isfinite(nrm):
        return None
    return (e / nrm).astype(np.float32)   # normalized copy


def _loader():
    try:
        _initial_load()
        _matrix_ready.set()
    except Exception as e:
        print(f"Embedding index: initial load failed, staying on DB-scan fallback: {e}")
        return
    try:
        from openrelife.database import fts_backfill_if_needed
        if fts_backfill_if_needed():
            _fts_ready.set()
    except Exception as e:
        print(f"Embedding index: FTS backfill failed: {e}")
    tick = 0
    while True:
        time.sleep(POLL_INTERVAL_S)
        tick += 1
        try:
            _poll_once(reconcile=(tick % RECONCILE_EVERY == 0))
        except Exception as e:
            print(f"Embedding index poll error (continuing): {e}")


def _initial_load():
    from openrelife.database import _connect
    import sqlite3
    t0 = time.time()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE text IS NOT NULL AND text != ''"
        ).fetchone()[0]
        hi = conn.execute("SELECT COALESCE(MAX(updated_at), 0) FROM entries").fetchone()[0]
        count_seen = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        cap = max(16, int(math.ceil(min(total, _max_rows) * HEADROOM)))
        mat = np.zeros((cap, EMB_DIM), dtype=np.float32)
        ids = np.zeros(cap, dtype=np.int64)
        ts = np.zeros(cap, dtype=np.int64)
        apps = np.empty(cap, dtype=object)
        titles = np.empty(cap, dtype=object)
        id_to_row, ts_to_row = {}, {}
        k = 0
        # Newest first; cap bounds RAM (older rows roll off).
        cur = conn.execute(
            "SELECT id, timestamp, app, title, embedding FROM entries "
            "WHERE text IS NOT NULL AND text != '' ORDER BY id DESC LIMIT ?",
            (min(total, _max_rows),),
        )
        for row in cur:
            vec = _parse_embedding(row["embedding"])
            if vec is None:
                continue
            mat[k] = vec
            ids[k] = row["id"]; ts[k] = row["timestamp"]
            apps[k] = row["app"] or ""; titles[k] = row["title"] or ""
            id_to_row[int(row["id"])] = k
            ts_to_row[int(row["timestamp"])] = k
            k += 1
    snap = _Snapshot(mat, ids, ts, apps, titles, k, id_to_row, ts_to_row, int(hi), int(count_seen))
    _publish(snap)
    print(f"Embedding index loaded: {k} rows in {time.time()-t0:.1f}s (cap {cap})")


def _publish(snap: _Snapshot):
    global _current
    _current = snap  # atomic rebind


def _poll_once(reconcile=False):
    from openrelife.database import get_updated_rows_with_embeddings_since, _connect
    snap = _current
    if snap is None:
        return

    # Trailing re-scan window makes the watermark robust to non-monotonic/cross-process
    # clocks; re-applying already-seen rows is idempotent (keyed by id).
    since = max(0, snap.hi_updated - TRAILING_US)
    rows, new_hi = get_updated_rows_with_embeddings_since(since)

    appends, overwrites = [], []
    for (timestamp, rid, app, title, blob) in rows:
        vec = _parse_embedding(blob)
        if vec is None:
            continue  # stub / zero embedding — no matrix row yet
        rid = int(rid); timestamp = int(timestamp)
        if rid in snap.id_to_row:
            overwrites.append((rid, timestamp, app or "", title or "", vec))
        else:
            appends.append((rid, timestamp, app or "", title or "", vec))

    with _writer_lock:
        deletes = set(_del_pending)
        _del_pending.clear()

    new_count = None
    if reconcile or deletes:
        with _connect() as conn:
            new_count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    if not appends and not overwrites and not deletes and not reconcile:
        if new_hi > snap.hi_updated:  # advance watermark even on a quiet tick
            _publish(_replace_meta(snap, hi_updated=int(new_hi)))
        return

    # Fast path: append-only into reserved headroom → zero-copy publish.
    if appends and not overwrites and not deletes and (snap.n_rows + len(appends)) <= snap.mat.shape[0]:
        _apply_appends_in_place(snap, appends, new_hi)
        return

    # General path: build a fresh snapshot (copy) applying appends+overwrites+deletes.
    _rebuild_with_changes(snap, appends, overwrites, deletes, new_hi, new_count, reconcile)


def _replace_meta(snap, **kw):
    from dataclasses import replace
    return replace(snap, **kw)


def _apply_appends_in_place(snap, appends, new_hi):
    """Write new rows into the hidden headroom of the SHARED arrays, then publish a
    snapshot exposing them. Old readers keep their smaller n_rows and never see the
    half-written region, so this is torn-read-free without copying the matrix."""
    base = snap.n_rows
    id_to_row = dict(snap.id_to_row)
    ts_to_row = dict(snap.ts_to_row)
    k = base
    for (rid, timestamp, app, title, vec) in appends:
        if rid in id_to_row:
            continue  # raced with a prior poll
        snap.mat[k] = vec
        snap.ids[k] = rid; snap.ts[k] = timestamp
        snap.apps[k] = app; snap.titles[k] = title
        id_to_row[rid] = k; ts_to_row[timestamp] = k
        k += 1
    new_snap = _Snapshot(snap.mat, snap.ids, snap.ts, snap.apps, snap.titles, k,
                         id_to_row, ts_to_row, int(max(new_hi, snap.hi_updated)), snap.count_seen)
    _publish(new_snap)


def _rebuild_with_changes(snap, appends, overwrites, deletes, new_hi, new_count, reconcile):
    n = snap.n_rows
    # Determine which existing rows survive.
    drop_rows = set()
    for ts_del in deletes:
        r = snap.ts_to_row.get(ts_del)
        if r is not None and r < n:
            drop_rows.add(r)

    live_ids = None
    if reconcile:
        from openrelife.database import _connect
        with _connect() as conn:
            live_ids = {row[0] for row in conn.execute("SELECT id FROM entries")}

    keep_idx = []
    for r in range(n):
        if r in drop_rows:
            continue
        if live_ids is not None and int(snap.ids[r]) not in live_ids:
            continue  # reconcile: dropped from DB without a notify
        keep_idx.append(r)

    new_rows = appends  # genuinely new
    cap_needed = len(keep_idx) + len(new_rows)
    # apply MAX_RESIDENT_ROWS window (drop oldest = smallest id) if over cap
    survivors = keep_idx
    if cap_needed > _max_rows:
        # keep newest by id
        survivors = sorted(keep_idx, key=lambda r: int(snap.ids[r]))[-(_max_rows - len(new_rows)):]
        cap_needed = len(survivors) + len(new_rows)

    cap = max(16, int(math.ceil(cap_needed * HEADROOM)))
    mat = np.zeros((cap, EMB_DIM), dtype=np.float32)
    ids = np.zeros(cap, dtype=np.int64)
    ts = np.zeros(cap, dtype=np.int64)
    apps = np.empty(cap, dtype=object)
    titles = np.empty(cap, dtype=object)
    id_to_row, ts_to_row = {}, {}
    ow_by_id = {rid: (timestamp, app, title, vec) for (rid, timestamp, app, title, vec) in overwrites}

    k = 0
    for r in survivors:
        rid = int(snap.ids[r])
        if rid in ow_by_id:
            timestamp, app, title, vec = ow_by_id.pop(rid)
            mat[k] = vec; ts[k] = timestamp; apps[k] = app; titles[k] = title
        else:
            mat[k] = snap.mat[r]; ts[k] = snap.ts[r]; apps[k] = snap.apps[r]; titles[k] = snap.titles[r]
        ids[k] = rid; id_to_row[rid] = k; ts_to_row[int(ts[k])] = k
        k += 1
    # any overwrite whose row wasn't a survivor becomes an append
    for rid, (timestamp, app, title, vec) in ow_by_id.items():
        if k >= cap:
            break
        mat[k] = vec; ids[k] = rid; ts[k] = timestamp; apps[k] = app; titles[k] = title
        id_to_row[rid] = k; ts_to_row[timestamp] = k; k += 1
    for (rid, timestamp, app, title, vec) in new_rows:
        if k >= cap or rid in id_to_row:
            continue
        mat[k] = vec; ids[k] = rid; ts[k] = timestamp; apps[k] = app; titles[k] = title
        id_to_row[rid] = k; ts_to_row[timestamp] = k; k += 1

    count_seen = int(new_count) if new_count is not None else snap.count_seen
    _publish(_Snapshot(mat, ids, ts, apps, titles, k, id_to_row, ts_to_row,
                       int(max(new_hi, snap.hi_updated)), count_seen))
