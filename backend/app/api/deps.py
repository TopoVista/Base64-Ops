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
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.db.mongo import get_db
from app.utils.datetime import utc_now
from app.utils.ids import new_id

# ---------------------------------------------------------------------------
# JWKS cache — fetched once and reused across requests
# ---------------------------------------------------------------------------
_jwks_client: pyjwt.PyJWKClient | None = None
# JWTs normally remain far below this size. Bounding an Authorization value
# before parsing or fetching a JWK avoids turning a deliberately enormous
# header into CPU/network work on every public authenticated endpoint.
MAX_CLERK_BEARER_TOKEN_CHARS = 16_384


async def ensure_user_indexes() -> None:
    """Install the identity uniqueness invariant before serving tenant data.

    API replicas can receive a user's first authenticated request at the same
    time. A unique Clerk identity index makes the database, not a process-local
    find-then-insert sequence, the source of truth. The partial predicate keeps
    this safe for any pre-existing non-Clerk records during a migration.
    """

    await get_db().users.create_index(
        "clerkId",
        unique=True,
        partialFilterExpression={"clerkId": {"$type": "string"}},
        name="users_one_clerk_identity",
    )


def _authorized_parties() -> frozenset[str]:
    """Return the explicit allowed browser origins for Clerk's ``azp`` claim."""

    settings = get_settings()
    configured = frozenset(
        value.strip().rstrip("/")
        for value in settings.clerk_authorized_parties.split(",")
        if value.strip()
    )
    if configured:
        return configured
    # The configured frontend origin is still an explicit production boundary.
    # Local development supports both loopback spellings used by Vite.
    origins = {settings.frontend_origin.rstrip("/")}
    if not settings.is_production:
        origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    return frozenset(origins)


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
        settings = get_settings()
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        decode_options: dict[str, Any] = {"verify_aud": False}
        decode_kwargs: dict[str, Any] = {}
        if settings.clerk_issuer:
            decode_kwargs["issuer"] = settings.clerk_issuer.rstrip("/")
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options=decode_options,  # Clerk tokens have no audience claim by default
            **decode_kwargs,
        )
        azp = payload.get("azp")
        if azp is not None and (not isinstance(azp, str) or azp.rstrip("/") not in _authorized_parties()):
            raise pyjwt.InvalidTokenError("Unauthorized Clerk token origin")
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from None
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        ) from exc
    except Exception as exc:
        # A JWKS fetch/configuration failure is an availability problem, not a
        # user error. Never echo provider URLs, parsing details, or exception
        # text back to a browser.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable. Please retry shortly.",
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
    if not token or len(token) > MAX_CLERK_BEARER_TOKEN_CHARS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )
    payload = _decode_clerk_token(token)

    # Clerk's subject claim is the Clerk user ID (e.g. "user_2abc...")
    clerk_user_id = payload.get("sub")
    if not isinstance(clerk_user_id, str) or not clerk_user_id or len(clerk_user_id) > 255:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")

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
        try:
            await db.users.insert_one(user)
        except DuplicateKeyError:
            # A concurrent API replica created this Clerk identity after our
            # initial read. Return that canonical record; never mint a second
            # tenant identity or surface a database detail to the browser.
            user = await db.users.find_one({"clerkId": clerk_user_id})
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication is temporarily unavailable. Please retry shortly.",
                ) from None

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
