import heapq
import re
import sqlite3
import threading
import time
from collections import namedtuple, OrderedDict
import numpy as np
import json
from typing import Any, List, Optional, Tuple

from openrelife.config import db_path


def _connect() -> sqlite3.Connection:
    """Open a SQLite connection with per-connection PRAGMAs for concurrency.

    - busy_timeout=5000: wait up to 5s on a locked DB (graceful retry, no SQLITE_BUSY)
    - synchronous=NORMAL: safe under WAL, faster than FULL on every commit

    journal_mode=WAL is set once persistently in create_db() — it sticks to the
    database file across connections.
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# Define the structure of a database entry using namedtuple
Entry = namedtuple("Entry", ["id", "app", "title", "text", "timestamp", "embedding", "words_coords", "ai_text", "ai_words_coords"])

# Lightweight entry without embedding blob (for timeline/sync)
LightEntry = namedtuple("LightEntry", ["id", "app", "title", "text", "timestamp", "words_coords", "ai_text", "ai_words_coords"])

# Minimal entry for bulk listing — no embedding, no coords (~1KB per entry vs ~35KB)
MetadataEntry = namedtuple("MetadataEntry", ["id", "app", "title", "text", "timestamp", "ai_text"])


def create_db() -> None:
    """
    Creates the SQLite database and the 'entries' table if they don't exist.

    The table schema includes columns for an auto-incrementing ID, application name,
    window title, extracted text, timestamp, and text embedding.
    """
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            # Enable Write-Ahead Logging once. WAL is persistent on the DB file:
            # readers and writers no longer block each other, eliminating the
            # UI freezes that happened during OCR write bursts under the default
            # rollback journal.
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS entries (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       app TEXT,
                       title TEXT,
                       text TEXT,
                       timestamp INTEGER UNIQUE,
                       embedding BLOB,
                       words_coords TEXT,
                       ai_text TEXT,
                       ai_words_coords TEXT
                   )"""
            )
            # Add index on timestamp for faster lookups
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON entries (timestamp)"
            )
            
            # Migration: Add words_coords column if it doesn't exist
            cursor.execute("PRAGMA table_info(entries)")
            columns = [column[1] for column in cursor.fetchall()]
            if "words_coords" not in columns:
                cursor.execute("ALTER TABLE entries ADD COLUMN words_coords TEXT DEFAULT '[]'")
            if "ai_text" not in columns:
                cursor.execute("ALTER TABLE entries ADD COLUMN ai_text TEXT")
            if "ai_words_coords" not in columns:
                cursor.execute("ALTER TABLE entries ADD COLUMN ai_words_coords TEXT")
            if "updated_at" not in columns:
                cursor.execute("ALTER TABLE entries ADD COLUMN updated_at INTEGER DEFAULT 0")
                # Backfill: set updated_at = timestamp for existing entries
                cursor.execute("UPDATE entries SET updated_at = timestamp WHERE updated_at = 0 OR updated_at IS NULL")

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_updated_at ON entries (updated_at)"
            )

            # Fix: entries created before async OCR (pre April 9 2025) that have
            # text=NULL were incorrectly migrated. They were already processed by the
            # old sync OCR — mark them as processed-empty so they don't clog the queue.
            cursor.execute(
                "UPDATE entries SET text = '' WHERE text IS NULL AND timestamp < 1775725200000000"
            )

            conn.commit()
    except sqlite3.Error as e:
        print(f"Database error during table creation: {e}")


def get_all_entries(limit: int = None, min_timestamp: int = 0) -> List[Entry]:
    """
    Retrieves entries from the database.

    Args:
        limit (int, optional): Maximum number of entries to return. Defaults to None (all).
        min_timestamp (int, optional): Only return entries newer than this timestamp. Defaults to 0.

    Returns:
        List[Entry]: A list of entries as Entry namedtuples.
    """
    entries: List[Entry] = []
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row  # Return rows as dictionary-like objects
            cursor = conn.cursor()
            
            query = "SELECT id, app, title, text, timestamp, embedding, words_coords, ai_text, ai_words_coords FROM entries WHERE timestamp > ? ORDER BY timestamp DESC"
            params = [min_timestamp]
            
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor.execute(query, tuple(params))
            results = cursor.fetchall()
            for row in results:
                # Deserialize the embedding blob back into a NumPy array
                embedding = np.frombuffer(row["embedding"], dtype=np.float32)
                words_coords_str = row["words_coords"] if row["words_coords"] else "[]"
                try:
                    words_coords = json.loads(words_coords_str)
                except (json.JSONDecodeError, TypeError):
                    words_coords = []
                
                ai_words_coords_str = row["ai_words_coords"] if row["ai_words_coords"] else "[]"
                try:
                    ai_words_coords = json.loads(ai_words_coords_str)
                except (json.JSONDecodeError, TypeError):
                    ai_words_coords = []
                    
                entries.append(
                    Entry(
                        id=row["id"],
                        app=row["app"],
                        title=row["title"],
                        text=row["text"],
                        timestamp=row["timestamp"],
                        embedding=embedding,
                        words_coords=words_coords,
                        ai_text=row["ai_text"],
                        ai_words_coords=ai_words_coords,
                    )
                )
    except sqlite3.Error as e:
        print(f"Database error while fetching all entries: {e}")
    return entries


