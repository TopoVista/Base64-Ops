from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.api.deps import get_current_user
from app.services.github_service import (
    complete_github_oauth,
    create_connect_url,
    disconnect_github,
    list_repositories,
)

router = APIRouter()


@router.get("/connect")
async def connect(redirectTo: str | None = None, user: dict = Depends(get_current_user)) -> dict:
    return {"url": create_connect_url(user["_id"], redirectTo)}


@router.get("/callback")
async def callback(code: str, state: str) -> RedirectResponse:
    redirect_to = await complete_github_oauth(code, state)
    return RedirectResponse(redirect_to)


@router.get("/repos")
async def repos(user: dict = Depends(get_current_user)) -> dict:
    return {"repos": await list_repositories(user["_id"])}


@router.delete("/disconnect")
async def disconnect(user: dict = Depends(get_current_user)) -> dict:
    return await disconnect_github(user["_id"])
