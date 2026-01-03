"""CLI interface for bmarxs."""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import browser_cookie3
import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .database import BookmarkDatabase
from .errors import (
    AuthError,
    BrowserError,
    CLIError,
    DatabaseError,
    ExitCode,
    NotFoundError,
)
from .formatters import format_bookmarks

console = Console()


class CLIContext:
    """Context object passed through CLI commands."""

    def __init__(self, data_dir: Path, quiet: bool = False, json_output: bool = False):
        self.data_dir = data_dir
        self.quiet = quiet
        self.json_output = json_output

    def print(self, message: str, style: str | None = None) -> None:
        """Print message unless in quiet mode."""
        if not self.quiet and not self.json_output:
            if style:
                console.print(f"[{style}]{message}[/{style}]")
            else:
                console.print(message)

    def print_info(self, message: str) -> None:
        """Print info message."""
        self.print(message, "dim")

    def print_success(self, message: str) -> None:
        """Print success message."""
        self.print(message, "bold green")

    def print_warning(self, message: str) -> None:
        """Print warning message."""
        self.print(message, "yellow")

    def print_error(self, message: str) -> None:
        """Print error message."""
        self.print(message, "bold red")

    def output_json(self, data: dict[str, Any]) -> None:
        """Output JSON to stdout."""
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))

    def output_result(
        self,
        success: bool = True,
        data: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        """Output structured result (for --json mode)."""
        if self.json_output:
            result: dict[str, Any] = {"success": success}
            if message:
                result["message"] = message
            if data:
                result.update(data)
            self.output_json(result)


def extract_x_cookies_from_chrome(ctx: CLIContext) -> list[dict]:
    """
    Extract X/Twitter cookies from Chrome browser.

    Returns list of cookies in Playwright format.
    Chrome must be closed for this to work.
    """
    playwright_cookies = []

    for domain in [".x.com", ".twitter.com"]:
        try:
            cj = browser_cookie3.chrome(domain_name=domain)
            for cookie in cj:
                playwright_cookies.append({
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": bool(cookie.secure),
                    "httpOnly": bool(cookie.has_nonstandard_attr("HttpOnly")),
                    "sameSite": "None",
                    "expires": cookie.expires if cookie.expires else -1,
                })
        except Exception as e:
            ctx.print_warning(f"Warning: Could not read cookies for {domain}: {e}")

    return playwright_cookies


def validate_x_cookies(cookies: list[dict]) -> bool:
    """Check if essential X auth cookies are present."""
    cookie_names = {c["name"] for c in cookies}
    return "auth_token" in cookie_names


DEFAULT_DATA_DIR = Path("./data")


def get_db(data_dir: Path) -> BookmarkDatabase:
    """Get database instance."""
    return BookmarkDatabase(data_dir / "bookmarks.db")


def get_session_path(data_dir: Path) -> Path:
    """Get session storage path."""
    return data_dir / "session"


def handle_error(ctx: CLIContext, error: CLIError) -> None:
    """Handle a CLI error with proper output and exit code."""
    if ctx.json_output:
        ctx.output_json(error.to_dict())
    else:
        ctx.print_error(error.message)
        if error.details:
            for key, value in error.details.items():
                ctx.print_info(f"  {key}: {value}")
    sys.exit(error.code)


pass_context = click.make_pass_decorator(CLIContext)


@click.group()
@click.version_option(version=__version__)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    help="Data directory for database and session storage",
)
@click.option(
    "--quiet", "-q",
    is_flag=True,
    help="Suppress non-essential output (progress, info messages)",
)
@click.option(
    "--json", "-j", "json_output",
    is_flag=True,
    help="Output results as JSON (for agent/programmatic use)",
)
@click.pass_context
def main(ctx: click.Context, data_dir: Path, quiet: bool, json_output: bool) -> None:
    """bmarxs - Export and manage X/Twitter bookmarks."""
    ctx.obj = CLIContext(data_dir=data_dir, quiet=quiet, json_output=json_output)