def get_timestamps() -> List[int]:
    """
    Retrieves all timestamps from the database, ordered descending.

    Returns:
        List[int]: A list of all timestamps.
                   Returns an empty list if the table is empty or an error occurs.
    """
    timestamps: List[int] = []
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            # Use the index for potentially faster retrieval
            cursor.execute("SELECT timestamp FROM entries ORDER BY timestamp DESC")
            results = cursor.fetchall()
            timestamps = [result[0] for result in results]
    except sqlite3.Error as e:
        print(f"Database error while fetching timestamps: {e}")
    return timestamps


def update_ai_ocr(timestamp: int, ai_text: str, ai_words_coords: List) -> bool:
    """
    Updates AI OCR data for an existing entry.
    
    Args:
        timestamp (int): The Unix timestamp of the screenshot.
        ai_text (str): The AI-extracted text.
        ai_words_coords (List): List of word coordinates from AI OCR.
    
    Returns:
        bool: True if update was successful, False otherwise.
    """
    ai_words_coords_json: str = json.dumps(ai_words_coords) if ai_words_coords else "[]"
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            import time as _time
            now_us = int(_time.time() * 1000000)
            cursor.execute(
                """UPDATE entries
                   SET ai_text = ?, ai_words_coords = ?, updated_at = ?
                   WHERE timestamp = ?""",
                (ai_text, ai_words_coords_json, now_us, timestamp),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Database error during AI OCR update: {e}")
        return False


def insert_entry(
    text: str, timestamp: int, embedding: np.ndarray, app: str, title: str, words_coords: List = None
) -> Optional[int]:
    """
    Inserts a new entry into the database.

    Args:
        text (str): The extracted text content.
        timestamp (int): The Unix timestamp of the screenshot.
        embedding (np.ndarray): The embedding vector for the text.
        app (str): The name of the active application.
        title (str): The title of the active window.
        words_coords (List): List of word coordinates from OCR.

    Returns:
        Optional[int]: The ID of the newly inserted row, or None if insertion fails.
                       Prints an error message to stderr on failure.
    """
    embedding_bytes: bytes = embedding.astype(np.float32).tobytes() # Ensure consistent dtype
    words_coords_json: str = json.dumps(words_coords) if words_coords else "[]"
    last_row_id: Optional[int] = None
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO entries (text, timestamp, embedding, app, title, words_coords)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(timestamp) DO NOTHING""", # Avoid duplicates based on timestamp
                (text, timestamp, embedding_bytes, app, title, words_coords_json),
            )
            conn.commit()
            if cursor.rowcount > 0: # Check if insert actually happened
                last_row_id = cursor.lastrowid

    except sqlite3.Error as e:
        print(f"Database error during insertion: {e}")
    return last_row_id


def insert_entry_stub(timestamp: int, app: str, title: str) -> Optional[int]:
    """Insert a placeholder entry without text/embedding (filled later by OCR worker)."""
    zero_embedding = np.zeros(384, dtype=np.float32).tobytes()
    last_row_id: Optional[int] = None
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO entries (text, timestamp, embedding, app, title, words_coords, updated_at)
                   VALUES (NULL, ?, ?, ?, ?, '[]', ?)
                   ON CONFLICT(timestamp) DO NOTHING""",
                (timestamp, zero_embedding, app, title, timestamp),
            )
            conn.commit()
            if cursor.rowcount > 0:
                last_row_id = cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error during stub insertion: {e}")
    return last_row_id


