from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=1)
    avatar: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: str
    _id: str
    name: str
    email: EmailStr
    avatar: str | None = None
    githubConnected: bool = False
    createdAt: str | None = None
    updatedAt: str | None = None


class AuthResponse(BaseModel):
    message: str
    user: UserResponse
