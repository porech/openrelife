import heapq
import sqlite3
from collections import namedtuple
import numpy as np
import json
from typing import Any, List, Optional, Tuple

from openrelife.config import db_path

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
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
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
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE entries 
                   SET ai_text = ?, ai_words_coords = ?
                   WHERE timestamp = ?""",
                (ai_text, ai_words_coords_json, timestamp),
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
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO entries (text, timestamp, embedding, app, title, words_coords)
                   VALUES ('', ?, ?, ?, ?, '[]')
                   ON CONFLICT(timestamp) DO NOTHING""",
                (timestamp, zero_embedding, app, title),
            )
            conn.commit()
            if cursor.rowcount > 0:
                last_row_id = cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error during stub insertion: {e}")
    return last_row_id


def update_entry_ocr(timestamp: int, text: str, embedding: np.ndarray, words_coords: List = None) -> bool:
    """Fill in OCR results for a previously inserted stub entry."""
    embedding_bytes: bytes = embedding.astype(np.float32).tobytes()
    words_coords_json: str = json.dumps(words_coords) if words_coords else "[]"
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE entries SET text = ?, embedding = ?, words_coords = ?
                   WHERE timestamp = ?""",
                (text, embedding_bytes, words_coords_json, timestamp),
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
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp FROM entries WHERE text = '' OR text IS NULL ORDER BY timestamp ASC"
            )
            timestamps = [row[0] for row in cursor]
    except sqlite3.Error as e:
        print(f"Database error while fetching pending OCR timestamps: {e}")
    return timestamps


def get_entries_metadata(limit: int = None, min_timestamp: int = 0) -> List[MetadataEntry]:
    """Retrieves entries without embedding or coords — only ~1KB per entry."""
    entries: List[MetadataEntry] = []
    try:
        with sqlite3.connect(db_path) as conn:
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


def get_entries_light(limit: int = None, min_timestamp: int = 0) -> List[LightEntry]:
    """Retrieves entries without embedding blob (saves ~1.5KB per entry)."""
    entries: List[LightEntry] = []
    try:
        with sqlite3.connect(db_path) as conn:
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


def search_entries_streaming(query_embedding: np.ndarray, query_text: str = "", limit: int = 20) -> List[dict]:
    """Search entries using streaming cosine similarity — constant memory.

    Iterates through all rows one at a time via a cursor, computes similarity
    per-row, and keeps only the top-N results in a min-heap. Never loads all
    entries into memory at once.

    Returns a list of dicts sorted by final score descending.
    """
    query_norm = np.linalg.norm(query_embedding)
    if query_norm == 0:
        return []

    query_lower = query_text.lower() if query_text else ""
    query_words = query_lower.split() if query_lower else []

    # Min-heap of (score, has_keyword, dict) — keeps top `limit` results
    heap: list = []

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, app, title, text, timestamp, embedding FROM entries"
            )

            for row in cursor:
                embedding = np.frombuffer(row["embedding"], dtype=np.float32)
                emb_norm = np.linalg.norm(embedding)
                if emb_norm == 0:
                    continue

                semantic_score = float(np.dot(query_embedding, embedding) / (query_norm * emb_norm))

                keyword_boost = 0.0
                if query_lower:
                    text_lower = row["text"].lower() if row["text"] else ""
                    if query_lower in text_lower:
                        keyword_boost = 0.5
                    elif query_words:
                        matched = sum(1 for w in query_words if w in text_lower)
                        if matched > 0:
                            keyword_boost = 0.3 * (matched / len(query_words))

                recency_score = row["timestamp"] / 1e10
                final_score = semantic_score + keyword_boost + recency_score
                has_keyword = keyword_boost > 0

                # Heap key: (has_keyword, final_score) — we want highest first,
                # but heapq is a min-heap, so we negate.
                heap_key = (-int(has_keyword), -final_score)

                if len(heap) < limit:
                    heapq.heappush(heap, (heap_key, {
                        'id': row["id"],
                        'app': row["app"],
                        'title': row["title"],
                        'text': row["text"],
                        'timestamp': row["timestamp"],
                    }))
                elif heap_key < heap[0][0]:
                    heapq.heapreplace(heap, (heap_key, {
                        'id': row["id"],
                        'app': row["app"],
                        'title': row["title"],
                        'text': row["text"],
                        'timestamp': row["timestamp"],
                    }))

    except sqlite3.Error as e:
        print(f"Database error during streaming search: {e}")

    # Sort results: best first (smallest heap_key = best)
    results = sorted(heap, key=lambda x: x[0])
    return [item[1] for item in results]


def get_entry_by_timestamp(timestamp: int) -> Optional[Entry]:
    """
    Retrieves a single entry by its timestamp.

    Args:
        timestamp (int): The timestamp of the entry to retrieve.

    Returns:
        Optional[Entry]: The entry as an Entry namedtuple, or None if not found.
    """
    try:
        with sqlite3.connect(db_path) as conn:
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
