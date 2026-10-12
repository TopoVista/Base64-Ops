import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.api.middleware import (
    RequestBodyLimitMiddleware,
    declared_body_exceeds_limit,
    is_expensive_path,
    trusted_client_ip,
)
from app.core.access_logging import AccessLogQueryRedactionFilter, redact_access_path_query
from app.core.config import Settings
from app.core.rate_limit import InMemoryRateLimitStore, rate_limit_key
from app.schemas.session import SessionChatRequest
from app.services import session_service

FERNET_KEY = Fernet.generate_key().decode("ascii")


def test_access_logs_redact_oauth_callback_query_values() -> None:
    import logging

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:5000",
            "GET",
            "/api/github/callback?code=one-time-code&state=signed-state",
            "1.1",
            307,
        ),
        exc_info=None,
    )

    assert AccessLogQueryRedactionFilter().filter(record)
    assert record.args[2] == "/api/github/callback?redacted"
    assert "one-time-code" not in record.args[2]
    assert "signed-state" not in record.args[2]


def test_access_log_redaction_keeps_query_free_paths_unchanged() -> None:
    assert redact_access_path_query("/api/health") == "/api/health"


async def test_in_memory_rate_limiter_enforces_a_bounded_sliding_window() -> None:
    limiter = InMemoryRateLimitStore()
    key = rate_limit_key(client_ip="203.0.113.4", scope="expensive")

    first = await limiter.check(key=key, limit=2, window_seconds=60)
    second = await limiter.check(key=key, limit=2, window_seconds=60)
    third = await limiter.check(key=key, limit=2, window_seconds=60)

    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert not third.allowed
    assert third.retry_after_seconds >= 1


async def test_in_memory_rate_limiter_evicts_oldest_unique_keys_at_capacity() -> None:
    limiter = InMemoryRateLimitStore(max_keys=2)

    await limiter.check(key="first", limit=10, window_seconds=60)
    await limiter.check(key="second", limit=10, window_seconds=60)
    await limiter.check(key="third", limit=10, window_seconds=60)

    assert tuple(limiter._events) == ("second", "third")


def test_rate_limit_key_does_not_include_raw_ip_address() -> None:
    key = rate_limit_key(client_ip="203.0.113.4", scope="api")

    assert "203.0.113.4" not in key
    assert key.startswith("base64:rl:api:")


def test_trusted_client_ip_accepts_only_a_single_valid_edge_address() -> None:
    assert trusted_client_ip(direct_client_ip="10.0.0.2", header_value="203.0.113.4") == "203.0.113.4"
    assert trusted_client_ip(direct_client_ip="10.0.0.2", header_value="2001:db8::1") == "2001:db8::1"
    # A caller-controlled X-Forwarded-For-style list is not parsed.
    assert trusted_client_ip(direct_client_ip="10.0.0.2", header_value="203.0.113.4, 10.0.0.1") == "10.0.0.2"
    assert trusted_client_ip(direct_client_ip="10.0.0.2", header_value="not-an-ip") == "10.0.0.2"


@pytest.mark.parametrize(
    "path",
    [
        "/api/session/chat",
        "/api/assistant/chat",
        "/api/stream-diagnostic",
        "/api/apply-patch",
        "/api/session/session-a/rag/reindex",
        "/api/session/session-a/ci/refresh",
        "/api/session/session-a/code/propose",
        "/api/session/session-a/approval/approval-a",
        "/api/session/session-a/pr",
    ],
)
def test_provider_or_workspace_work_uses_the_expensive_rate_limit(path: str) -> None:
    assert is_expensive_path(path)


@pytest.mark.parametrize("path", ["/api/session/all", "/api/session/session-a/evidence", "/api/github/repositories"])
def test_ordinary_reads_keep_the_regular_rate_limit(path: str) -> None:
    assert not is_expensive_path(path)


