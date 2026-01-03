"""SQLite database operations for storing bookmarks."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


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
                    urls TEXT
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

    def save_bookmark(self, bookmark: Bookmark) -> bool:
        """
        Save a bookmark to the database.

        Returns True if inserted, False if already exists.
        """
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO bookmarks (
                        tweet_id, author_id, author_username, author_name,
                        text, created_at, bookmark_saved_at, raw_json,
                        media_urls, urls
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> Iterator[Bookmark]:
        """
        Get bookmarks with optional filters.

        Args:
            limit: Maximum number of bookmarks to return
            since: Only return bookmarks saved after this datetime
            after_tweet_id: Only return bookmarks saved after this tweet
            author: Filter by author username
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
        )
