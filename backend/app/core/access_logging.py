"""Secret-safe Uvicorn access-log helpers.

OAuth authorization codes and signed state values arrive in query strings.
They must never be retained in an access log, even on a local development
machine. The filter keeps the request path useful while replacing every query
string with a stable marker.
"""

import logging
from typing import Any


def redact_access_path_query(path: str) -> str:
    """Return a route path without its potentially sensitive query string."""

    base, separator, _query = path.partition("?")
    return f"{base}?redacted" if separator else path


class AccessLogQueryRedactionFilter(logging.Filter):
    """Redact the ``full_path`` slot used by Uvicorn's AccessFormatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access records use five positional arguments: client,
        # method, full path, HTTP version, and status code. Preserve the tuple
        # shape because AccessFormatter reads it before rendering.
        args: Any = record.args
        if record.name != "uvicorn.access" or not isinstance(args, tuple) or len(args) != 5:
            return True
        full_path = args[2]
        if not isinstance(full_path, str) or "?" not in full_path:
            return True
        sanitized = list(args)
        sanitized[2] = redact_access_path_query(full_path)
        record.args = tuple(sanitized)
        return True


def configure_access_log_redaction() -> None:
    """Install the filter once, including when Uvicorn configured logging first."""

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, AccessLogQueryRedactionFilter) for item in access_logger.filters):
        access_logger.addFilter(AccessLogQueryRedactionFilter())
