from fastapi import APIRouter, Depends, Response

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import get_me, login_user, register_user

router = APIRouter()


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.jwt_expires_minutes * 60,
    )


@router.post("/register")
async def register(payload: RegisterRequest, response: Response) -> dict:
    user, token = await register_user(payload)
    set_auth_cookie(response, token)
    return {"message": "User registered successfully", "user": user}


@router.post("/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    user, token = await login_user(payload)
    set_auth_cookie(response, token)
    return {"message": "User logged in successfully", "user": user}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"message": "User retrieved successfully", "user": await get_me(user)}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie("access_token")
    return {"message": "Logged out successfully"}
