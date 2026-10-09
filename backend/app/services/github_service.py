import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    github_authorize_url,
    sign_oauth_state,
    verify_oauth_state,
)
from app.db.mongo import get_db
from app.utils.datetime import utc_now

_GITHUB_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ACTIONS_LOG_DOWNLOAD_HOST_SUFFIX = ".actions.githubusercontent.com"
# GitHub currently issues many Actions log redirects through this Azure Blob
# account family rather than an actions.githubusercontent.com host. Keep this
# deliberately narrow: accepting arbitrary blob storage would turn the API
# into an outbound-request primitive.
_ACTIONS_LOG_AZURE_HOST = re.compile(r"^productionresultssa\d+\.blob\.core\.windows\.net$")


def is_safe_actions_log_download_url(url: str) -> bool:
    """Allow only GitHub Actions' HTTPS signed-log download hosts.

    A bearer token is never forwarded to this second request, but allowing an
    arbitrary redirect target would still make the API an outbound-request
    primitive. GitHub.com Actions log downloads use either its Actions host
    family or the narrowly allowlisted Azure account used for result archives.
    GitHub Enterprise is intentionally unsupported by this boundary.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and not parsed.username and not parsed.password and (
        host == "actions.githubusercontent.com"
        or host.endswith(_ACTIONS_LOG_DOWNLOAD_HOST_SUFFIX)
        or bool(_ACTIONS_LOG_AZURE_HOST.fullmatch(host))
    )


def external_log_download_headers() -> dict[str, str]:
    """Headers for a GitHub-issued signed log URL; intentionally no OAuth."""
    return {"Accept": "application/octet-stream"}


def normalize_github_repository_url(repo_url: str) -> tuple[str, str, str]:
    """Accept only a canonical public GitHub HTTPS repository location.

    Repository URLs originate in the browser, so this parsing boundary must
    not permit an arbitrary host to receive an OAuth token during ``git
    clone``. Authorization is checked separately for the connected account.
    """
    parsed = urlparse(repo_url.strip())
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() != "github.com":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a GitHub repository from this account.",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment or port:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub repository URL.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub repository URL.")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    if not _GITHUB_REPOSITORY_PART.fullmatch(owner) or not _GITHUB_REPOSITORY_PART.fullmatch(repository):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub repository URL.")
    return owner, repository, f"https://github.com/{owner}/{repository}.git"


def safe_frontend_redirect(redirect_to: str | None) -> str:
    """Keep OAuth completion inside an explicitly allowed frontend origin.

    Development has two conventional loopback spellings.  They are different
    browser origins, so returning a user who began at ``127.0.0.1`` to
    ``localhost`` after OAuth can make an otherwise valid Clerk session look
    unauthenticated.  Permit both *only* outside production and preserve the
    exact initiating origin.  Production remains pinned to FRONTEND_ORIGIN.
    """
    settings = get_settings()
    fallback = f"{settings.frontend_origin}/new?github=connected"
    if not redirect_to:
        return fallback
    target = urlparse(redirect_to)
    origin = urlparse(settings.frontend_origin)
    allowed_origins = {(origin.scheme, origin.netloc)}
    if not settings.is_production:
        allowed_origins.update(
            {
                ("http", "localhost:5173"),
                ("http", "127.0.0.1:5173"),
            }
        )
    if not target.scheme and not target.netloc and redirect_to.startswith("/") and not redirect_to.startswith("//"):
        return urlunparse((origin.scheme, origin.netloc, target.path, "", target.query, ""))
    if (target.scheme, target.netloc) in allowed_origins:
        return redirect_to
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth return URL.")


def _oauth_state_hash(state: str) -> str:
    """Database key for a state challenge; never persist the browser token."""

    return hashlib.sha256(state.encode("utf-8")).hexdigest()


async def create_connect_url(user_id: str, redirect_to: str | None = None) -> str:
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub OAuth is not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in backend/.env.",
        )
    now = utc_now()
    state = sign_oauth_state(
        {
            "userId": user_id,
            "redirectTo": safe_frontend_redirect(redirect_to),
            "nonce": secrets.token_urlsafe(24),
        }
    )
    await get_db().github_oauth_states.insert_one(
        {
            "_id": _oauth_state_hash(state),
            "userId": user_id,
            "createdAt": now,
            "expiresAt": now + timedelta(seconds=settings.github_oauth_state_ttl_seconds),
        }
    )
    return github_authorize_url(state)


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": f"{settings.base_url}/api/github/callback",
            },
        )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub OAuth failed")
    return data


async def fetch_github_user(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    response.raise_for_status()
    return response.json()


async def complete_github_oauth(code: str, state: str) -> str:
    db = get_db()
    try:
        state_payload = verify_oauth_state(state)
        user_id = str(state_payload["userId"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired GitHub connection request.",
        ) from exc
    consumed = await db.github_oauth_states.find_one_and_delete(
        {"_id": _oauth_state_hash(state), "userId": user_id, "expiresAt": {"$gt": utc_now()}}
    )
    if not consumed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired GitHub connection request.",
        )
    token_data = await exchange_code_for_token(code)
    access_token = token_data["access_token"]
    github_user = await fetch_github_user(access_token)
    await db.github_accounts.update_one(
        {"userId": user_id},
        {
            "$set": {
                "userId": user_id,
                "githubId": str(github_user["id"]),
                "githubLogin": github_user["login"],
                "accessToken": encrypt_secret(access_token),
                "updatedAt": utc_now(),
            },
            "$setOnInsert": {"createdAt": utc_now()},
        },
        upsert=True,
    )
    # A reconnect may grant a different set of repositories. Repository list
    # cache records contain no token, but they must not outlive this change.
    await db.github_repository_cache.delete_one({"_id": user_id})
    return safe_frontend_redirect(state_payload.get("redirectTo"))


async def get_github_access_token(user_id: str) -> str:
    account = await get_db().github_accounts.find_one({"userId": user_id})
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GitHub account not connected")
    return decrypt_secret(account["accessToken"])


async def resolve_authorized_repository(user_id: str, repo_url: str) -> dict[str, str]:
    """Canonicalize a repository and verify the current OAuth token can read it."""
    owner, repository, canonical_clone_url = normalize_github_repository_url(repo_url)
    try:
        response = await github_api(user_id, "GET", f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403, 404}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The selected repository is unavailable to this GitHub account.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub could not verify the selected repository. Please try again.",
        ) from None
    if not isinstance(response, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The selected repository is unavailable.")
    return {
        "owner": owner,
        "repository": repository,
        "cloneUrl": str(response.get("clone_url") or canonical_clone_url),
        "defaultBranch": str(response.get("default_branch") or "main"),
    }


async def ensure_github_indexes() -> None:
    """Install expiry support for tenant-scoped GitHub metadata caching."""

    await get_db().github_repository_cache.create_index("expiresAt", expireAfterSeconds=0)
    await get_db().github_oauth_states.create_index("expiresAt", expireAfterSeconds=0)


async def _fetch_repositories(user_id: str) -> list[dict[str, Any]]:
    """Fetch repository metadata from GitHub without caching credentials."""

    access_token = await get_github_access_token(user_id)
    repos: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(timeout=30) as client:
        while page <= 5:
            response = await client.get(
                "https://api.github.com/user/repos",
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == status.HTTP_401_UNAUTHORIZED:
                    detail = "GitHub connection expired. Reconnect GitHub and try again."
                elif code == status.HTTP_403_FORBIDDEN:
                    detail = "GitHub denied repository access. Reconnect GitHub with repository access enabled."
                elif code in {status.HTTP_429_TOO_MANY_REQUESTS, 502, 503, 504}:
                    detail = "GitHub is temporarily unavailable. Wait a moment and refresh repositories."
                else:
                    detail = "GitHub could not list repositories. Refresh or reconnect GitHub."
                raise HTTPException(
                    status_code=code if code < 500 else status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=detail,
                ) from None
            batch = response.json()
            if not batch:
                break
            repos.extend(batch)
            page += 1
    return [
        {
            "id": repo["id"],
            "name": repo["name"],
            "fullName": repo["full_name"],
            "htmlUrl": repo["html_url"],
            "cloneUrl": repo["clone_url"],
            "private": repo["private"],
            "defaultBranch": repo["default_branch"],
            "description": repo.get("description"),
            "fork": repo.get("fork", False),
            "owner": {
                "login": repo["owner"]["login"],
                "avatarUrl": repo["owner"]["avatar_url"],
                "htmlUrl": repo["owner"]["html_url"],
            },
        }
        for repo in repos
    ]


async def list_repositories(user_id: str) -> list[dict[str, Any]]:
    """Return a short-lived metadata cache, otherwise fetch GitHub once.

    The cache key is the authenticated user ID rather than a browser-supplied
    repository name. It contains display metadata only; a later session
    creation still calls ``resolve_authorized_repository`` against GitHub.
    """

    db = get_db()
    now = utc_now()
    cached = await db.github_repository_cache.find_one({"_id": user_id, "expiresAt": {"$gt": now}})
    repositories = cached.get("repositories") if cached else None
    if isinstance(repositories, list) and all(isinstance(item, dict) for item in repositories):
        return repositories

    repositories = await _fetch_repositories(user_id)
    cache_ttl = get_settings().github_repository_cache_ttl_seconds
    if cache_ttl > 0:
        await db.github_repository_cache.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "repositories": repositories,
                    "fetchedAt": now,
                    "expiresAt": now + timedelta(seconds=cache_ttl),
                }
            },
            upsert=True,
        )
    return repositories


async def disconnect_github(user_id: str) -> dict[str, bool]:
    db = get_db()
    await db.github_accounts.delete_one({"userId": user_id})
    await db.github_repository_cache.delete_one({"_id": user_id})
    return {"connected": False}


async def github_api(user_id: str, method: str, path: str, **kwargs: Any) -> Any:
    token = await get_github_access_token(user_id)
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.request(
            method,
            f"https://api.github.com{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            **kwargs,
        )
    response.raise_for_status()
    if response.content:
        return response.json()
    return None


async def github_api_bytes(user_id: str, path: str, *, max_bytes: int) -> tuple[bytes, bool]:
    """Read bounded non-JSON GitHub content through the existing OAuth service."""
    token = await get_github_access_token(user_id)
    data = bytearray()
    truncated = False
    api_url = f"https://api.github.com{path}"
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        async with client.stream(
            "GET",
            api_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise httpx.HTTPStatusError(
                        "Missing GitHub log redirect location", request=response.request, response=response
                    )
                download_url = urljoin(api_url, location)
                if not is_safe_actions_log_download_url(download_url):
                    raise httpx.HTTPStatusError(
                        "Unsafe GitHub log redirect", request=response.request, response=response
                    )
                # Signed Actions-log URLs are external to api.github.com.
                # Never forward the GitHub OAuth bearer token to that host.
                async with client.stream(
                    "GET", download_url, headers=external_log_download_headers()
                ) as download:
                    download.raise_for_status()
                    async for chunk in download.aiter_bytes():
                        remaining = max_bytes - len(data)
                        if remaining <= 0:
                            truncated = True
                            break
                        data.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            truncated = True
                            break
            else:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    remaining = max_bytes - len(data)
                    if remaining <= 0:
                        truncated = True
                        break
                    data.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
    return bytes(data), truncated