def update_entry_ocr(timestamp: int, text: str, embedding: np.ndarray, words_coords: List = None) -> bool:
    """Fill in OCR results for a previously inserted stub entry."""
    import time as _time
    embedding_bytes: bytes = embedding.astype(np.float32).tobytes()
    words_coords_json: str = json.dumps(words_coords) if words_coords else "[]"
    now_us = int(_time.time() * 1000000)
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE entries SET text = ?, embedding = ?, words_coords = ?, updated_at = ?
                   WHERE timestamp = ?""",
                (text, embedding_bytes, words_coords_json, now_us, timestamp),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        print(f"Database error during OCR update: {e}")
        return False


def delete_entries(timestamps: List[int]) -> int:
    """
    Deletes entries with the specified timestamps from the database.
    
    Args:
        timestamps (List[int]): List of timestamps to delete.
        
    Returns:
        int: Number of deleted entries.
    """
    deleted_count = 0
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(timestamps))
            sql = f"DELETE FROM entries WHERE timestamp IN ({placeholders})"
            cursor.execute(sql, timestamps)
            conn.commit()
            deleted_count = cursor.rowcount
    except sqlite3.Error as e:
        print(f"Database error during deletion: {e}")
    if deleted_count:
        # A delete can leave deleted ids inside cached rankings (and over-report
        # total) until the freshness token catches the COUNT change. Deletes are
        # rare, explicit user actions — clear the cache outright to stay correct.
        _invalidate_rank_cache()
    return deleted_count




def get_pending_ocr_timestamps() -> List[int]:
    """Returns timestamps of entries that have no OCR text (stub entries)."""
    timestamps: List[int] = []
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp FROM entries WHERE text IS NULL ORDER BY timestamp DESC"
            )
            timestamps = [row[0] for row in cursor]
    except sqlite3.Error as e:
        print(f"Database error while fetching pending OCR timestamps: {e}")
    return timestamps


def get_pending_ocr_timestamps_in_set(candidate_timestamps: List[int]) -> List[int]:
    """Returns subset of candidate_timestamps that still have text=NULL.

    Used to detect frames that weren't processed (e.g. after subprocess hang/kill).
    """
    if not candidate_timestamps:
        return []
    pending: List[int] = []
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(candidate_timestamps))
            cursor.execute(
                f"SELECT timestamp FROM entries WHERE text IS NULL AND timestamp IN ({placeholders})",
                candidate_timestamps,
            )
            pending = [row[0] for row in cursor]
    except sqlite3.Error as e:
        print(f"Database error while checking pending subset: {e}")
    return pending


def get_entries_metadata(limit: int = None, min_timestamp: int = 0) -> List[MetadataEntry]:
    """Retrieves entries without embedding or coords — only ~1KB per entry."""
    entries: List[MetadataEntry] = []
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT id, app, title, text, timestamp, ai_text FROM entries WHERE timestamp > ? ORDER BY timestamp DESC"
            params: list = [min_timestamp]

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, tuple(params))
            for row in cursor:
                entries.append(
                    MetadataEntry(
                        id=row["id"],
                        app=row["app"],
                        title=row["title"],
                        text=row["text"],
                        timestamp=row["timestamp"],
                        ai_text=row["ai_text"],
                    )
                )
    except sqlite3.Error as e:
        print(f"Database error while fetching metadata entries: {e}")
    return entries


def get_timestamps_updated_since(since_updated_at: int = 0) -> tuple:
    """Returns (timestamps, max_updated_at) for entries where updated_at > since.

    Lightweight: returns only timestamps, no entry data.
    """
    timestamps: List[int] = []
    max_updated: int = since_updated_at
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            # INDEXED BY idx_updated_at forces the planner to use the updated_at
            # index for the WHERE filter. Without the hint, the ORDER BY timestamp
            # DESC clause makes SQLite prefer a full scan via idx_timestamp
            # (~125k rows) just to find the few recently-updated entries —
            # turning every /api/sync poll into a 1–3 s lockup.
            cursor.execute(
                "SELECT timestamp, updated_at FROM entries "
                "INDEXED BY idx_updated_at "
                "WHERE updated_at > ? ORDER BY timestamp DESC",
                (since_updated_at,),
            )
            for row in cursor:
                timestamps.append(row[0])
                if row[1] > max_updated:
                    max_updated = row[1]
    except sqlite3.Error as e:
        print(f"Database error while fetching updated timestamps: {e}")
    return timestamps, max_updated


def get_new_timestamps(since_timestamp: int = 0, limit: int = 500) -> tuple:
    """Returns (timestamps, new_cursor) for newly captured entries.

    Timestamp-based and bounded: only entries with ``timestamp > since_timestamp``
    are returned, oldest first, capped at ``limit``. ``new_cursor`` is the largest
    timestamp returned (or ``since_timestamp`` if none), so the next poll resumes
    after the last drained capture without skipping any.

    Unlike a ``updated_at``-based poll, this is immune to OCR backlogs: re-OCR'ing
    old frames bumps their ``updated_at`` but not their ``timestamp``, so they
    never flood this endpoint. Stale cached OCR text is refreshed lazily on view.
    """
    timestamps: List[int] = []
    new_cursor: int = since_timestamp
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp FROM entries "
                "WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                (since_timestamp, limit),
            )
            for row in cursor:
                timestamps.append(row[0])
            if timestamps:
                new_cursor = timestamps[-1]  # ASC order -> last is the largest
    except sqlite3.Error as e:
        print(f"Database error while fetching new timestamps: {e}")
    return timestamps, new_cursor


def get_entries_light(limit: int = None, min_timestamp: int = 0) -> List[LightEntry]:
    """Retrieves entries without embedding blob (saves ~1.5KB per entry)."""
    entries: List[LightEntry] = []
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT id, app, title, text, timestamp, words_coords, ai_text, ai_words_coords FROM entries WHERE timestamp > ? ORDER BY timestamp DESC"
            params: list = [min_timestamp]

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, tuple(params))
            for row in cursor:
                words_coords_str = row["words_coords"] if row["words_coords"] else "[]"
                try:
                    words_coords = json.loads(words_coords_str)
                except (json.JSONDecodeError, TypeError):
                    words_coords = []

                ai_words_coords_str = row["ai_words_coords"] if row["ai_words_coords"] else "[]"
                try:
                    ai_words_coords = json.loads(ai_words_coords_str)
                except (json.JSONDecodeError, TypeError):
                    ai_words_coords = []

                entries.append(
                    LightEntry(
                        id=row["id"],
                        app=row["app"],
                        title=row["title"],
                        text=row["text"],
                        timestamp=row["timestamp"],
                        words_coords=words_coords,
                        ai_text=row["ai_text"],
                        ai_words_coords=ai_words_coords,
                    )
                )
    except sqlite3.Error as e:
        print(f"Database error while fetching light entries: {e}")
    return entries


# Absolute cosine floor: candidates below this are dropped outright (keyword
# matches bypass it). Kept low because all-MiniLM-L6-v2 maps noisy OCR text into
# a narrow cone where even good multi-word queries top out around 0.4 — a high
# absolute floor would silently nuke legitimate searches.
DEFAULT_MIN_SIMILARITY = 0.15
# Adaptive relevance: keep semantic-only matches within this margin of the best
# match for the query. Because the cosine scale is query-dependent, an absolute
# cutoff can't separate good from bad queries; a margin relative to the top does.
DEFAULT_RELEVANCE_MARGIN = 0.12
# A screen captured repeatedly produces many near-identical consecutive frames.
# Collapse a run of same-window captures (same app+title, each within this gap of
# the previous) into a single representative so results show distinct moments.
DEFAULT_DEDUPE_WINDOW_US = 120 * 1_000_000  # 2 minutes

# ---------------------------------------------------------------------------
# Per-query ranked-id cache.
#
# search_entries_streaming does a full ~150k-row scan that costs ~18s. The ONLY
# durable product of that scan is the ranked id list, so we cache exactly that
# per (normalized query + filters): the first query pays ~18s once, and every
# subsequent page is ranked_ids[offset:offset+limit] -> _fetch_entries_by_ids
# (an indexed lookup over <=50 rows, single-digit ms). This is what makes
# pagination / "load more" instant instead of ~18s per page.
#
# Invalidation uses a freshness token = (MAX(timestamp) << 24) ^ COUNT(*).
# Verified against the live code: insert_entry_stub writes the final timestamp at
# insert and update_entry_ocr only touches text/embedding/words_coords/updated_at
# (never timestamp or row count), so the token is STABLE while the OCR worker
# drains its backlog continuously, and changes only when /api/sync ingests a
# genuinely new capture (or on delete — see delete_entries, which clears outright).
_RANK_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_RANK_CACHE_LOCK = threading.Lock()
_RANK_CACHE_CAP = 32          # LRU capacity (distinct query+filter combos)
_RANK_IDS_CAP = 5000          # bound stored ids per query (load-more past this is implausible)
_TOKEN_TTL_US = 1_000_000     # recompute the freshness token at most once/sec
_token_cache = {"value": None, "at_us": 0}
# Coalesce concurrent identical cold builds: the HTTP layer can't cancel a
# running scan, so without this a user pausing mid-type could fire two requests
# for the same query and run the ~18s scan twice. Second caller waits, then reads.
_INFLIGHT: dict = {}


def _cache_key(query_text, min_similarity, relevance_margin, dedupe_window_us, since, until, app):
    q = " ".join((query_text or "").lower().split())
    # app is NOT lowercased: the scan filters with a case-sensitive `app = ?`,
    # so the key must preserve case or it would serve cross-case cached results.
    return (q, round(min_similarity, 4), round(relevance_margin, 4), dedupe_window_us,
            since or 0, until or 0, app or "")


def _freshness_token() -> int:
    """(MAX(timestamp)<<24) ^ COUNT(*), TTL-gated so rapid load-more clicks don't
    re-query. Changes only on new captures/deletes, not on OCR text fills."""
    now = int(time.time() * 1_000_000)
    if _token_cache["value"] is not None and now - _token_cache["at_us"] < _TOKEN_TTL_US:
        return _token_cache["value"]
    tok = 0
    try:
        with _connect() as conn:
            row = conn.execute("SELECT MAX(timestamp) AS m, COUNT(*) AS c FROM entries").fetchone()
            tok = ((row[0] or 0) << 24) ^ (row[1] or 0)
    except sqlite3.Error as e:
        print(f"Database error computing freshness token: {e}")
    _token_cache["value"] = tok
    _token_cache["at_us"] = now
    return tok


def _invalidate_rank_cache():
    """Drop all cached rankings (and force a token recompute). Called on deletes,
    which — unlike the continuous OCR path — are rare, explicit user actions."""
    with _RANK_CACHE_LOCK:
        _RANK_CACHE.clear()
    _token_cache["value"] = None


def _fetch_entries_by_ids(conn, ids: List[int]) -> dict:
    """Fetch {id: {id, app, title, text, timestamp}} for the given ids."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    cursor = conn.execute(
        f"SELECT id, app, title, text, timestamp FROM entries WHERE id IN ({placeholders})",
        ids,
    )
    out = {}
    for row in cursor:
        out[row["id"]] = {
            'id': row["id"],
            'app': row["app"],
            'title': row["title"],
            'text': row["text"],
            'timestamp': row["timestamp"],
        }
    return out