@main.command()
@click.option("--all", "sync_all", is_flag=True, help="Sync all bookmarks (not just new)")
@click.option("--visible", is_flag=True, help="Show browser window (default: headless)")
@click.option("--enrich", is_flag=True, help="Fetch URL titles and descriptions")
@click.option("--enrich-summary", is_flag=True, help="Also extract page text for summaries (implies --enrich)")
@pass_context
def sync(ctx: CLIContext, sync_all: bool, visible: bool, enrich: bool, enrich_summary: bool) -> None:
    """Sync bookmarks from X/Twitter."""
    from .enricher import enrich_all_bookmarks
    from .scraper import BookmarkScraper

    db = get_db(ctx.data_dir)
    session_path = get_session_path(ctx.data_dir)

    ctx.print("Syncing bookmarks...", "bold")

    if sync_all:
        ctx.print_warning("Syncing ALL bookmarks (this may take a while)")
    else:
        ctx.print_info("Syncing new bookmarks only (will stop at first duplicate)")

    scraper = BookmarkScraper(
        db=db,
        session_path=session_path,
        headless=not visible,
    )

    try:
        count = scraper.sync(sync_all=sync_all)
        ctx.print_success(f"Synced {count} new bookmarks!")

        enriched_count = 0
        if enrich or enrich_summary:
            ctx.print("Enriching URLs...", "bold")
            enriched_count = enrich_all_bookmarks(
                db,
                include_summary=enrich_summary,
                only_unenriched=True,
            )
            ctx.print_success(f"Enriched {enriched_count} URLs")

        ctx.output_result(
            success=True,
            data={
                "synced_count": count,
                "enriched_count": enriched_count,
                "sync_all": sync_all,
            },
            message=f"Synced {count} new bookmarks",
        )

    except Exception as e:
        error_msg = str(e)
        if "auth" in error_msg.lower() or "login" in error_msg.lower():
            handle_error(ctx, AuthError("Authentication failed", {"original_error": error_msg}))
        elif "network" in error_msg.lower() or "connection" in error_msg.lower():
            from .errors import NetworkError
            handle_error(ctx, NetworkError("Network error during sync", {"original_error": error_msg}))
        else:
            handle_error(ctx, BrowserError(f"Error during sync: {error_msg}"))


@main.command()
@click.option("--since", type=click.DateTime(), help="Only show bookmarks saved after this date")
@click.option("--after-tweet", type=str, help="Only show bookmarks saved after this tweet ID")
@click.option("--author", type=str, help="Filter by author username")
@click.option("--limit", type=int, help="Maximum number of bookmarks to show")
@click.option("--unprocessed", is_flag=True, help="Only show unprocessed bookmarks")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "md"]),
    default="table",
    help="Output format (default: table)",
)
@pass_context
def list(
    ctx: CLIContext,
    since: datetime | None,
    after_tweet: str | None,
    author: str | None,
    limit: int | None,
    unprocessed: bool,
    output_format: str,
) -> None:
    """List bookmarks from the database."""
    db = get_db(ctx.data_dir)

    # Global --json flag overrides format
    if ctx.json_output:
        output_format = "json"

    bookmarks = db.get_all_bookmarks(
        limit=limit,
        since=since,
        after_tweet_id=after_tweet,
        author=author,
        unprocessed=unprocessed,
    )

    if output_format == "table":
        if ctx.quiet:
            return  # No output in quiet mode for table format

        table = Table(show_header=True, header_style="bold")
        table.add_column("Author", style="cyan")
        table.add_column("Text", max_width=60)
        table.add_column("Created", style="dim")
        table.add_column("Tweet ID", style="dim")
        table.add_column("Processed", style="dim")

        count = 0
        for bookmark in bookmarks:
            text = bookmark.text[:57] + "..." if len(bookmark.text) > 60 else bookmark.text
            text = text.replace("\n", " ")
            table.add_row(
                f"@{bookmark.author_username}",
                text,
                bookmark.created_at.strftime("%Y-%m-%d"),
                bookmark.tweet_id,
                "✓" if bookmark.processed else "",
            )
            count += 1

        console.print(table)
        ctx.print_info(f"Showing {count} bookmarks")
    else:
        # Re-fetch since iterator was consumed
        bookmarks = db.get_all_bookmarks(
            limit=limit,
            since=since,
            after_tweet_id=after_tweet,
            author=author,
            unprocessed=unprocessed,
        )
        output = format_bookmarks(bookmarks, output_format)
        click.echo(output)


