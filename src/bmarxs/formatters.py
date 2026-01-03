"""Output formatters for bookmarks (JSON, CSV, Markdown)."""

import csv
import json
from io import StringIO
from typing import Iterator

from .database import Bookmark


def format_json(bookmarks: Iterator[Bookmark], pretty: bool = True) -> str:
    """Format bookmarks as JSON."""
    bookmark_list = [b.to_dict() for b in bookmarks]
    if pretty:
        return json.dumps(bookmark_list, indent=2, ensure_ascii=False)
    return json.dumps(bookmark_list, ensure_ascii=False)


def format_csv(bookmarks: Iterator[Bookmark]) -> str:
    """Format bookmarks as CSV with all fields."""
    output = StringIO()
    writer = csv.writer(output)

    # Header - includes all fields
    writer.writerow([
        "tweet_id",
        "author_id",
        "author_username",
        "author_name",
        "text",
        "created_at",
        "bookmark_saved_at",
        "media_urls",
        "urls",
        "processed",
        "processed_at",
        "url_metadata",
    ])

    # Data rows
    for bookmark in bookmarks:
        # Serialize url_metadata as JSON string for CSV
        url_metadata_str = ""
        if bookmark.url_metadata:
            url_metadata_str = json.dumps([
                {"url": m.url, "title": m.title, "description": m.description, "summary": m.summary}
                for m in bookmark.url_metadata
            ])

        writer.writerow([
            bookmark.tweet_id,
            bookmark.author_id,
            bookmark.author_username,
            bookmark.author_name,
            bookmark.text.replace("\n", " "),  # Flatten newlines for CSV
            bookmark.created_at.isoformat(),
            bookmark.bookmark_saved_at.isoformat(),
            "|".join(bookmark.media_urls) if bookmark.media_urls else "",
            "|".join(bookmark.urls) if bookmark.urls else "",
            "true" if bookmark.processed else "false",
            bookmark.processed_at.isoformat() if bookmark.processed_at else "",
            url_metadata_str,
        ])

    return output.getvalue()


def format_markdown(bookmarks: Iterator[Bookmark]) -> str:
    """Format bookmarks as Markdown with all fields."""
    lines = ["# X/Twitter Bookmarks\n"]

    for bookmark in bookmarks:
        lines.append(f"## @{bookmark.author_username} ({bookmark.author_name})\n")
        lines.append(f"**Tweet ID:** {bookmark.tweet_id}  ")
        lines.append(f"**Created:** {bookmark.created_at.strftime('%Y-%m-%d %H:%M')}  ")
        lines.append(f"**Bookmarked:** {bookmark.bookmark_saved_at.strftime('%Y-%m-%d %H:%M')}  ")

        # Processing status
        if bookmark.processed:
            processed_at_str = bookmark.processed_at.strftime('%Y-%m-%d %H:%M') if bookmark.processed_at else "unknown"
            lines.append(f"**Status:** Processed ({processed_at_str})\n")
        else:
            lines.append(f"**Status:** Unprocessed\n")

        lines.append(f"\n{bookmark.text}\n")

        if bookmark.media_urls:
            lines.append("\n**Media:**\n")
            for url in bookmark.media_urls:
                lines.append(f"- {url}\n")

        if bookmark.urls:
            lines.append("\n**Links:**\n")
            for url in bookmark.urls:
                lines.append(f"- {url}\n")

        # Enriched URL metadata
        if bookmark.url_metadata:
            lines.append("\n**Enriched URL Data:**\n")
            for meta in bookmark.url_metadata:
                lines.append(f"- **{meta.url}**\n")
                if meta.title:
                    lines.append(f"  - Title: {meta.title}\n")
                if meta.description:
                    lines.append(f"  - Description: {meta.description}\n")
                if meta.summary:
                    lines.append(f"  - Summary: {meta.summary[:200]}{'...' if len(meta.summary) > 200 else ''}\n")

        lines.append(f"\n[View on X](https://x.com/{bookmark.author_username}/status/{bookmark.tweet_id})\n")
        lines.append("\n---\n\n")

    return "".join(lines)


def format_bookmarks(
    bookmarks: Iterator[Bookmark],
    format_type: str,
) -> str:
    """Format bookmarks in the specified format."""
    # Convert iterator to list since we may need to iterate multiple times
    # or the iterator might be exhausted
    bookmark_list = list(bookmarks)

    if format_type == "json":
        return format_json(iter(bookmark_list))
    elif format_type == "csv":
        return format_csv(iter(bookmark_list))
    elif format_type == "md" or format_type == "markdown":
        return format_markdown(iter(bookmark_list))
    else:
        raise ValueError(f"Unknown format: {format_type}")