def _ranked_ids_for_query(query_embedding, query_text, min_similarity,
                          relevance_margin, dedupe_window_us,
                          since=None, until=None, app=None):
    """The ~18s hot path: full scan + keyword-first/semantic ranking + dedupe.

    Returns (ranked_ids, total) — the COMPLETE ranked id list (not a page). Paging
    and caching are handled by search_entries_streaming. Optional since/until/app
    narrow the scan via a WHERE clause (also used for Phase-2 filters).
    """
    query_norm = np.linalg.norm(query_embedding)
    if query_norm == 0:
        return [], 0

    query_lower = query_text.lower() if query_text else ""
    # Whole-word matching: ignore 1-char tokens (e.g. Italian "e"/"a"), and match
    # on word boundaries so "cani" matches the word "cani" but NOT "meccanici".
    query_words = [w for w in query_lower.split() if len(w) >= 2]
    word_res = [(w, re.compile(r"\b" + re.escape(w) + r"\b")) for w in query_words]
    # Full-phrase match is also whole-word bounded (a single-word query would
    # otherwise fall back to a substring match here and re-introduce the bug).
    phrase_re = re.compile(r"\b" + re.escape(query_lower) + r"\b") if query_lower else None

    where, params = [], []
    if since:
        where.append("timestamp >= ?"); params.append(since)
    if until:
        where.append("timestamp <= ?"); params.append(until)
    if app:
        where.append("app = ?"); params.append(app)
    sql = "SELECT id, app, title, text, timestamp, embedding FROM entries"
    if where:
        sql += " WHERE " + " AND ".join(where)

    # Lightweight candidates: (has_keyword, keyword_strength, semantic, timestamp,
    # id, app, title). app/title are kept (not hashed) so the run-dedupe below
    # compares the exact window signature — a hash could false-collapse two
    # distinct adjacent windows on a collision.
    candidates: list = []
    top_semantic = float("-inf")

    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)

            for row in cursor:
                embedding = np.frombuffer(row["embedding"], dtype=np.float32)
                emb_norm = np.linalg.norm(embedding)
                if emb_norm == 0:
                    continue

                semantic_score = float(np.dot(query_embedding, embedding) / (query_norm * emb_norm))

                keyword_strength = 0.0
                if query_lower:
                    text_lower = row["text"].lower() if row["text"] else ""
                    if query_lower in text_lower and phrase_re.search(text_lower):
                        keyword_strength = 1.0
                    elif word_res:
                        matched = sum(1 for w, rx in word_res
                                      if w in text_lower and rx.search(text_lower))
                        if matched > 0:
                            keyword_strength = matched / len(word_res)
                has_keyword = keyword_strength > 0.0

                if semantic_score < min_similarity and not has_keyword:
                    continue

                if semantic_score > top_semantic:
                    top_semantic = semantic_score
                candidates.append((has_keyword, keyword_strength, semantic_score,
                                   row["timestamp"], row["id"],
                                   row["app"] or "", row["title"] or ""))

            if not candidates:
                return [], 0

            # Tier 2 (semantic-only) gated by an adaptive floor relative to the
            # best match; Tier 1 (keyword) bypasses it so literal hits never drop.
            cutoff = top_semantic - relevance_margin
            kept = [c for c in candidates if c[0] or c[2] >= cutoff]

            # Keyword tier first; within keyword tier by match strength then
            # newest; within semantic tier by similarity then newest.
            kept.sort(key=lambda c: (
                1 if c[0] else 0,
                c[1] if c[0] else c[2],
                c[3],
            ), reverse=True)

            # Collapse runs of the same window (same sig within dedupe_window_us).
            if dedupe_window_us and dedupe_window_us > 0:
                deduped = []
                prev_sig = None
                prev_ts = None
                for c in kept:
                    sig = (c[0], c[5], c[6])  # has_keyword, app, title
                    if (prev_sig is not None and sig == prev_sig
                            and abs(c[3] - prev_ts) <= dedupe_window_us):
                        prev_ts = c[3]  # chain the run so long sessions collapse fully
                        continue
                    deduped.append(c)
                    prev_sig = sig
                    prev_ts = c[3]
                kept = deduped

            return [c[4] for c in kept], len(kept)

    except sqlite3.Error as e:
        print(f"Database error during streaming search: {e}")
        return [], 0


