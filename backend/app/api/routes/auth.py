"""
Auth routes — Clerk edition.

With Clerk handling sign-up/sign-in on the frontend, the backend only
needs one endpoint:

    POST /api/auth/me
        • Verifies the Clerk JWT (via get_current_user dependency).
        • Upserts the user in MongoDB (first call creates the record).
        • Returns the backend user profile including githubConnected status.

    GET /api/auth/me
        • Same as POST but for read-only fetches after the user is already
          synced (used by useBackendUser hook polling).
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.db.mongo import get_db
from app.services.serializers import public_user

router = APIRouter()


async def _me_response(user: dict) -> dict:
    github_connected = await get_db().github_accounts.count_documents({"userId": user["_id"]}) > 0
    return {"message": "User retrieved successfully", "user": public_user(user, github_connected)}


@router.post("/me")
async def sync_user(user: dict = Depends(get_current_user)) -> dict:
    """Upsert + return the backend profile for the authenticated Clerk user."""
    return await _me_response(user)


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)) -> dict:
    """Return the backend profile for the authenticated Clerk user."""
    return await _me_response(user)
