from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import get_current_user
from app.services.github_service import (
    complete_github_oauth,
    create_connect_url,
    disconnect_github,
    list_repositories,
)
from app.services.incident_service import GitHubWebhookService

router = APIRouter()


@router.get("/connect")
async def connect(redirectTo: str | None = None, user: dict = Depends(get_current_user)) -> dict:
    return {"url": await create_connect_url(user["_id"], redirectTo)}


@router.get("/callback")
async def callback(code: str, state: str) -> RedirectResponse:
    redirect_to = await complete_github_oauth(code, state)
    return RedirectResponse(redirect_to)


@router.post("/webhooks", status_code=status.HTTP_202_ACCEPTED)
async def webhook(request: Request) -> dict:
    """Receive only signed GitHub webhook envelopes.

    This intentionally has no Clerk dependency: GitHub authenticates the raw
    body with `X-Hub-Signature-256`, while all eventual user/repository access
    is resolved from opt-in records persisted by the authenticated API.
    """

    return await GitHubWebhookService().accept_delivery(
        body=await request.body(),
        event_name=request.headers.get("X-GitHub-Event", ""),
        action=request.headers.get("X-GitHub-Event-Action"),
        delivery_id=request.headers.get("X-GitHub-Delivery", ""),
        signature=request.headers.get("X-Hub-Signature-256", ""),
    )


@router.get("/repos")
async def repos(user: dict = Depends(get_current_user)) -> dict:
    return {"repos": await list_repositories(user["_id"])}


@router.delete("/disconnect")
async def disconnect(user: dict = Depends(get_current_user)) -> dict:
    return await disconnect_github(user["_id"])
