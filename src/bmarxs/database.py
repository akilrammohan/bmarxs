"""SQLite database operations for storing bookmarks."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass
class UrlMetadata:
    """Metadata for an enriched URL."""

    url: str
    title: str | None = None
    description: str | None = None
    summary: str | None = None


@dataclass
class Bookmark:
    """Represents a bookmarked tweet."""

    tweet_id: str
    author_id: str
    author_username: str
    author_name: str
    text: str
    created_at: datetime
    bookmark_saved_at: datetime
    raw_json: str
    media_urls: list[str] | None = None
    urls: list[str] | None = None
    # Processing state
    processed: bool = False
    processed_at: datetime | None = None
    # Enriched URL metadata
    url_metadata: list[UrlMetadata] | None = None

    def to_dict(self) -> dict:
        """Convert bookmark to dictionary."""
        return {
            "tweet_id": self.tweet_id,
            "author_id": self.author_id,
            "author_username": self.author_username,
            "author_name": self.author_name,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
            "bookmark_saved_at": self.bookmark_saved_at.isoformat(),
            "media_urls": self.media_urls,
            "urls": self.urls,
            "processed": self.processed,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "url_metadata": [
                {"url": m.url, "title": m.title, "description": m.description, "summary": m.summary}
                for m in self.url_metadata
            ] if self.url_metadata else None,
        }


class BookmarkDatabase:
    """SQLite database for bookmark storage."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self) -> None:
        """Create database and tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bookmarks (
                    tweet_id TEXT PRIMARY KEY,
                    author_id TEXT NOT NULL,
                    author_username TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    bookmark_saved_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    media_urls TEXT,
                    urls TEXT,
                    processed INTEGER DEFAULT 0,
                    processed_at TEXT,
                    url_metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bookmark_saved_at
                ON bookmarks(bookmark_saved_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_author_username
                ON bookmarks(author_username)
            """)
            conn.commit()

        # Run migrations for existing databases (adds new columns)
        self._migrate_db()

        # Create indexes on new columns after migration
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed
                ON bookmarks(processed)
            """)

            # FTS5 full-text search table
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
                    tweet_id,
                    text,
                    author_username,
                    author_name,
                    content='bookmarks',
                    content_rowid='rowid'
                )
            """)

            # Triggers to keep FTS in sync
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS bookmarks_ai AFTER INSERT ON bookmarks BEGIN
                    INSERT INTO bookmarks_fts(rowid, tweet_id, text, author_username, author_name)
                    VALUES (NEW.rowid, NEW.tweet_id, NEW.text, NEW.author_username, NEW.author_name);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS bookmarks_ad AFTER DELETE ON bookmarks BEGIN
                    INSERT INTO bookmarks_fts(bookmarks_fts, rowid, tweet_id, text, author_username, author_name)
                    VALUES ('delete', OLD.rowid, OLD.tweet_id, OLD.text, OLD.author_username, OLD.author_name);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS bookmarks_au AFTER UPDATE ON bookmarks BEGIN
                    INSERT INTO bookmarks_fts(bookmarks_fts, rowid, tweet_id, text, author_username, author_name)
                    VALUES ('delete', OLD.rowid, OLD.tweet_id, OLD.text, OLD.author_username, OLD.author_name);
                    INSERT INTO bookmarks_fts(rowid, tweet_id, text, author_username, author_name)
                    VALUES (NEW.rowid, NEW.tweet_id, NEW.text, NEW.author_username, NEW.author_name);
                END
            """)

            conn.commit()

            # Rebuild FTS index for existing data
            try:
                conn.execute("INSERT INTO bookmarks_fts(bookmarks_fts) VALUES('rebuild')")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # FTS table might be empty

    def _migrate_db(self) -> None:
        """Add new columns to existing databases."""
        with sqlite3.connect(self.db_path) as conn:
            # Get existing columns
            cursor = conn.execute("PRAGMA table_info(bookmarks)")
            existing_columns = {row[1] for row in cursor.fetchall()}

            # Add missing columns
            if "processed" not in existing_columns:
                conn.execute("ALTER TABLE bookmarks ADD COLUMN processed INTEGER DEFAULT 0")
            if "processed_at" not in existing_columns:
                conn.execute("ALTER TABLE bookmarks ADD COLUMN processed_at TEXT")
            if "url_metadata" not in existing_columns:
                conn.execute("ALTER TABLE bookmarks ADD COLUMN url_metadata TEXT")

            conn.commit()

    def save_bookmark(self, bookmark: Bookmark) -> bool:
        """
        Save a bookmark to the database.

        Returns True if inserted, False if already exists.
        """
        with sqlite3.connect(self.db_path) as conn:
            try:
                url_metadata_json = None
                if bookmark.url_metadata:
                    url_metadata_json = json.dumps([
                        {"url": m.url, "title": m.title, "description": m.description, "summary": m.summary}
                        for m in bookmark.url_metadata
                    ])

                conn.execute(
                    """
                    INSERT INTO bookmarks (
                        tweet_id, author_id, author_username, author_name,
                        text, created_at, bookmark_saved_at, raw_json,
                        media_urls, urls, processed, processed_at, url_metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bookmark.tweet_id,
                        bookmark.author_id,
                        bookmark.author_username,
                        bookmark.author_name,
                        bookmark.text,
                        bookmark.created_at.isoformat(),
                        bookmark.bookmark_saved_at.isoformat(),
                        bookmark.raw_json,
                        json.dumps(bookmark.media_urls) if bookmark.media_urls else None,
                        json.dumps(bookmark.urls) if bookmark.urls else None,
                        1 if bookmark.processed else 0,
                        bookmark.processed_at.isoformat() if bookmark.processed_at else None,
                        url_metadata_json,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def exists(self, tweet_id: str) -> bool:
        """Check if a bookmark exists in the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM bookmarks WHERE tweet_id = ?",
                (tweet_id,),
            )
            return cursor.fetchone() is not None

    def get_most_recent_tweet_id(self) -> str | None:
        """Get the most recently saved bookmark's tweet ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT tweet_id FROM bookmarks ORDER BY bookmark_saved_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def get_bookmark(self, tweet_id: str) -> Bookmark | None:
        """Get a single bookmark by tweet ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM bookmarks WHERE tweet_id = ?",
                (tweet_id,),
            )
            row = cursor.fetchone()
            return self._row_to_bookmark(row) if row else None

    def get_all_bookmarks(
        self,
        limit: int | None = None,
        since: datetime | None = None,
        after_tweet_id: str | None = None,
        author: str | None = None,
        unprocessed: bool = False,
    ) -> Iterator[Bookmark]:
        """
        Get bookmarks with optional filters.

        Args:
            limit: Maximum number of bookmarks to return
            since: Only return bookmarks saved after this datetime
            after_tweet_id: Only return bookmarks saved after this tweet
            author: Filter by author username
            unprocessed: Only return unprocessed bookmarks
        """
        conditions = []
        params: list = []

        if since:
            conditions.append("bookmark_saved_at > ?")
            params.append(since.isoformat())

        if after_tweet_id:
            # Get the bookmark_saved_at for this tweet
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT bookmark_saved_at FROM bookmarks WHERE tweet_id = ?",
                    (after_tweet_id,),
                )
                row = cursor.fetchone()
                if row:
                    conditions.append("bookmark_saved_at > ?")
                    params.append(row[0])

        if author:
            conditions.append("LOWER(author_username) = LOWER(?)")
            params.append(author)

        if unprocessed:
            conditions.append("(processed = 0 OR processed IS NULL)")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM bookmarks WHERE {where_clause} ORDER BY bookmark_saved_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            for row in cursor:
                yield self._row_to_bookmark(row)

    def count(self) -> int:
        """Get total number of bookmarks."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM bookmarks")
            return cursor.fetchone()[0]

    def get_stats(self) -> dict:
        """Get statistics about the bookmarks."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Total count
            total = conn.execute("SELECT COUNT(*) as count FROM bookmarks").fetchone()["count"]

            # Date range
            dates = conn.execute("""
                SELECT
                    MIN(bookmark_saved_at) as oldest,
                    MAX(bookmark_saved_at) as newest
                FROM bookmarks
            """).fetchone()

            # Top authors
            top_authors = conn.execute("""
                SELECT author_username, COUNT(*) as count
                FROM bookmarks
                GROUP BY author_username
                ORDER BY count DESC
                LIMIT 10
            """).fetchall()

            return {
                "total_bookmarks": total,
                "oldest_bookmark": dates["oldest"],
                "newest_bookmark": dates["newest"],
                "top_authors": [
                    {"username": row["author_username"], "count": row["count"]}
                    for row in top_authors
                ],
            }

    def _row_to_bookmark(self, row: sqlite3.Row) -> Bookmark:
        """Convert a database row to a Bookmark object."""
        # Parse url_metadata if present
        url_metadata = None
        if row["url_metadata"]:
            url_metadata_raw = json.loads(row["url_metadata"])
            url_metadata = [
                UrlMetadata(
                    url=m["url"],
                    title=m.get("title"),
                    description=m.get("description"),
                    summary=m.get("summary"),
                )
                for m in url_metadata_raw
            ]

        return Bookmark(
            tweet_id=row["tweet_id"],
            author_id=row["author_id"],
            author_username=row["author_username"],
            author_name=row["author_name"],
            text=row["text"],
            created_at=datetime.fromisoformat(row["created_at"]),
            bookmark_saved_at=datetime.fromisoformat(row["bookmark_saved_at"]),
            raw_json=row["raw_json"],
            media_urls=json.loads(row["media_urls"]) if row["media_urls"] else None,
            urls=json.loads(row["urls"]) if row["urls"] else None,
            processed=bool(row["processed"]) if row["processed"] is not None else False,
            processed_at=datetime.fromisoformat(row["processed_at"]) if row["processed_at"] else None,
            url_metadata=url_metadata,
        )

    def mark_processed(self, tweet_id: str) -> bool:
        """
        Mark a bookmark as processed.

        Returns True if updated, False if bookmark not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE bookmarks
                SET processed = 1, processed_at = ?
                WHERE tweet_id = ?
                """,
                (datetime.now().isoformat(), tweet_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_unprocessed(self, tweet_id: str) -> bool:
        """
        Mark a bookmark as unprocessed.

        Returns True if updated, False if bookmark not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE bookmarks
                SET processed = 0, processed_at = NULL
                WHERE tweet_id = ?
                """,
                (tweet_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def search(self, query: str, limit: int | None = None) -> Iterator[Bookmark]:
        """
        Full-text search across tweet text and author fields.

        Args:
            query: Search query (supports FTS5 syntax)
            limit: Maximum number of results
        """
        sql = """
            SELECT b.*
            FROM bookmarks b
            JOIN bookmarks_fts fts ON b.tweet_id = fts.tweet_id
            WHERE bookmarks_fts MATCH ?
            ORDER BY rank
        """
        if limit:
            sql += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, (query,))
            for row in cursor:
                yield self._row_to_bookmark(row)

    def update_url_metadata(self, tweet_id: str, url_metadata: list[UrlMetadata]) -> bool:
        """
        Update URL metadata for a bookmark.

        Returns True if updated, False if bookmark not found.
        """
        url_metadata_json = json.dumps([
            {"url": m.url, "title": m.title, "description": m.description, "summary": m.summary}
            for m in url_metadata
        ])

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE bookmarks SET url_metadata = ? WHERE tweet_id = ?",
                (url_metadata_json, tweet_id),
            )
            conn.commit()
            return cursor.rowcount > 0