def search_entries_streaming(
    query_embedding: np.ndarray,
    query_text: str = "",
    limit: int = 50,
    offset: int = 0,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    relevance_margin: float = DEFAULT_RELEVANCE_MARGIN,
    dedupe_window_us: int = DEFAULT_DEDUPE_WINDOW_US,
    since: Optional[int] = None,
    until: Optional[int] = None,
    app: Optional[str] = None,
    now_us: Optional[int] = None,
) -> dict:
    """Paginated keyword-first search, backed by a per-query ranked-id cache.

    The expensive ranking scan runs at most once per (normalized query + filters)
    until new captures arrive: the first page pays ~18s, every subsequent page is
    served instantly by slicing the cached ranked id list and re-fetching only the
    page's rows. See the cache notes near the top of this module.

    Args:
        query_embedding, query_text, min_similarity, relevance_margin,
        dedupe_window_us: ranking parameters (see _ranked_ids_for_query).
        limit/offset: page window over the ranked set.
        since/until/app: optional filters (microsecond timestamps / app name).
        now_us: accepted for backward compatibility; unused.

    Returns dict {results, total, offset, limit, has_more}. ``total`` is capped to
    the number of reachable (cached) ids so the count never over-reports.
    """
    empty = {"results": [], "total": 0, "offset": offset, "limit": limit, "has_more": False}

    token = _freshness_token()
    key = _cache_key(query_text, min_similarity, relevance_margin, dedupe_window_us,
                     since, until, app)

    def _build():
        ids, tot = _ranked_ids_for_query(query_embedding, query_text, min_similarity,
                                         relevance_margin, dedupe_window_us, since, until, app)
        ids = ids[:_RANK_IDS_CAP]
        return ids, min(tot, len(ids))  # honest total: never exceeds reachable ids

    ranked_ids = None
    total = 0
    with _RANK_CACHE_LOCK:
        entry = _RANK_CACHE.get(key)
        if entry is not None and entry["token"] == token:
            _RANK_CACHE.move_to_end(key)
            ranked_ids, total = entry["ids"], entry["total"]
        else:
            ev = _INFLIGHT.get(key)
            if ev is None:
                ev = threading.Event()
                _INFLIGHT[key] = ev
                iam_builder = True
            else:
                iam_builder = False

    if ranked_ids is None:
        if iam_builder:
            try:
                ranked_ids, total = _build()
                now = int(time.time() * 1_000_000)
                with _RANK_CACHE_LOCK:
                    _RANK_CACHE[key] = {"ids": ranked_ids, "total": total,
                                        "created_us": now, "token": token}
                    _RANK_CACHE.move_to_end(key)
                    while len(_RANK_CACHE) > _RANK_CACHE_CAP:
                        _RANK_CACHE.popitem(last=False)
            finally:
                with _RANK_CACHE_LOCK:
                    if _INFLIGHT.get(key) is ev:
                        del _INFLIGHT[key]
                ev.set()
        else:
            # Another thread is already building this exact query — wait for it
            # rather than launching a duplicate ~18s scan (the server can't honor
            # the client's AbortController on a CPU-bound scan).
            ev.wait(timeout=120)
            with _RANK_CACHE_LOCK:
                entry = _RANK_CACHE.get(key)
            if entry is not None and entry["token"] == token:
                ranked_ids, total = entry["ids"], entry["total"]
            else:
                ranked_ids, total = _build()  # builder failed / token moved; do it ourselves

    if not ranked_ids:
        return {"results": [], "total": total, "offset": offset,
                "limit": limit, "has_more": False}

    page_ids = ranked_ids[offset:offset + limit]
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows_by_id = _fetch_entries_by_ids(conn, page_ids)
    except sqlite3.Error as e:
        print(f"Database error fetching search page: {e}")
        return empty
    # Preserve ranked order; tolerate ids deleted since the ranking was cached.
    page = [rows_by_id[i] for i in page_ids if i in rows_by_id]

    return {
        "results": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


def build_snippet(text: str, query_text: str, width: int = 160) -> str:
    """A short, single-line preview window centered on the first whole-word match
    of the query (or the start of the text if none). Whitespace is collapsed so
    newline-heavy OCR text reads inline. The frontend highlights matches itself
    (re-derives them in JS) to avoid Python-codepoint vs JS-UTF16 offset drift.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    words = [w for w in (query_text or "").lower().split() if len(w) >= 2]
    low = text.lower()
    first = -1
    for w in words:
        m = re.search(r"\b" + re.escape(w) + r"\b", low)
        if m and (first == -1 or m.start() < first):
            first = m.start()
    if first < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, first - width // 3)
    end = start + width
    return (("…" if start > 0 else "") + text[start:end]
            + ("…" if end < len(text) else ""))


def get_entry_by_timestamp(timestamp: int) -> Optional[Entry]:
    """
    Retrieves a single entry by its timestamp.

    Args:
        timestamp (int): The timestamp of the entry to retrieve.

    Returns:
        Optional[Entry]: The entry as an Entry namedtuple, or None if not found.
    """
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT id, app, title, text, timestamp, embedding, words_coords, ai_text, ai_words_coords FROM entries WHERE timestamp = ?"
            cursor.execute(query, (timestamp,))
            row = cursor.fetchone()
            
            if row:
                # Deserialize the embedding blob back into a NumPy array
                embedding = np.frombuffer(row["embedding"], dtype=np.float32)
                words_coords_str = row["words_coords"] if row["words_coords"] else "[]"
                try:
                    words_coords = json.loads(words_coords_str)
                except (json.JSONDecodeError, TypeError):
                    words_coords = []
                
                ai_words_coords_str = row["ai_words_coords"] if row["ai_words_coords"] else "[]"
                try:
                    ai_words_coords = json.loads(ai_words_coords_str)
                except (json.JSONDecodeError, TypeError):
                    ai_words_coords = []
                    
                return Entry(
                    id=row["id"],
                    app=row["app"],
                    title=row["title"],
                    text=row["text"],
                    timestamp=row["timestamp"],
                    embedding=embedding,
                    words_coords=words_coords,
                    ai_text=row["ai_text"],
                    ai_words_coords=ai_words_coords
                )
    except sqlite3.Error as e:
        print(f"Database error during entry retrieval: {e}")
    
    return None
