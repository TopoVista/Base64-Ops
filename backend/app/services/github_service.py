import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
import jwt as pyjwt
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
_GITHUB_APP_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_GITHUB_INSTALLATION_ID = re.compile(r"^[1-9][0-9]{0,18}$")


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


def github_app_credentials_configured() -> bool:
    """Return whether server-side GitHub App credentials are available.

    This only establishes that an App JWT can be minted. It does not advertise
    repository access, installation permissions, or any enabled feature.
    """

    settings = get_settings()
    return bool(
        _GITHUB_APP_ID.fullmatch(settings.github_app_id.strip())
        and settings.github_app_private_key.strip()
    )


def github_app_is_configured() -> bool:
    """Return whether the distinct candidate-verification App boundary is configured."""

    return bool(get_settings().github_candidate_verification_enabled and github_app_credentials_configured())


def github_incident_reporting_is_configured() -> bool:
    """Return whether opt-in GitHub Check reporting may be attempted."""

    return bool(get_settings().github_incident_reporting_enabled and github_app_credentials_configured())


def _github_app_jwt() -> str:
    """Create the short-lived JWT accepted by GitHub's App endpoints.

    The key is only consumed at the server boundary. Callers receive generic
    availability failures rather than malformed-key/provider details.
    """

    settings = get_settings()
    app_id = settings.github_app_id.strip()
    private_key = settings.github_app_private_key.replace("\\n", "\n").strip()
    if not (_GITHUB_APP_ID.fullmatch(app_id) and private_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub App credentials are not configured.",
        )
    now = int(utc_now().timestamp())
    try:
        token = pyjwt.encode(
            {"iat": now - 30, "exp": now + 540, "iss": app_id},
            private_key,
            algorithm="RS256",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub App authentication is unavailable.",
        ) from exc
    return token.decode("utf-8") if isinstance(token, bytes) else token


async def _github_app_api(method: str, path: str, **kwargs: Any) -> Any:
    """Call a GitHub App endpoint with a newly minted short-lived JWT."""

    headers = {
        "Authorization": f"Bearer {_github_app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    headers.update(kwargs.pop("headers", {}))
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, f"https://api.github.com{path}", headers=headers, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


async def validate_github_app_installation_repository(
    *, installation_id: str, repository_id: str
) -> dict[str, str]:
    """Prove that a configured App installation can access one known repo.

    ``repository_id`` is supplied only from a user-owned persisted session or
    previously server-validated automation setting. A browser-provided owner
    or repository name is never used as the App API target.
    """

    if not _GITHUB_INSTALLATION_ID.fullmatch(installation_id) or not repository_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid GitHub App installation.")
    for page in range(1, 6):
        data = await _github_app_api(
            "GET",
            f"/app/installations/{installation_id}/repositories",
            params={"per_page": 100, "page": page},
        )
        repositories = data.get("repositories", []) if isinstance(data, dict) else []
        if not isinstance(repositories, list):
            break
        for repository in repositories:
            if isinstance(repository, dict) and str(repository.get("id")) == repository_id:
                full_name = repository.get("full_name")
                clone_url = repository.get("clone_url")
                if isinstance(full_name, str) and isinstance(clone_url, str):
                    return {
                        "id": repository_id,
                        "fullName": full_name,
                        "cloneUrl": clone_url,
                        "defaultBranch": str(repository.get("default_branch") or "main"),
                    }
        if len(repositories) < 100:
            break
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="The configured GitHub App installation cannot access this repository.",
    )


async def create_github_installation_token(installation_id: str) -> str:
    """Mint an ephemeral installation token; never persist or return it."""

    if not _GITHUB_INSTALLATION_ID.fullmatch(installation_id):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid GitHub App installation.")
    data = await _github_app_api("POST", f"/app/installations/{installation_id}/access_tokens", json={})
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub App did not issue a verification token.",
        )
    return token


async def github_installation_api(installation_id: str, method: str, path: str, **kwargs: Any) -> Any:
    """Call a repository endpoint with an ephemeral installation token.

    This intentionally has a distinct entry point from :func:`github_api` so
    OAuth access cannot be accidentally promoted into App verification scope.
    """

    token = await create_github_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    headers.update(kwargs.pop("headers", {}))
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.request(method, f"https://api.github.com{path}", headers=headers, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


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
    return await _github_api_bytes_for_token(token, path, max_bytes=max_bytes)


async def github_installation_api_bytes(
    installation_id: str, path: str, *, max_bytes: int
) -> tuple[bytes, bool]:
    """Read bounded GitHub Actions content with an ephemeral App token.

    Candidate verification uses a distinct GitHub App boundary.  This helper
    deliberately shares the same strict signed-log redirect policy as the
    OAuth reader without ever accepting a browser-provided token or forwarding
    an installation token to a signed external download URL.
    """

    token = await create_github_installation_token(installation_id)
    return await _github_api_bytes_for_token(token, path, max_bytes=max_bytes)


async def _github_api_bytes_for_token(token: str, path: str, *, max_bytes: int) -> tuple[bytes, bool]:
    """Download only a bounded Actions response using a caller-owned token."""

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