def test_production_requires_shared_rate_limit_and_host_configuration() -> None:
    for values in (
        {"environment": "production", "redis_url": "redis://example", "frontend_origin": "https://app.example"},
        {"environment": "production", "trusted_hosts": "api.example", "frontend_origin": "https://app.example"},
        {"environment": "production", "trusted_hosts": "api.example", "redis_url": "redis://example"},
    ):
        try:
            Settings(**values)
        except ValueError:
            continue
        raise AssertionError("Production security configuration unexpectedly validated")


def test_production_security_configuration_accepts_explicit_safe_values() -> None:
    settings = Settings(
        environment="production",
        trusted_hosts="api.example",
        redis_url="redis://example",
        frontend_origin="https://app.example",
        base_url="https://api.example",
        mongo_uri="mongodb://example",
        clerk_jwks_url="https://clerk.example/jwks",
        clerk_issuer="https://clerk.example",
        github_client_id="client-id",
        github_client_secret="client-secret",
        github_oauth_state_secret="x" * 32,
        github_token_encryption_key=FERNET_KEY,
        rate_limit_fail_closed=True,
        trusted_client_ip_header="CF-Connecting-IP",
    )

    assert settings.is_production


def test_production_workers_require_only_their_least_privileged_dependencies() -> None:
    maintenance = Settings(
        environment="production",
        service_role="maintenance",
        mongo_uri="mongodb://example",
    )
    indexer = Settings(
        environment="production",
        service_role="indexer",
        mongo_uri="mongodb://example",
        github_token_encryption_key=FERNET_KEY,
    )

    assert maintenance.service_role == "maintenance"
    assert indexer.service_role == "indexer"

    with pytest.raises(ValueError, match="GITHUB_TOKEN_ENCRYPTION_KEY"):
        Settings(
            environment="production",
            service_role="ci_observer",
            mongo_uri="mongodb://example",
            github_token_encryption_key="dev-only-token-key",
        )

    with pytest.raises(ValueError, match="SERVICE_ROLE"):
        Settings(environment="production", service_role="untrusted", mongo_uri="mongodb://example")


def test_production_requires_fail_closed_traffic_protection() -> None:
    values = {
        "environment": "production",
        "trusted_hosts": "api.example",
        "redis_url": "redis://example",
        "frontend_origin": "https://app.example",
        "base_url": "https://api.example",
        "mongo_uri": "mongodb://example",
        "clerk_jwks_url": "https://clerk.example/jwks",
        "clerk_issuer": "https://clerk.example",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "github_oauth_state_secret": "x" * 32,
        "github_token_encryption_key": FERNET_KEY,
    }

    with pytest.raises(ValueError, match="RATE_LIMIT_FAIL_CLOSED"):
        Settings(**values)


def test_production_rejects_spoofable_forwarded_for_rate_limit_header() -> None:
    values = {
        "environment": "production",
        "trusted_hosts": "api.example",
        "redis_url": "redis://example",
        "frontend_origin": "https://app.example",
        "base_url": "https://api.example",
        "mongo_uri": "mongodb://example",
        "clerk_jwks_url": "https://clerk.example/jwks",
        "clerk_issuer": "https://clerk.example",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "github_oauth_state_secret": "x" * 32,
        "github_token_encryption_key": FERNET_KEY,
        "rate_limit_fail_closed": True,
        "trusted_client_ip_header": "X-Forwarded-For",
    }

    with pytest.raises(ValueError, match="must not use X-Forwarded-For"):
        Settings(**values)


def test_production_rejects_development_oauth_secrets() -> None:
    values = {
        "environment": "production",
        "trusted_hosts": "api.example",
        "redis_url": "redis://example",
        "frontend_origin": "https://app.example",
        "base_url": "https://api.example",
        "mongo_uri": "mongodb://example",
        "clerk_jwks_url": "https://clerk.example/jwks",
        "clerk_issuer": "https://clerk.example",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "github_oauth_state_secret": "dev-only-github-state",
        "github_token_encryption_key": FERNET_KEY,
        "rate_limit_fail_closed": True,
        "trusted_client_ip_header": "CF-Connecting-IP",
    }

    with pytest.raises(ValueError, match="GITHUB_OAUTH_STATE_SECRET"):
        Settings(**values)


