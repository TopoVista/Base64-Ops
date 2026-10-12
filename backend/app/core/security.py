import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.core.config import get_settings


def _password_digest(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_digest(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(_password_digest(password), hashed_password.encode("utf-8"))


def create_access_token(subject: str) -> str:
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expires_minutes)
    return jwt.encode(
        {"sub": subject, "exp": expires_at},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


def _legacy_fernet(passphrase: str) -> Fernet:
    """Build the historical passphrase-derived key for a bounded migration window."""

    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> Fernet:
    """Use a native Fernet key when configured, preserving development ergonomics."""

    key = get_settings().github_token_encryption_key
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError):
        # Development settings historically accepted arbitrary passphrases.
        # Production startup now rejects these values; retaining this path
        # keeps local fixtures and existing developer .env files usable.
        return _legacy_fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        legacy_key = get_settings().github_token_encryption_legacy_key
        if not legacy_key:
            raise
        return _legacy_fernet(legacy_key).decrypt(value.encode("utf-8")).decode("utf-8")


def sign_oauth_state(payload: dict[str, Any], *, issued_at: datetime | None = None) -> str:
    """Sign a short-lived OAuth state payload with an issuance timestamp."""

    settings = get_settings()
    issued = issued_at or datetime.now(UTC)
    state_payload = {**payload, "iat": int(issued.timestamp())}
    raw = json.dumps(state_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.github_oauth_state_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
        + "."
        + base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    )


def verify_oauth_state(
    state: str,
    *,
    max_age_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify OAuth state integrity and a bounded creation time.

    One-time use is enforced by the GitHub service's hashed state record;
    this function keeps the cryptographic envelope independent and testable.
    """

    settings = get_settings()
    try:
        raw_part, signature_part = state.split(".", maxsplit=1)
        raw = base64.urlsafe_b64decode(raw_part + "=" * (-len(raw_part) % 4))
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid OAuth state") from exc
    expected = hmac.new(settings.github_oauth_state_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid OAuth state")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid OAuth state") from exc
    if not isinstance(payload, dict) or isinstance(payload.get("iat"), bool) or not isinstance(payload.get("iat"), int):
        raise ValueError("Invalid OAuth state")
    current = now or datetime.now(UTC)
    age = int(current.timestamp()) - payload["iat"]
    ttl = max_age_seconds if max_age_seconds is not None else settings.github_oauth_state_ttl_seconds
    if age < -30 or age > ttl:
        raise ValueError("OAuth state has expired")
    return payload


def github_authorize_url(state: str) -> str:
    settings = get_settings()
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": f"{settings.base_url}/api/github/callback",
            "scope": "repo workflow read:user user:email",
            "state": state,
        },
        quote_via=quote,
    )
    return f"https://github.com/login/oauth/authorize?{query}"
