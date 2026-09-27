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


class DiagnosticStreamRequest(BaseModel):
    """Bounded, untrusted CI output supplied by the authenticated workspace user."""

    sessionSlugId: str | None = Field(default=None, max_length=160)
    failedLog: str = Field(min_length=1, max_length=500_000)
    gitDiff: str = Field(default="", max_length=500_000)
    filePath: str | None = Field(default=None, max_length=1024)
    originalContent: str | None = Field(default=None, max_length=250_000)


class ApplyApprovedPatchRequest(BaseModel):
    """A browser may execute only an already-bound, pending approval."""

    slugId: str = Field(min_length=1, max_length=160)
    approvalId: str = Field(min_length=1, max_length=160)
