from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


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
    # A CI remediation can be constrained to a file selected from server-side
    # CI evidence. It is not a filesystem authorization mechanism: the patch
    # engine still validates the path and evidence before any plan exists.
    patchTargetPath: str | None = Field(default=None, max_length=1_024)


class RepositoryIndexRequest(BaseModel):
    """Repository identity needed when indexing before the first chat request."""

    repoUrl: str = Field(min_length=1, max_length=2_048)
    defaultBranch: str | None = Field(default="main", max_length=256)


class CodeEditProposalRequest(BaseModel):
    """A browser draft that must still become an approval-bound DeliveryPlan."""

    path: str = Field(min_length=1, max_length=1_024)
    expectedOriginalHash: str = Field(min_length=64, max_length=64)
    proposedContent: str = Field(min_length=1, max_length=500_000)
    commitMessage: str | None = Field(default=None, max_length=120)


class CommitMessageUpdateRequest(BaseModel):
    """Rebind a reviewed delivery to a deliberately chosen commit message."""

    commitMessage: str = Field(min_length=1, max_length=120)


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

    sessionSlugId: str | None = Field(
        default=None, max_length=160, validation_alias=AliasChoices("sessionSlugId", "session_slug_id")
    )
    failedLog: str = Field(
        min_length=1, max_length=500_000, validation_alias=AliasChoices("failedLog", "failed_log")
    )
    gitDiff: str = Field(
        default="", max_length=500_000, validation_alias=AliasChoices("gitDiff", "git_diff")
    )
    filePath: str | None = Field(default=None, max_length=1024)
    originalContent: str | None = Field(default=None, max_length=250_000)
    generateFix: bool = False


class ApplyApprovedPatchRequest(BaseModel):
    """A browser may execute only an already-bound, pending approval."""

    slugId: str = Field(min_length=1, max_length=160)
    approvalId: str = Field(min_length=1, max_length=160)
