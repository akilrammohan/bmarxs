"""URL enrichment for bookmark metadata."""

import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console

from .database import BookmarkDatabase, UrlMetadata

console = Console()

# Skip these domains for enrichment (Twitter/X internal links)
SKIP_DOMAINS = {"twitter.com", "x.com", "t.co", "pic.twitter.com"}

# Request timeout
TIMEOUT = 10.0

# User agent for requests
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def should_enrich_url(url: str) -> bool:
    """Check if URL should be enriched (not a Twitter/X internal link)."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain not in SKIP_DOMAINS
    except Exception:
        return False


def extract_metadata(html: str, url: str) -> UrlMetadata:
    """Extract title and description from HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Try Open Graph tags first, then fall back to regular tags
    title = None
    description = None

    # Open Graph title
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]

    # Fall back to regular title
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

    # Open Graph description
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = og_desc["content"]

    # Fall back to meta description
    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"]

    return UrlMetadata(url=url, title=title, description=description)


def extract_page_text(html: str) -> str:
    """Extract main text content from HTML for summarization."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
        element.decompose()

    # Get text
    text = soup.get_text(separator=" ", strip=True)

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text)

    # Limit length for summarization
    return text[:10000]


def fetch_url_metadata(
    url: str,
    include_summary: bool = False,
) -> UrlMetadata | None:
    """
    Fetch metadata for a URL.

    Args:
        url: URL to fetch
        include_summary: If True, extract page text for summary field

    Returns:
        UrlMetadata or None if fetch failed
    """
    if not should_enrich_url(url):
        return None

    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()

            metadata = extract_metadata(response.text, url)

            if include_summary:
                page_text = extract_page_text(response.text)
                # Store the raw text as "summary" - an LLM can process this later
                metadata.summary = page_text[:2000]  # Truncate for storage

            return metadata

    except Exception as e:
        console.print(f"[dim]Could not fetch {url}: {e}[/dim]")
        return None


def enrich_bookmark(
    db: BookmarkDatabase,
    tweet_id: str,
    include_summary: bool = False,
) -> int:
    """
    Enrich a single bookmark's URLs with metadata.

    Returns number of URLs enriched.
    """
    bookmark = db.get_bookmark(tweet_id)
    if not bookmark or not bookmark.urls:
        return 0

    enriched_metadata = []
    for url in bookmark.urls:
        metadata = fetch_url_metadata(url, include_summary=include_summary)
        if metadata:
            enriched_metadata.append(metadata)

    if enriched_metadata:
        db.update_url_metadata(tweet_id, enriched_metadata)

    return len(enriched_metadata)


def enrich_all_bookmarks(
    db: BookmarkDatabase,
    include_summary: bool = False,
    only_unenriched: bool = True,
) -> int:
    """
    Enrich all bookmarks that have URLs.

    Args:
        db: Database instance
        include_summary: Include page text summary
        only_unenriched: Only enrich bookmarks without existing url_metadata

    Returns:
        Total number of URLs enriched
    """
    total_enriched = 0

    # Get all bookmarks with URLs
    for bookmark in db.get_all_bookmarks():
        if not bookmark.urls:
            continue

        # Skip if already enriched
        if only_unenriched and bookmark.url_metadata:
            continue

        count = enrich_bookmark(db, bookmark.tweet_id, include_summary=include_summary)
        if count > 0:
            console.print(f"[dim]Enriched {count} URL(s) for tweet {bookmark.tweet_id}[/dim]")
            total_enriched += count

    return total_enriched
