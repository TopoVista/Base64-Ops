"""HTTP protections that must run before routes can trigger expensive work."""

from __future__ import annotations

import logging
from ipaddress import ip_address

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_settings
from app.core.rate_limit import InMemoryRateLimitStore, RateLimitStore, RedisRateLimitStore, rate_limit_key

logger = logging.getLogger(__name__)

_EXPENSIVE_PATHS = frozenset(
    {
        "/api/session/chat",
        "/api/assistant/chat",
        "/api/stream-diagnostic",
        "/api/apply-patch",
    }
)
_BYPASS_PATHS = frozenset({"/health", "/api/health", "/ready", "/api/ready"})
_EXPENSIVE_SESSION_SUFFIXES = frozenset(
    {
        "/rag/reindex",
        "/ci/refresh",
        "/code/propose",
        "/pr",
    }
)


def declared_body_exceeds_limit(content_length: str | None, limit: int) -> bool:
    """Reject an oversized declared body before it is parsed or persisted."""
    if not content_length:
        return False
    try:
        return int(content_length) > limit
    except ValueError:
        return True


def trusted_client_ip(*, direct_client_ip: str, header_value: str | None) -> str:
    """Return a verified edge IP or the direct connection identity.

    The configured edge header is accepted only as exactly one valid IP
    address. In particular, this never parses X-Forwarded-For lists, where an
    untrusted caller could prepend a spoofed address and evade per-IP limits.
    """

    candidate = (header_value or "").strip()
    if candidate:
        try:
            return ip_address(candidate).compressed
        except ValueError:
            pass
    return direct_client_ip


def is_expensive_path(path: str) -> bool:
    """Classify endpoints that can spend provider, clone, or delivery capacity.

    Route parameters prevent this from being a fixed exact-path list. Keep
    the matching conservative and explicit: ordinary session reads retain the
    regular budget, while work that can clone/index, call GitHub, build an
    approval proposal, or execute a previously approved delivery shares the
    tighter abuse budget.
    """

    if path in _EXPENSIVE_PATHS:
        return True
    return path.startswith("/api/session/") and (
        any(path.endswith(suffix) for suffix in _EXPENSIVE_SESSION_SUFFIXES)
        or "/approval/" in path
    )


class RequestBodyTooLarge(Exception):
    """Private signal used to stop parsing a streamed oversized request."""


class RequestBodyLimitMiddleware:
    """Enforce a real body ceiling for both declared and chunked requests.

    ``Content-Length`` is useful for rejecting a known oversized payload before
    reading it, but it is optional for chunked HTTP requests. The wrapped ASGI
    receive callable counts each body chunk as the framework consumes it, so a
    missing or misleading header cannot reach request parsing, persistence, or
    a model invocation beyond the configured ceiling.
    """

    def __init__(self, app, *, settings: Settings) -> None:
        self.app: ASGIApp = app
        self._settings = settings

    @staticmethod
    async def _send_payload_too_large(send: Send) -> None:
        body = b'{"message":"Request payload is too large."}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if (
            request.url.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH"}
            and declared_body_exceeds_limit(
                request.headers.get("content-length"), self._settings.max_request_body_bytes
            )
        ):
            await self._send_payload_too_large(send)
            return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._settings.max_request_body_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._send_payload_too_large(send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if get_settings().is_production or request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings, store: RateLimitStore | None = None) -> None:
        super().__init__(app)
        self._settings = settings
        self._store = store or (
            RedisRateLimitStore(settings.redis_url)
            if settings.redis_url
            else InMemoryRateLimitStore(max_keys=settings.rate_limit_local_max_keys)
        )

    async def dispatch(self, request: Request, call_next):
        if not self._settings.rate_limit_enabled or request.url.path in _BYPASS_PATHS:
            return await call_next(request)

        direct_client_ip = request.client.host if request.client else "unknown"
        client_ip = trusted_client_ip(
            direct_client_ip=direct_client_ip,
            header_value=request.headers.get(self._settings.trusted_client_ip_header)
            if self._settings.trusted_client_ip_header
            else None,
        )
        expensive = is_expensive_path(request.url.path)
        limit = (
            self._settings.rate_limit_expensive_requests_per_minute
            if expensive
            else self._settings.rate_limit_requests_per_minute
        )
        scope = "expensive" if expensive else "api"
        try:
            decision = await self._store.check(
                key=rate_limit_key(client_ip=client_ip, scope=scope),
                limit=max(1, limit),
                window_seconds=60,
            )
        except Exception as exc:
            # Never log Redis connection strings or request headers.
            logger.warning("Rate-limit backend unavailable: %s", type(exc).__name__)
            if not self._settings.rate_limit_fail_closed:
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={"message": "Traffic protection is temporarily unavailable. Please retry shortly."},
            )

        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={"message": "Too many requests. Please retry shortly."},
                headers={"Retry-After": str(decision.retry_after_seconds), "Cache-Control": "no-store"},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
