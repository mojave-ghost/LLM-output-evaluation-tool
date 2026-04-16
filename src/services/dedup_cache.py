import hashlib
import sqlite3
import uuid
from typing import Optional


class DedupCache:
    """In-memory hash → result_id map with SQLite backing store.

    Matches the DedupCache class from the UML class diagram.
    SHA-256 of response_text is used as the cache key.
    """

    def __init__(self, db_path: str = "dedup_cache.db") -> None:
        self._store: dict[str, uuid.UUID] = {}
        self.backing = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self._load_from_db()

    def _init_db(self) -> None:
        self.backing.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_cache (
                response_hash TEXT PRIMARY KEY,
                result_id     TEXT NOT NULL,
                cached_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.backing.commit()

    def _load_from_db(self) -> None:
        cursor = self.backing.execute("SELECT response_hash, result_id FROM dedup_cache")
        for row in cursor.fetchall():
            self._store[row[0]] = uuid.UUID(row[1])

    def get(self, hash: str) -> Optional[uuid.UUID]:
        """Return the cached result_id for the given hash, or None on miss."""
        return self._store.get(hash)

    def set(self, hash: str, result_id: uuid.UUID) -> None:
        """Store a hash → result_id mapping in memory and SQLite."""
        self._store[hash] = result_id
        self.backing.execute(
            "INSERT OR REPLACE INTO dedup_cache (response_hash, result_id) VALUES (?, ?)",
            (hash, str(result_id)),
        )
        self.backing.commit()

    @staticmethod
    def hash_response(text: str) -> str:
        """Return the SHA-256 hex digest of the given response text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
