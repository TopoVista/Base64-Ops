from typing import Any

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


def create_connect_url(user_id: str, redirect_to: str | None = None) -> str:
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub OAuth is not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in backend/.env.",
        )
    state = sign_oauth_state({"userId": user_id, "redirectTo": redirect_to})
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
    state_payload = verify_oauth_state(state)
    user_id = state_payload["userId"]
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
    settings = get_settings()
    return state_payload.get("redirectTo") or f"{settings.frontend_origin}/new?github=connected"


async def get_github_access_token(user_id: str) -> str:
    account = await get_db().github_accounts.find_one({"userId": user_id})
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GitHub account not connected")
    return decrypt_secret(account["accessToken"])


async def list_repositories(user_id: str) -> list[dict[str, Any]]:
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


async def disconnect_github(user_id: str) -> dict[str, bool]:
    await get_db().github_accounts.delete_one({"userId": user_id})
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
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        async with client.stream(
            "GET",
            f"https://api.github.com{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        ) as response:
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
