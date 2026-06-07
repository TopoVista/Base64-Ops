from fastapi import HTTPException, status

from app.core.security import create_access_token, hash_password, verify_password
from app.db.mongo import get_db
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.serializers import public_user
from app.utils.datetime import utc_now
from app.utils.ids import new_id


async def register_user(payload: RegisterRequest) -> tuple[dict, str]:
    db = get_db()
    normalized_email = payload.email.lower()
    existing = await db.users.find_one({"email": normalized_email})
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    now = utc_now()
    user = {
        "_id": new_id("usr_"),
        "name": payload.name,
        "email": normalized_email,
        "avatar": payload.avatar,
        "passwordHash": hash_password(payload.password),
        "createdAt": now,
        "updatedAt": now,
    }
    await db.users.insert_one(user)
    token = create_access_token(user["_id"])
    return public_user(user, github_connected=False), token


async def login_user(payload: LoginRequest) -> tuple[dict, str]:
    db = get_db()
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user["passwordHash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    github_connected = await db.github_accounts.count_documents({"userId": user["_id"]}) > 0
    token = create_access_token(user["_id"])
    return public_user(user, github_connected=github_connected), token


async def get_me(user: dict) -> dict:
    github_connected = await get_db().github_accounts.count_documents({"userId": user["_id"]}) > 0
    return public_user(user, github_connected=github_connected)
