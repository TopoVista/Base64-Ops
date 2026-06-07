from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    id: str | None = None
    role: Literal["user", "assistant", "system"]
    content: str = ""
    createdAt: str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)


class SessionChatRequest(BaseModel):
    slugId: str
    repoUrl: str
    defaultBranch: str | None = "main"
    message: str | None = None
    messages: list[Any] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    editedArgs: dict[str, Any] | None = None
    note: str | None = None


class CreatePullRequestRequest(BaseModel):
    title: str | None = None
    body: str | None = None


class RunbookRequest(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
