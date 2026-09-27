import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import bcrypt
from cryptography.fernet import Fernet
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


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(settings.github_token_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def sign_oauth_state(payload: dict[str, Any]) -> str:
    settings = get_settings()
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.github_oauth_state_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
        + "."
        + base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    )


def verify_oauth_state(state: str) -> dict[str, Any]:
    settings = get_settings()
    raw_part, signature_part = state.split(".", maxsplit=1)
    raw = base64.urlsafe_b64decode(raw_part + "=" * (-len(raw_part) % 4))
    signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
    expected = hmac.new(settings.github_oauth_state_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid OAuth state")
    return json.loads(raw.decode("utf-8"))


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
