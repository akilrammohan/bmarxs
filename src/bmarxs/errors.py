"""Error types and exit codes for bmarxs CLI.

Exit Code Scheme:
    0  - Success
    1  - General error (unknown/unexpected)
    2  - Authentication error (missing or invalid session)
    3  - Network error (connection failed, timeout)
    4  - Not found (bookmark, file, or resource doesn't exist)
    5  - Invalid input (bad arguments, malformed data)
    6  - Database error (SQLite issues)
    7  - Browser error (Playwright/Chrome issues)
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """CLI exit codes."""

    SUCCESS = 0
    GENERAL_ERROR = 1
    AUTH_ERROR = 2
    NETWORK_ERROR = 3
    NOT_FOUND = 4
    INVALID_INPUT = 5
    DATABASE_ERROR = 6
    BROWSER_ERROR = 7


@dataclass
class CLIError(Exception):
    """Structured error for CLI operations."""

    code: ExitCode
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert error to JSON-serializable dict."""
        result = {
            "success": False,
            "error": {
                "code": self.code.value,
                "code_name": self.code.name.lower(),
                "message": self.message,
            },
        }
        if self.details:
            result["error"]["details"] = self.details
        return result


class AuthError(CLIError):
    """Authentication/session error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ExitCode.AUTH_ERROR, message, details)


class NetworkError(CLIError):
    """Network/connection error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ExitCode.NETWORK_ERROR, message, details)


class NotFoundError(CLIError):
    """Resource not found error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ExitCode.NOT_FOUND, message, details)


class InvalidInputError(CLIError):
    """Invalid input/arguments error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ExitCode.INVALID_INPUT, message, details)


class DatabaseError(CLIError):
    """Database operation error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ExitCode.DATABASE_ERROR, message, details)


class BrowserError(CLIError):
    """Browser/Playwright error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ExitCode.BROWSER_ERROR, message, details)