def test_production_requires_a_real_fernet_key_and_32_byte_oauth_state_secret() -> None:
    values = {
        "environment": "production",
        "trusted_hosts": "api.example",
        "redis_url": "redis://example",
        "frontend_origin": "https://app.example",
        "base_url": "https://api.example",
        "mongo_uri": "mongodb://example",
        "clerk_jwks_url": "https://clerk.example/jwks",
        "clerk_issuer": "https://clerk.example",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "github_oauth_state_secret": "x" * 32,
        "github_token_encryption_key": "not-a-fernet-key",
        "rate_limit_fail_closed": True,
        "trusted_client_ip_header": "CF-Connecting-IP",
    }

    with pytest.raises(ValueError, match="Fernet key"):
        Settings(**values)

    values["github_token_encryption_key"] = FERNET_KEY
    values["github_oauth_state_secret"] = "too-short"
    with pytest.raises(ValueError, match="GITHUB_OAUTH_STATE_SECRET"):
        Settings(**values)


def test_production_requires_a_https_clerk_issuer() -> None:
    values = {
        "environment": "production",
        "trusted_hosts": "api.example",
        "redis_url": "redis://example",
        "frontend_origin": "https://app.example",
        "base_url": "https://api.example",
        "mongo_uri": "mongodb://example",
        "clerk_jwks_url": "https://clerk.example/jwks",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "github_oauth_state_secret": "x" * 32,
        "github_token_encryption_key": FERNET_KEY,
        "rate_limit_fail_closed": True,
        "trusted_client_ip_header": "CF-Connecting-IP",
    }

    with pytest.raises(ValueError, match="CLERK_ISSUER"):
        Settings(**values)


def test_declared_oversized_or_malformed_body_is_rejected_before_parsing() -> None:
    assert not declared_body_exceeds_limit("1024", 1_024)
    assert declared_body_exceeds_limit("1025", 1_024)
    assert declared_body_exceeds_limit("not-a-number", 1_024)


async def test_chunked_oversized_body_is_rejected_before_the_route_can_parse_it() -> None:
    route_completed = False

    async def route(_scope, receive, send):  # noqa: ANN001
        nonlocal route_completed
        await receive()
        await receive()
        route_completed = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"unexpected"})

    incoming = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )

    async def receive():  # noqa: ANN201
        return next(incoming)

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(route, settings=Settings(max_request_body_bytes=5))
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/session/chat",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        },
        receive,
        send,
    )

    assert not route_completed
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"message":"Request payload is too large."}'


def test_alternate_chat_message_shape_cannot_bypass_prompt_limit() -> None:
    with pytest.raises(ValidationError, match="Message is too long"):
        SessionChatRequest(
            slugId="session-a",
            repoUrl="https://github.com/acme/base64.git",
            messages=[{"parts": [{"text": "x" * 24_001}]}],
        )


async def test_session_search_escapes_browser_regex_characters(monkeypatch) -> None:
    class Cursor:
        def sort(self, *_args):  # noqa: ANN002, ANN201
            return self

        def skip(self, *_args):  # noqa: ANN002, ANN201
            return self

        def limit(self, *_args):  # noqa: ANN002, ANN201
            return self

        async def to_list(self, _limit):  # noqa: ANN001, ANN201
            return []

    class Sessions:
        query: dict | None = None

        def find(self, query):  # noqa: ANN001, ANN201
            self.query = query
            return Cursor()

        async def count_documents(self, _query):  # noqa: ANN001, ANN201
            return 0

    sessions = Sessions()
    monkeypatch.setattr(session_service, "get_db", lambda: type("Database", (), {"sessions": sessions})())

    await session_service.get_user_sessions("user-a", "(a+)+$", 20, 1)

    assert sessions.query and sessions.query["$or"][0]["title"]["$regex"] == r"\(a\+\)\+\$"
