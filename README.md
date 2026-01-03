# bmarxs

CLI for exporting X/Twitter bookmarks. Designed for both human and agent use.

Works by importing login cookies from Chrome. First log into X in Chrome, then close Chrome after successful login. Then run `bmarxs import-cookies` before running anything else.

## Install

```bash
pipx install bmarxs   # Recommended
# or
uv tool install bmarxs
# or
pip install bmarxs
```

## Setup

```bash
# Import cookies from Chrome (must be logged into X, Chrome closed)
bmarxs import-cookies
```

## Commands

```bash
bmarxs sync                    # Sync new bookmarks
bmarxs sync --all --enrich     # Sync all + fetch URL metadata
bmarxs list                    # List bookmarks (table)
bmarxs list --unprocessed      # List unprocessed only
bmarxs search "query"          # Full-text search
bmarxs export                  # Export to stdout (JSON)
bmarxs export --format csv     # Export as CSV
bmarxs stats                   # Show statistics
bmarxs mark-processed ID...    # Mark as processed
bmarxs mark-unprocessed ID...  # Mark as unprocessed
bmarxs enrich                  # Enrich URLs with metadata
```

## Agent/Programmatic Use

### Global Flags

| Flag | Description |
|------|-------------|
| `--json` / `-j` | Output structured JSON |
| `--quiet` / `-q` | Suppress progress messages |

### Examples

```bash
# Get stats as JSON
bmarxs --json stats

# Sync and get structured result
bmarxs --json sync

# List unprocessed bookmarks as JSON
bmarxs --json list --unprocessed

# Export to file
bmarxs export --format json > bookmarks.json
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Auth error |
| 3 | Network error |
| 4 | Not found |
| 5 | Invalid input |
| 6 | Database error |
| 7 | Browser error |

### JSON Response Format

Success:
```json
{"success": true, "synced_count": 5, "message": "Synced 5 new bookmarks"}
```

Error:
```json
{"success": false, "error": {"code": 2, "code_name": "auth_error", "message": "Auth token not found"}}
```

## Data

Stored in `./data/` by default (override with `--data-dir`):
- `bookmarks.db` - SQLite database
- `session/state.json` - Browser session
