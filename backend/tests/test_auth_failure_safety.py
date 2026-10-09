import jwt as pyjwt
import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.api import deps
from app.core.config import Settings


def test_invalid_jwt_does_not_echo_verification_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid_decode(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise pyjwt.InvalidTokenError("internal parser detail")

    class Client:
        def get_signing_key_from_jwt(self, _token: str):
            return type("SigningKey", (), {"key": "not-used"})()

    monkeypatch.setattr(deps, "_get_jwks_client", Client)
    monkeypatch.setattr(deps.pyjwt, "decode", invalid_decode)

    with pytest.raises(HTTPException) as error:
        deps._decode_clerk_token("malformed")

    assert error.value.status_code == 401
    assert error.value.detail == "Invalid authentication token."


def test_jwks_outage_is_retryable_and_does_not_echo_provider_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable_client():
        raise RuntimeError("https://clerk.example/jwks?secret=do-not-leak")

    monkeypatch.setattr(deps, "_get_jwks_client", unavailable_client)

    with pytest.raises(HTTPException) as error:
        deps._decode_clerk_token("any-token")

    assert error.value.status_code == 503
    assert error.value.detail == "Authentication is temporarily unavailable. Please retry shortly."
    assert "secret" not in str(error.value.detail)


def test_clerk_token_is_bound_to_the_configured_issuer_and_authorized_party(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    settings = Settings(
        clerk_issuer="https://clerk.example",
        clerk_authorized_parties="https://app.example,https://preview.example",
    )

    class Client:
        def get_signing_key_from_jwt(self, _token: str):
            return type("SigningKey", (), {"key": "not-used"})()

    def decode(*_args, **kwargs):  # noqa: ANN002, ANN003
        captured.update(kwargs)
        return {"sub": "user_clerk", "azp": "https://app.example"}

    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "_get_jwks_client", Client)
    monkeypatch.setattr(deps.pyjwt, "decode", decode)

    assert deps._decode_clerk_token("token") == {"sub": "user_clerk", "azp": "https://app.example"}
    assert captured["issuer"] == "https://clerk.example"


def test_clerk_token_with_untrusted_authorized_party_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(clerk_authorized_parties="https://app.example")

    class Client:
        def get_signing_key_from_jwt(self, _token: str):
            return type("SigningKey", (), {"key": "not-used"})()

    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "_get_jwks_client", Client)
    monkeypatch.setattr(deps.pyjwt, "decode", lambda *_args, **_kwargs: {"sub": "user_clerk", "azp": "https://evil.example"})

    with pytest.raises(HTTPException) as error:
        deps._decode_clerk_token("token")

    assert error.value.status_code == 401
    assert error.value.detail == "Invalid authentication token."


async def test_concurrent_first_login_reuses_the_unique_clerk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class Users:
        def __init__(self) -> None:
            self.find_calls = 0

        async def find_one(self, _query):  # noqa: ANN001, ANN201
            self.find_calls += 1
            return None if self.find_calls == 1 else {"_id": "usr_existing", "clerkId": "user_clerk"}

        async def insert_one(self, _document):  # noqa: ANN001, ANN201
            raise DuplicateKeyError("users_one_clerk_identity")

    users = Users()
    monkeypatch.setattr(deps, "get_db", lambda: type("Db", (), {"users": users})())
    monkeypatch.setattr(deps, "_decode_clerk_token", lambda _token: {"sub": "user_clerk"})

    result = await deps.get_current_user(type("Request", (), {"headers": {"Authorization": "Bearer token"}})())

    assert result == {"_id": "usr_existing", "clerkId": "user_clerk"}


async def test_oversized_bearer_token_is_rejected_before_jwt_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def decode(_token: str):  # noqa: ANN001, ANN202
        nonlocal called
        called = True
        return {"sub": "user_clerk"}

    monkeypatch.setattr(deps, "_decode_clerk_token", decode)
    request = type(
        "Request",
        (),
        {"headers": {"Authorization": "Bearer " + "x" * (deps.MAX_CLERK_BEARER_TOKEN_CHARS + 1)}},
    )()

    with pytest.raises(HTTPException) as error:
        await deps.get_current_user(request)

    assert error.value.status_code == 401
    assert error.value.detail == "Invalid authentication token."
    assert called is False


@pytest.mark.parametrize("subject", [None, 42, "", "u" * 256])
async def test_clerk_subject_must_be_a_bounded_string(monkeypatch: pytest.MonkeyPatch, subject: object) -> None:
    monkeypatch.setattr(deps, "_decode_clerk_token", lambda _token: {"sub": subject})
    request = type("Request", (), {"headers": {"Authorization": "Bearer token"}})()

    with pytest.raises(HTTPException) as error:
        await deps.get_current_user(request)

    assert error.value.status_code == 401
    assert error.value.detail == "Invalid authentication token."


async def test_user_identity_index_is_unique_and_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class Users:
        async def create_index(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(deps, "get_db", lambda: type("Db", (), {"users": Users()})())

    await deps.ensure_user_indexes()

    assert captured["args"] == ("clerkId",)
    assert captured["kwargs"]["unique"] is True
    assert captured["kwargs"]["partialFilterExpression"] == {"clerkId": {"$type": "string"}}
