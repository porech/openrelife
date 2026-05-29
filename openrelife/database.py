import heapq
import sqlite3
from collections import namedtuple
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
# Adaptive relevance: keep entries scoring within this margin of the best match
# for the query. Because the cosine scale is query-dependent, an absolute cutoff
# can't separate good from bad queries; a margin relative to the top score does.
DEFAULT_RELEVANCE_MARGIN = 0.12
# Recency only nudges near-ties: bonus in [0, RECENCY_WEIGHT], decaying with age.
RECENCY_WEIGHT = 0.1
RECENCY_TAU_US = 7 * 24 * 60 * 60 * 1_000_000  # 7-day e-folding time, in microseconds


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


def search_entries_streaming(
    query_embedding: np.ndarray,
    query_text: str = "",
    limit: int = 50,
    offset: int = 0,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    relevance_margin: float = DEFAULT_RELEVANCE_MARGIN,
    now_us: Optional[int] = None,
) -> dict:
    """Search entries by semantic relevance, with an adaptive relevance floor.

    Scans every row once, computing cosine similarity per row. A row becomes a
    candidate if its similarity reaches ``min_similarity`` (a low absolute floor)
    OR the query text matches its OCR text (keyword matches always qualify).
    After the scan, an *adaptive* floor is applied: only candidates scoring within
    ``relevance_margin`` of the best match for this query are kept. This adapts to
    the query-dependent cosine scale of the embedding model, so weak queries yield
    a smaller (or empty) set instead of being padded with unrelated entries, while
    legitimate low-scoring queries are not nuked by a fixed cutoff.

    Ranking is dominated by the semantic score, refined by a keyword boost and a
    small recency bonus:

        final_score = semantic_score + keyword_boost + recency_bonus
        semantic_score in [-1, 1]; keyword_boost in [0, 0.5];
        recency_bonus  = RECENCY_WEIGHT * exp(-age / RECENCY_TAU_US)  in [0, 0.1]

    Supports offset/limit pagination over the full ranked, thresholded result set,
    so older relevant entries remain reachable.

    Memory: only lightweight per-candidate tuples (no OCR text) are held during
    the scan; full rows for the requested page are re-fetched by id at the end.

    Args:
        query_embedding: query vector.
        query_text: raw query string, used for keyword matching.
        limit: page size.
        offset: number of leading results to skip.
        min_similarity: absolute cosine floor for non-keyword candidates.
        relevance_margin: keep candidates with semantic_score >= top_score - margin.
        now_us: reference "now" in microseconds for recency decay (defaults to
            wall-clock time; injectable for deterministic tests).

    Returns:
        dict with keys ``results`` (list of entry dicts, best first),
        ``total`` (number of entries passing the adaptive filter), ``offset``,
        ``limit`` and ``has_more``.
    """
    empty = {"results": [], "total": 0, "offset": offset, "limit": limit, "has_more": False}

    query_norm = np.linalg.norm(query_embedding)
    if query_norm == 0:
        return empty

    if now_us is None:
        import time
        now_us = int(time.time() * 1_000_000)

    query_lower = query_text.lower() if query_text else ""
    query_words = query_lower.split() if query_lower else []

    # Lightweight candidates: (final_score, timestamp, semantic_score, has_keyword, id).
    candidates: list = []
    top_semantic = float("-inf")

    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, text, timestamp, embedding FROM entries"
            )

            for row in cursor:
                embedding = np.frombuffer(row["embedding"], dtype=np.float32)
                emb_norm = np.linalg.norm(embedding)
                if emb_norm == 0:
                    continue

                semantic_score = float(np.dot(query_embedding, embedding) / (query_norm * emb_norm))

                keyword_boost = 0.0
                has_keyword = False
                if query_lower:
                    text_lower = row["text"].lower() if row["text"] else ""
                    if query_lower in text_lower:
                        keyword_boost = 0.5
                        has_keyword = True
                    elif query_words:
                        matched = sum(1 for w in query_words if w in text_lower)
                        if matched > 0:
                            keyword_boost = 0.3 * (matched / len(query_words))
                            has_keyword = True

                # Absolute floor: drop the genuine bottom, unless the query text
                # literally appears in the entry.
                if semantic_score < min_similarity and not has_keyword:
                    continue

                age_us = max(0, now_us - row["timestamp"])
                recency_bonus = RECENCY_WEIGHT * np.exp(-age_us / RECENCY_TAU_US)
                final_score = semantic_score + keyword_boost + float(recency_bonus)

                if semantic_score > top_semantic:
                    top_semantic = semantic_score
                candidates.append((final_score, row["timestamp"], semantic_score,
                                   has_keyword, row["id"]))

            if not candidates:
                return empty

            # Adaptive floor relative to the best match for this query. Keyword
            # matches are kept regardless so literal hits never disappear.
            cutoff = top_semantic - relevance_margin
            kept = [c for c in candidates if c[2] >= cutoff or c[3]]

            # Best first: highest final_score, then newest.
            kept.sort(key=lambda c: (c[0], c[1]), reverse=True)
            total = len(kept)

            page_meta = kept[offset:offset + limit]
            page_ids = [c[4] for c in page_meta]
            rows_by_id = _fetch_entries_by_ids(conn, page_ids)
            page = [rows_by_id[i] for i in page_ids if i in rows_by_id]

    except sqlite3.Error as e:
        print(f"Database error during streaming search: {e}")
        return empty

    return {
        "results": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


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
