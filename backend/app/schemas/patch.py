from typing import Literal

from pydantic import BaseModel, Field


class FileEditIntent(BaseModel):
    path: str
    operation: Literal["modify", "create", "delete"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    expected_original_hash: str | None = None
    proposed_content: str | None = None


class PatchProposal(BaseModel):
    summary: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    edits: list[FileEditIntent] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class ChangeSurface(BaseModel):
    surface: str
    files: list[str]
    reasons: list[str]


class RepositoryCapability(BaseModel):
    kind: str
    source: str
    commands: list[str]
