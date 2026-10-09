import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services import github_service


def test_repository_url_is_canonicalized_without_credentials_or_arbitrary_hosts() -> None:
    assert github_service.normalize_github_repository_url("https://github.com/acme/base64") == (
        "acme",
        "base64",
        "https://github.com/acme/base64.git",
    )

    for attacker_url in (
        "https://github.com.evil.example/acme/base64.git",
        "https://token@github.com/acme/base64.git",
        "https://github.com:invalid/acme/base64.git",
        "https://evil.example/acme/base64.git",
        "git@github.com:acme/base64.git",
    ):
        with pytest.raises(HTTPException) as exc:
            github_service.normalize_github_repository_url(attacker_url)
        assert exc.value.status_code == 400


def test_oauth_redirect_cannot_leave_the_configured_frontend(monkeypatch) -> None:
    monkeypatch.setattr(
        github_service,
        "get_settings",
        lambda: Settings(frontend_origin="https://app.base64ops.example"),
    )

    assert github_service.safe_frontend_redirect("/session/demo") == "https://app.base64ops.example/session/demo"
    with pytest.raises(HTTPException) as exc:
        github_service.safe_frontend_redirect("https://attacker.example/callback")
    assert exc.value.status_code == 400


def test_development_oauth_redirect_preserves_the_loopback_origin_that_started_it(monkeypatch) -> None:
    monkeypatch.setattr(
        github_service,
        "get_settings",
        lambda: Settings(
            environment="development",
            frontend_origin="http://localhost:5173",
        ),
    )

    assert (
        github_service.safe_frontend_redirect("http://127.0.0.1:5173/session/demo?tab=code")
        == "http://127.0.0.1:5173/session/demo?tab=code"
    )


def test_production_oauth_redirect_does_not_allow_the_alternate_loopback_origin(monkeypatch) -> None:
    monkeypatch.setattr(
        github_service,
        "get_settings",
        lambda: Settings(
            environment="production",
            frontend_origin="https://app.base64ops.example",
            trusted_hosts="api.base64ops.example",
            redis_url="redis://example.invalid:6379/0",
            rate_limit_fail_closed=True,
            trusted_client_ip_header="X-Real-IP",
            base_url="https://api.base64ops.example",
            mongo_uri="mongodb://example.invalid/base64",
            clerk_jwks_url="https://clerk.example/jwks",
            clerk_issuer="https://clerk.example",
            github_client_id="client-id",
            github_client_secret="client-secret",
            github_oauth_state_secret="long-production-state-secret",
            github_token_encryption_key="long-production-token-encryption-key",
        ),
    )

    with pytest.raises(HTTPException) as exc:
        github_service.safe_frontend_redirect("http://127.0.0.1:5173/session/demo")
    assert exc.value.status_code == 400


def test_actions_log_redirect_headers_never_forward_github_oauth_credentials() -> None:
    headers = github_service.external_log_download_headers()

    assert headers == {"Accept": "application/octet-stream"}
    assert "Authorization" not in headers


def test_actions_log_redirect_is_limited_to_github_actions_download_hosts() -> None:
    assert github_service.is_safe_actions_log_download_url(
        "https://pipelines.actions.githubusercontent.com/signed-log"
    )
    assert github_service.is_safe_actions_log_download_url(
        "https://productionresultssa16.blob.core.windows.net/signed-log"
    )
    assert not github_service.is_safe_actions_log_download_url("https://attacker.example/signed-log")
    assert not github_service.is_safe_actions_log_download_url("http://pipelines.actions.githubusercontent.com/log")
    assert not github_service.is_safe_actions_log_download_url(
        "https://actions.githubusercontent.com.attacker.example/log"
    )
    assert not github_service.is_safe_actions_log_download_url(
        "https://arbitrary.blob.core.windows.net/signed-log"
    )


async def test_repository_resolution_requires_github_api_access(monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def authorized(_user_id: str, method: str, path: str) -> dict:
        seen.update({"method": method, "path": path})
        return {"clone_url": "https://github.com/acme/base64.git", "default_branch": "main"}

    monkeypatch.setattr(github_service, "github_api", authorized)

    resolved = await github_service.resolve_authorized_repository("user-a", "https://github.com/acme/base64")

    assert seen == {"method": "GET", "path": "/repos/acme/base64"}
    assert resolved["cloneUrl"] == "https://github.com/acme/base64.git"


async def test_repository_metadata_cache_is_tenant_scoped_and_never_stores_tokens(monkeypatch) -> None:
    class RepositoryCache:
        def __init__(self) -> None:
            self.records: dict[str, dict] = {}

        async def find_one(self, query):  # noqa: ANN001, ANN201
            record = self.records.get(query["_id"])
            if not record or record["expiresAt"] <= query["expiresAt"]["$gt"]:
                return None
            return record

        async def update_one(self, query, update, upsert=False):  # noqa: ANN001, ANN201
            assert upsert
            self.records[query["_id"]] = {"_id": query["_id"], **update["$set"]}

        async def delete_one(self, query):  # noqa: ANN001, ANN201
            self.records.pop(query["_id"], None)

    cache = RepositoryCache()
    database = type("Database", (), {"github_repository_cache": cache})()
    fetch_calls: list[str] = []

    async def fetch(user_id: str) -> list[dict[str, str]]:
        fetch_calls.append(user_id)
        return [{"id": "42", "name": "base64", "cloneUrl": "https://github.com/acme/base64.git"}]

    monkeypatch.setattr(github_service, "get_db", lambda: database)
    monkeypatch.setattr(github_service, "_fetch_repositories", fetch)
    monkeypatch.setattr(github_service, "get_settings", lambda: Settings(github_repository_cache_ttl_seconds=60))

    first = await github_service.list_repositories("user-a")
    second = await github_service.list_repositories("user-a")

    assert first == second
    assert fetch_calls == ["user-a"]
    assert set(cache.records["user-a"]) == {"_id", "repositories", "fetchedAt", "expiresAt"}


async def test_disconnect_invalidates_only_the_current_users_repository_cache(monkeypatch) -> None:
    class Collection:
        def __init__(self, records: dict[str, dict]) -> None:
            self.records = records

        async def delete_one(self, query):  # noqa: ANN001, ANN201
            self.records.pop(query.get("_id") or query.get("userId"), None)

    cache = Collection({"user-a": {"_id": "user-a"}, "user-b": {"_id": "user-b"}})
    accounts = Collection({"user-a": {"userId": "user-a"}, "user-b": {"userId": "user-b"}})
    database = type(
        "Database", (), {"github_repository_cache": cache, "github_accounts": accounts}
    )()
    monkeypatch.setattr(github_service, "get_db", lambda: database)

    assert await github_service.disconnect_github("user-a") == {"connected": False}
    assert set(cache.records) == {"user-b"}
    assert set(accounts.records) == {"user-b"}
