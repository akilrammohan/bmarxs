"""Playwright-based scraper for X/Twitter bookmarks with GraphQL interception."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, Response, sync_playwright
from rich.console import Console

from .database import Bookmark, BookmarkDatabase

console = Console()

# Realistic browser context options to avoid detection
BROWSER_CONTEXT_OPTIONS = {
    "viewport": {"width": 1920, "height": 1080},
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "locale": "en-US",
    "timezone_id": "America/Los_Angeles",
}


class BookmarkScraper:
    """Scrapes X/Twitter bookmarks using Playwright and GraphQL interception."""

    BOOKMARKS_URL = "https://x.com/i/bookmarks"
    GRAPHQL_PATTERN = "Bookmarks"

    def __init__(
        self,
        db: BookmarkDatabase,
        session_path: Path,
        headless: bool = True,
    ):
        self.db = db
        self.session_path = session_path
        self.headless = headless
        self._bookmarks_data: list[dict] = []
        self._stop_scraping = False
        self._last_data_time = 0.0
        self._last_scroll_height = 0
        self._no_new_data_timeout = 2.0  # seconds (reduced from 5)
        self._scroll_check_iterations = 2  # (reduced from 3)
        self._new_bookmarks_count = 0
        self._duplicate_found = False

    def _handle_response(self, response: Response) -> None:
        """Handle GraphQL responses containing bookmark data."""
        if self._stop_scraping:
            return

        url = response.url
        if self.GRAPHQL_PATTERN in url and response.status == 200:
            try:
                data = response.json()
                self._last_data_time = time.time()
                self._process_bookmarks_response(data)
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to parse response: {e}[/yellow]")

    def _process_bookmarks_response(self, data: dict) -> None:
        """Extract and save bookmarks from GraphQL response."""
        try:
            # Navigate the nested response structure
            # Structure: data.bookmark_timeline_v2.timeline.instructions[].entries[]
            timeline = data.get("data", {}).get("bookmark_timeline_v2", {}).get("timeline", {})
            instructions = timeline.get("instructions", [])

            for instruction in instructions:
                if instruction.get("type") == "TimelineAddEntries":
                    entries = instruction.get("entries", [])
                    for entry in entries:
                        self._process_entry(entry)

        except Exception as e:
            console.print(f"[yellow]Warning: Error processing bookmark data: {e}[/yellow]")

    def _process_entry(self, entry: dict) -> None:
        """Process a single timeline entry."""
        entry_id = entry.get("entryId", "")

        # Skip cursor entries
        if entry_id.startswith("cursor-"):
            return

        content = entry.get("content", {})
        item_content = content.get("itemContent", {})
        tweet_results = item_content.get("tweet_results", {})
        result = tweet_results.get("result", {})

        # Handle different result types
        if result.get("__typename") == "TweetWithVisibilityResults":
            result = result.get("tweet", {})

        if not result or result.get("__typename") != "Tweet":
            return

        try:
            bookmark = self._parse_tweet(result, entry)

            # Check if we already have this bookmark (for incremental sync)
            if self.db.exists(bookmark.tweet_id):
                self._duplicate_found = True
                self._stop_scraping = True
                console.print(
                    f"[cyan]Found existing bookmark (tweet {bookmark.tweet_id}), stopping sync[/cyan]"
                )
                return

            # Save the bookmark
            if self.db.save_bookmark(bookmark):
                self._new_bookmarks_count += 1
                console.print(
                    f"[green]Saved bookmark {self._new_bookmarks_count}: "
                    f"@{bookmark.author_username}[/green]"
                )

        except Exception as e:
            console.print(f"[yellow]Warning: Failed to parse tweet: {e}[/yellow]")

    def _parse_tweet(self, result: dict, entry: dict) -> Bookmark:
        """Parse a tweet result into a Bookmark object."""
        legacy = result.get("legacy", {})
        core = result.get("core", {})
        user_results = core.get("user_results", {}).get("result", {})
        # User info can be in "core" or "legacy" depending on API version
        user_core = user_results.get("core", {})
        user_legacy = user_results.get("legacy", {})

        # Extract tweet ID from rest_id or entry_id
        tweet_id = result.get("rest_id") or entry.get("entryId", "").replace("tweet-", "")

        # Parse created_at
        created_at_str = legacy.get("created_at", "")
        if created_at_str:
            # Twitter format: "Sat Jan 01 00:00:00 +0000 2022"
            created_at = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
        else:
            created_at = datetime.now()

        # Extract media URLs
        media_urls = []
        extended_entities = legacy.get("extended_entities", {})
        for media in extended_entities.get("media", []):
            if media.get("type") == "photo":
                media_urls.append(media.get("media_url_https", ""))
            elif media.get("type") in ("video", "animated_gif"):
                variants = media.get("video_info", {}).get("variants", [])
                # Get highest bitrate video
                video_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
                if video_variants:
                    best = max(video_variants, key=lambda x: x.get("bitrate", 0))
                    media_urls.append(best.get("url", ""))

        # Extract URLs
        urls = []
        for url_entity in legacy.get("entities", {}).get("urls", []):
            expanded = url_entity.get("expanded_url", "")
            if expanded:
                urls.append(expanded)

        return Bookmark(
            tweet_id=tweet_id,
            author_id=user_results.get("rest_id", ""),
            author_username=user_core.get("screen_name") or user_legacy.get("screen_name", ""),
            author_name=user_core.get("name") or user_legacy.get("name", ""),
            text=legacy.get("full_text", ""),
            created_at=created_at,
            bookmark_saved_at=datetime.now(),
            raw_json=json.dumps(result),
            media_urls=media_urls if media_urls else None,
            urls=urls if urls else None,
        )

    def _session_exists(self) -> bool:
        """Check if a saved session exists."""
        state_file = self.session_path / "state.json"
        return state_file.exists()

    def _validate_session(self) -> bool:
        """Validate that the session has required auth cookies."""
        state_file = self.session_path / "state.json"
        if not state_file.exists():
            return False

        try:
            import json
            data = json.loads(state_file.read_text())
            cookies = data.get("cookies", [])
            cookie_names = {c.get("name") for c in cookies}
            return "auth_token" in cookie_names
        except Exception:
            return False

    def _scroll_page(self, page: Page) -> bool:
        """
        Scroll the page and check if we've reached the end.

        Returns True if more content might be available.
        """
        current_height = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)  # Wait for content to load

        new_height = page.evaluate("document.body.scrollHeight")
        height_changed = new_height > current_height

        # Check time since last data
        time_since_data = time.time() - self._last_data_time

        # Continue if height changed OR we recently got data
        return height_changed or time_since_data < self._no_new_data_timeout

    def sync(
        self,
        sync_all: bool = False,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        """
        Sync bookmarks from X/Twitter.

        Args:
            sync_all: If True, sync all bookmarks. If False, stop at first duplicate.
            on_progress: Optional callback for progress updates.

        Returns:
            Number of new bookmarks saved.
        """
        self._bookmarks_data = []
        self._stop_scraping = False
        self._new_bookmarks_count = 0
        self._duplicate_found = False
        self._last_data_time = time.time()

        # If not syncing all, we'll stop at the first duplicate
        if not sync_all:
            most_recent = self.db.get_most_recent_tweet_id()
            if most_recent:
                console.print(f"[dim]Will stop at tweet {most_recent}[/dim]")

        # Validate session exists before starting browser
        if not self._session_exists():
            raise RuntimeError(
                "No session found. Please run 'bmarxs import-cookies' first.\n"
                "1. Log into X/Twitter in Chrome\n"
                "2. Close Chrome completely\n"
                "3. Run: bmarxs import-cookies"
            )

        if not self._validate_session():
            raise RuntimeError(
                "Session is missing auth token. Please run 'bmarxs import-cookies' again.\n"
                "Make sure you're logged into X/Twitter in Chrome before importing."
            )

        with sync_playwright() as p:
            # Use real Chrome to avoid bot detection (not Chromium for Testing)
            browser = p.chromium.launch(
                headless=self.headless,
                channel="chrome",  # Use installed Chrome, not bundled Chromium
            )

            # Load saved session
            context = browser.new_context(
                storage_state=str(self.session_path / "state.json"),
                **BROWSER_CONTEXT_OPTIONS,
            )
            console.print("[dim]Loaded saved session[/dim]")

            page = context.new_page()
            page.on("response", self._handle_response)

            # Navigate to bookmarks
            console.print(f"[dim]Navigating to {self.BOOKMARKS_URL}[/dim]")
            page.goto(self.BOOKMARKS_URL)

            # Wait for page to load and check if we're redirected to login
            page.wait_for_timeout(3000)
            current_url = page.url

            if "login" in current_url or "flow" in current_url:
                browser.close()
                raise RuntimeError(
                    "Session expired or invalid. Please run 'bmarxs import-cookies' again.\n"
                    "1. Log into X/Twitter in Chrome\n"
                    "2. Close Chrome completely\n"
                    "3. Run: bmarxs import-cookies"
                )

            # Wait for bookmarks to load
            console.print("[dim]Waiting for bookmarks to load...[/dim]")
            page.wait_for_timeout(3000)

            # Scroll to load all bookmarks
            console.print("[dim]Scrolling to load bookmarks...[/dim]")
            no_change_count = 0

            while not self._stop_scraping:
                has_more = self._scroll_page(page)

                if on_progress:
                    on_progress(self._new_bookmarks_count)

                if not has_more:
                    no_change_count += 1
                    if no_change_count >= self._scroll_check_iterations:
                        console.print("[dim]Reached end of bookmarks[/dim]")
                        break
                else:
                    no_change_count = 0

                # Check if we've been idle too long
                if time.time() - self._last_data_time > self._no_new_data_timeout * 2:
                    console.print("[dim]No new data received, stopping[/dim]")
                    break

            browser.close()

        return self._new_bookmarks_count