@main.command()
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "csv", "md"]),
    default="json",
    help="Export format (default: json)",
)
@click.option("--since", type=click.DateTime(), help="Only export bookmarks saved after this date")
@click.option("--author", type=str, help="Filter by author username")
@click.option("--unprocessed", is_flag=True, help="Only export unprocessed bookmarks")
@pass_context
def export(
    ctx: CLIContext,
    output_format: str,
    since: datetime | None,
    author: str | None,
    unprocessed: bool,
) -> None:
    """Export bookmarks to stdout."""
    db = get_db(ctx.data_dir)

    bookmarks = db.get_all_bookmarks(since=since, author=author, unprocessed=unprocessed)
    formatted = format_bookmarks(bookmarks, output_format)
    click.echo(formatted)


@main.command()
@pass_context
def stats(ctx: CLIContext) -> None:
    """Show bookmark statistics."""
    db = get_db(ctx.data_dir)

    stats_data = db.get_stats()

    if ctx.json_output:
        ctx.output_result(success=True, data=stats_data)
    elif not ctx.quiet:
        console.print("\n[bold]Bookmark Statistics[/bold]\n")
        console.print(f"Total bookmarks: [cyan]{stats_data['total_bookmarks']}[/cyan]")

        if stats_data["oldest_bookmark"]:
            console.print(f"Oldest bookmark: [dim]{stats_data['oldest_bookmark']}[/dim]")
            console.print(f"Newest bookmark: [dim]{stats_data['newest_bookmark']}[/dim]")

        if stats_data["top_authors"]:
            console.print("\n[bold]Top Authors:[/bold]")
            table = Table(show_header=True, header_style="bold")
            table.add_column("Author", style="cyan")
            table.add_column("Bookmarks", justify="right")

            for author_stat in stats_data["top_authors"]:
                table.add_row(f"@{author_stat['username']}", str(author_stat["count"]))

            console.print(table)


@main.command()
@click.option("--summary", is_flag=True, help="Also extract page text for summaries")
@click.option("--force", is_flag=True, help="Re-enrich all URLs, even already enriched ones")
@pass_context
def enrich(ctx: CLIContext, summary: bool, force: bool) -> None:
    """Enrich bookmark URLs with titles and descriptions."""
    from .enricher import enrich_all_bookmarks

    db = get_db(ctx.data_dir)

    ctx.print("Enriching URLs...", "bold")
    if summary:
        ctx.print_info("Including page text summaries")

    try:
        enriched = enrich_all_bookmarks(
            db,
            include_summary=summary,
            only_unenriched=not force,
        )

        ctx.print_success(f"Enriched {enriched} URLs")
        ctx.output_result(
            success=True,
            data={"enriched_count": enriched, "include_summary": summary, "force": force},
            message=f"Enriched {enriched} URLs",
        )
    except Exception as e:
        from .errors import NetworkError
        handle_error(ctx, NetworkError(f"Error enriching URLs: {e}"))


@main.command()
@click.argument("query")
@click.option("--limit", type=int, help="Maximum number of results")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format (default: table)",
)
@pass_context
def search(ctx: CLIContext, query: str, limit: int | None, output_format: str) -> None:
    """Full-text search across bookmarks."""
    db = get_db(ctx.data_dir)

    # Global --json flag overrides format
    if ctx.json_output:
        output_format = "json"

    results = db.search(query, limit=limit)

    if output_format == "table":
        if ctx.quiet:
            return  # No output in quiet mode for table format

        table = Table(show_header=True, header_style="bold")
        table.add_column("Author", style="cyan")
        table.add_column("Text", max_width=60)
        table.add_column("Created", style="dim")
        table.add_column("Tweet ID", style="dim")

        count = 0
        for bookmark in results:
            text = bookmark.text[:57] + "..." if len(bookmark.text) > 60 else bookmark.text
            text = text.replace("\n", " ")
            table.add_row(
                f"@{bookmark.author_username}",
                text,
                bookmark.created_at.strftime("%Y-%m-%d"),
                bookmark.tweet_id,
            )
            count += 1

        console.print(table)
        ctx.print_info(f"Found {count} results for '{query}'")
    else:
        results = db.search(query, limit=limit)
        bookmark_list = [b.to_dict() for b in results]
        click.echo(json.dumps(bookmark_list, indent=2))


