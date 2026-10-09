from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.core import security
from app.core.config import Settings
from app.services import github_service


def test_signed_oauth_state_expires_even_when_its_signature_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "get_settings", lambda: Settings(github_oauth_state_secret="test-state-secret"))
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    state = security.sign_oauth_state({"userId": "user-a"}, issued_at=issued_at)

    verified = security.verify_oauth_state(state, max_age_seconds=60, now=issued_at + timedelta(seconds=60))
    assert verified["userId"] == "user-a"
    with pytest.raises(ValueError, match="expired"):
        security.verify_oauth_state(state, max_age_seconds=60, now=issued_at + timedelta(seconds=61))


async def test_oauth_connect_persists_only_a_hashed_one_time_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    class States:
        def __init__(self) -> None:
            self.records: dict[str, dict] = {}

        async def insert_one(self, record):  # noqa: ANN001, ANN201
            self.records[record["_id"]] = record

    states = States()
    monkeypatch.setattr(github_service, "get_db", lambda: type("Database", (), {"github_oauth_states": states})())
    monkeypatch.setattr(
        github_service,
        "get_settings",
        lambda: Settings(
            github_client_id="client-id",
            github_client_secret="client-secret",
            frontend_origin="https://app.base64ops.example",
        ),
    )

    url = await github_service.create_connect_url("user-a", "/session/demo")
    state = parse_qs(urlparse(url).query)["state"][0]

    assert set(states.records) == {github_service._oauth_state_hash(state)}
    record = next(iter(states.records.values()))
    assert record["userId"] == "user-a"
    assert "state" not in record and "nonce" not in record
    assert record["expiresAt"] > record["createdAt"]


async def test_oauth_callback_consumes_the_challenge_before_linking_an_account(monkeypatch: pytest.MonkeyPatch) -> None:
    state = security.sign_oauth_state(
        {"userId": "user-a", "redirectTo": "https://app.base64ops.example/new", "nonce": "test"}
    )
    state_hash = github_service._oauth_state_hash(state)

    class States:
        def __init__(self) -> None:
            self.records = {
                state_hash: {
                    "_id": state_hash,
                    "userId": "user-a",
                    "expiresAt": datetime.now(UTC) + timedelta(minutes=5),
                }
            }

        async def find_one_and_delete(self, query):  # noqa: ANN001, ANN201
            record = self.records.get(query["_id"])
            if not record or record["userId"] != query["userId"] or record["expiresAt"] <= query["expiresAt"]["$gt"]:
                return None
            return self.records.pop(query["_id"])

    class Collection:
        async def update_one(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

        async def delete_one(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    database = type(
        "Database",
        (),
        {"github_oauth_states": States(), "github_accounts": Collection(), "github_repository_cache": Collection()},
    )()

    async def exchange(_code: str) -> dict[str, str]:
        return {"access_token": "test-access-token"}

    async def github_user(_token: str) -> dict[str, str]:
        return {"id": "42", "login": "octocat"}

    monkeypatch.setattr(github_service, "get_db", lambda: database)
    monkeypatch.setattr(github_service, "exchange_code_for_token", exchange)
    monkeypatch.setattr(github_service, "fetch_github_user", github_user)
    monkeypatch.setattr(
        github_service,
        "get_settings",
        lambda: Settings(frontend_origin="https://app.base64ops.example"),
    )

    assert await github_service.complete_github_oauth("code", state) == "https://app.base64ops.example/new"
    with pytest.raises(HTTPException) as reused:
        await github_service.complete_github_oauth("another-code", state)
    assert reused.value.status_code == 400
