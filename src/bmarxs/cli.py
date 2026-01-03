"""CLI interface for bmarxs."""

import json
from datetime import datetime
from pathlib import Path

import browser_cookie3
import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .database import BookmarkDatabase
from .formatters import format_bookmarks
from .scraper import BookmarkScraper

console = Console()


def extract_x_cookies_from_chrome() -> list[dict]:
    """
    Extract X/Twitter cookies from Chrome browser.

    Returns list of cookies in Playwright format.
    Chrome must be closed for this to work.
    """
    playwright_cookies = []

    # Get cookies for both x.com and twitter.com domains
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
            console.print(f"[yellow]Warning: Could not read cookies for {domain}: {e}[/yellow]")

    return playwright_cookies


def validate_x_cookies(cookies: list[dict]) -> bool:
    """Check if essential X auth cookies are present."""
    cookie_names = {c["name"] for c in cookies}
    # auth_token is the main session cookie
    return "auth_token" in cookie_names

# Default data directory (current directory)
DEFAULT_DATA_DIR = Path("./data")


def get_db(data_dir: Path) -> BookmarkDatabase:
    """Get database instance."""
    return BookmarkDatabase(data_dir / "bookmarks.db")


def get_session_path(data_dir: Path) -> Path:
    """Get session storage path."""
    return data_dir / "session"


@click.group()
@click.version_option(version=__version__)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    help="Data directory for database and session storage",
)
@click.pass_context
def main(ctx: click.Context, data_dir: Path) -> None:
    """bmarxs - Export and manage X/Twitter bookmarks."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = data_dir


@main.command()
@click.option("--all", "sync_all", is_flag=True, help="Sync all bookmarks (not just new)")
@click.option("--visible", is_flag=True, help="Show browser window (default: headless)")
@click.pass_context
def sync(ctx: click.Context, sync_all: bool, visible: bool) -> None:
    """Sync bookmarks from X/Twitter."""
    data_dir = ctx.obj["data_dir"]
    db = get_db(data_dir)
    session_path = get_session_path(data_dir)

    console.print(f"[bold]Syncing bookmarks...[/bold]")

    if sync_all:
        console.print("[yellow]Syncing ALL bookmarks (this may take a while)[/yellow]")
    else:
        console.print("[dim]Syncing new bookmarks only (will stop at first duplicate)[/dim]")

    scraper = BookmarkScraper(
        db=db,
        session_path=session_path,
        headless=not visible,
    )

    try:
        count = scraper.sync(sync_all=sync_all)
        console.print(f"\n[bold green]Synced {count} new bookmarks![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Error during sync: {e}[/bold red]")
        raise click.Abort()


@main.command()
@click.option("--since", type=click.DateTime(), help="Only show bookmarks saved after this date")
@click.option("--after-tweet", type=str, help="Only show bookmarks saved after this tweet ID")
@click.option("--author", type=str, help="Filter by author username")
@click.option("--limit", type=int, help="Maximum number of bookmarks to show")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv", "md"]),
    default="table",
    help="Output format",
)
@click.pass_context
def list(
    ctx: click.Context,
    since: datetime | None,
    after_tweet: str | None,
    author: str | None,
    limit: int | None,
    output_format: str,
) -> None:
    """List bookmarks from the database."""
    data_dir = ctx.obj["data_dir"]
    db = get_db(data_dir)

    bookmarks = db.get_all_bookmarks(
        limit=limit,
        since=since,
        after_tweet_id=after_tweet,
        author=author,
    )

    if output_format == "table":
        table = Table(show_header=True, header_style="bold")
        table.add_column("Author", style="cyan")
        table.add_column("Text", max_width=60)
        table.add_column("Created", style="dim")
        table.add_column("Tweet ID", style="dim")

        count = 0
        for bookmark in bookmarks:
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
        console.print(f"\n[dim]Showing {count} bookmarks[/dim]")
    else:
        # Re-fetch since iterator was consumed
        bookmarks = db.get_all_bookmarks(
            limit=limit,
            since=since,
            after_tweet_id=after_tweet,
            author=author,
        )
        output = format_bookmarks(bookmarks, output_format)
        click.echo(output)


@main.command()
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "csv", "md"]),
    default="json",
    help="Export format",
)
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output file path")
@click.option("--since", type=click.DateTime(), help="Only export bookmarks saved after this date")
@click.option("--author", type=str, help="Filter by author username")
@click.pass_context
def export(
    ctx: click.Context,
    output_format: str,
    output: Path | None,
    since: datetime | None,
    author: str | None,
) -> None:
    """Export bookmarks to a file."""
    data_dir = ctx.obj["data_dir"]
    db = get_db(data_dir)

    bookmarks = db.get_all_bookmarks(since=since, author=author)
    formatted = format_bookmarks(bookmarks, output_format)

    if output:
        output.write_text(formatted, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        # Generate default filename
        extension = "md" if output_format == "md" else output_format
        default_name = f"bookmarks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extension}"
        Path(default_name).write_text(formatted, encoding="utf-8")
        console.print(f"[green]Exported to {default_name}[/green]")


@main.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show bookmark statistics."""
    data_dir = ctx.obj["data_dir"]
    db = get_db(data_dir)

    stats_data = db.get_stats()

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

        for author in stats_data["top_authors"]:
            table.add_row(f"@{author['username']}", str(author["count"]))

        console.print(table)


@main.command("import-cookies")
@click.pass_context
def import_cookies(ctx: click.Context) -> None:
    """Import cookies from Chrome browser (must be logged into X, Chrome must be closed)."""
    data_dir = ctx.obj["data_dir"]
    session_path = get_session_path(data_dir)

    console.print("[bold]Importing cookies from Chrome...[/bold]")
    console.print("[dim]Make sure Chrome is closed and you're logged into X/Twitter.[/dim]\n")

    try:
        cookies = extract_x_cookies_from_chrome()
    except Exception as e:
        console.print(f"[bold red]Error reading cookies: {e}[/bold red]")
        console.print("\n[yellow]Make sure Chrome is completely closed and try again.[/yellow]")
        raise click.Abort()

    if not cookies:
        console.print("[bold red]No X/Twitter cookies found in Chrome.[/bold red]")
        console.print("\n[yellow]Please log into X/Twitter in Chrome first, then close Chrome and try again.[/yellow]")
        raise click.Abort()

    if not validate_x_cookies(cookies):
        console.print("[bold red]Auth token not found - you may not be logged into X/Twitter.[/bold red]")
        console.print("\n[yellow]Please log into X/Twitter in Chrome first, then close Chrome and try again.[/yellow]")
        raise click.Abort()

    # Save to Playwright storage state format
    storage_state = {
        "cookies": cookies,
        "origins": [],
    }

    session_path.mkdir(parents=True, exist_ok=True)
    state_file = session_path / "state.json"
    state_file.write_text(json.dumps(storage_state, indent=2))

    console.print(f"[green]Imported {len(cookies)} cookies![/green]")
    console.print(f"[dim]Saved to {state_file}[/dim]")
    console.print("\n[bold]You can now run:[/bold] bmarxs sync")


@main.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Import cookies from Chrome (alias for import-cookies)."""
    # Just invoke import-cookies
    ctx.invoke(import_cookies)


if __name__ == "__main__":
    main()