@main.command("mark-processed")
@click.argument("tweet_ids", nargs=-1, required=True)
@pass_context
def mark_processed(ctx: CLIContext, tweet_ids: tuple[str, ...]) -> None:
    """Mark one or more bookmarks as processed."""
    db = get_db(ctx.data_dir)

    results: list[dict[str, Any]] = []
    success_count = 0
    failed_ids: list[str] = []

    for tweet_id in tweet_ids:
        if db.mark_processed(tweet_id):
            success_count += 1
            results.append({"tweet_id": tweet_id, "status": "processed"})
        else:
            failed_ids.append(tweet_id)
            results.append({"tweet_id": tweet_id, "status": "not_found"})
            ctx.print_warning(f"Warning: Bookmark {tweet_id} not found")

    ctx.print_success(f"Marked {success_count} bookmark(s) as processed")

    if ctx.json_output:
        ctx.output_json({
            "success": len(failed_ids) == 0,
            "processed_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
            "results": results,
        })

    if failed_ids:
        sys.exit(ExitCode.NOT_FOUND)


@main.command("mark-unprocessed")
@click.argument("tweet_ids", nargs=-1, required=True)
@pass_context
def mark_unprocessed(ctx: CLIContext, tweet_ids: tuple[str, ...]) -> None:
    """Mark one or more bookmarks as unprocessed."""
    db = get_db(ctx.data_dir)

    results: list[dict[str, Any]] = []
    success_count = 0
    failed_ids: list[str] = []

    for tweet_id in tweet_ids:
        if db.mark_unprocessed(tweet_id):
            success_count += 1
            results.append({"tweet_id": tweet_id, "status": "unprocessed"})
        else:
            failed_ids.append(tweet_id)
            results.append({"tweet_id": tweet_id, "status": "not_found"})
            ctx.print_warning(f"Warning: Bookmark {tweet_id} not found")

    ctx.print_success(f"Marked {success_count} bookmark(s) as unprocessed")

    if ctx.json_output:
        ctx.output_json({
            "success": len(failed_ids) == 0,
            "unprocessed_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
            "results": results,
        })

    if failed_ids:
        sys.exit(ExitCode.NOT_FOUND)


@main.command("import-cookies")
@pass_context
def import_cookies(ctx: CLIContext) -> None:
    """Import cookies from Chrome browser (must be logged into X, Chrome must be closed)."""
    session_path = get_session_path(ctx.data_dir)

    ctx.print("Importing cookies from Chrome...", "bold")
    ctx.print_info("Make sure Chrome is closed and you're logged into X/Twitter.")

    try:
        cookies = extract_x_cookies_from_chrome(ctx)
    except Exception as e:
        handle_error(ctx, BrowserError(
            "Error reading cookies from Chrome",
            {"hint": "Make sure Chrome is completely closed and try again", "original_error": str(e)},
        ))
        return  # unreachable, but helps type checker

    if not cookies:
        handle_error(ctx, AuthError(
            "No X/Twitter cookies found in Chrome",
            {"hint": "Please log into X/Twitter in Chrome first, then close Chrome and try again"},
        ))
        return

    if not validate_x_cookies(cookies):
        handle_error(ctx, AuthError(
            "Auth token not found - you may not be logged into X/Twitter",
            {"hint": "Please log into X/Twitter in Chrome first, then close Chrome and try again"},
        ))
        return

    storage_state = {
        "cookies": cookies,
        "origins": [],
    }

    session_path.mkdir(parents=True, exist_ok=True)
    state_file = session_path / "state.json"
    state_file.write_text(json.dumps(storage_state, indent=2))

    ctx.print_success(f"Imported {len(cookies)} cookies!")
    ctx.print_info(f"Saved to {state_file}")
    ctx.print("You can now run: bmarxs sync", "bold")

    ctx.output_result(
        success=True,
        data={
            "cookie_count": len(cookies),
            "session_file": str(state_file),
        },
        message=f"Imported {len(cookies)} cookies",
    )


@main.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Import cookies from Chrome (alias for import-cookies)."""
    ctx.invoke(import_cookies)


if __name__ == "__main__":
    main()
