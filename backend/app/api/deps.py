from typing import Any

from fastapi import Cookie, HTTPException, Request, status

from app.core.security import decode_access_token
from app.db.mongo import get_db


async def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    bearer = request.headers.get("Authorization", "")
    token = access_token
    if bearer.startswith("Bearer "):
        token = bearer.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = await get_db().users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
