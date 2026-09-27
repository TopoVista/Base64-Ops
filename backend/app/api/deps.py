"""
Authentication dependency using Clerk JWTs.

Every protected endpoint declares:
    user: dict = Depends(get_current_user)

The returned dict is the MongoDB user document (without sensitive fields).
It contains at minimum: _id, name, email, avatar, createdAt, updatedAt.
"""

from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.db.mongo import get_db
from app.utils.datetime import utc_now
from app.utils.ids import new_id

# ---------------------------------------------------------------------------
# JWKS cache — fetched once and reused across requests
# ---------------------------------------------------------------------------
_jwks_client: pyjwt.PyJWKClient | None = None


def _get_jwks_client() -> pyjwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.clerk_jwks_url:
            raise RuntimeError("CLERK_JWKS_URL is not configured in .env")
        _jwks_client = pyjwt.PyJWKClient(settings.clerk_jwks_url)
    return _jwks_client


def _decode_clerk_token(token: str) -> dict[str, Any]:
    """Decode and verify a Clerk-issued JWT. Returns the payload dict."""
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk tokens have no audience claim by default
        )
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from None
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication error: {exc}",
        ) from exc


async def get_current_user(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency that:
    1. Extracts the Clerk Bearer token from the Authorization header.
    2. Verifies it against Clerk's JWKS endpoint.
    3. Upserts the user in MongoDB (first-time login creates the record).
    4. Returns the MongoDB user document.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or malformed",
        )

    token = auth_header.removeprefix("Bearer ").strip()
    payload = _decode_clerk_token(token)

    # Clerk's subject claim is the Clerk user ID (e.g. "user_2abc...")
    clerk_user_id: str = payload.get("sub", "")
    if not clerk_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")

    db = get_db()

    # Upsert: look up by Clerk user ID; create a new record on first sign-in
    user = await db.users.find_one({"clerkId": clerk_user_id})
    if not user:
        # Pull name/email/avatar from the Clerk JWT claims if available
        email = ""
        if payload.get("email"):
            email = payload["email"]

        now = utc_now()
        user = {
            "_id": new_id("usr_"),
            "clerkId": clerk_user_id,
            "name": payload.get("name", ""),
            "email": email,
            "avatar": payload.get("image_url") or payload.get("picture"),
            "createdAt": now,
            "updatedAt": now,
        }
        await db.users.insert_one(user)

    return user

# ---------------------------------------------------------------
# Convenience dependency: authenticated user's internal DB ID
# ---------------------------------------------------------------
async def get_current_user_id(
    user: dict[str, Any] = Depends(get_current_user),
) -> str:
    """
    Convenience dependency for endpoints that only need the
    authenticated user's internal MongoDB ID.
    """
    return str(user["_id"])