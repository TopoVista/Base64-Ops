from enum import StrEnum

from pydantic import BaseModel, Field


class ToolRisk(StrEnum):
    READ = "read"
    PROPOSAL = "proposal"
    WRITE = "write"
    PRIVILEGED = "privileged"


class ToolPolicy(BaseModel):
    tool_name: str
    risk_level: ToolRisk
    requires_approval: bool
    timeout_seconds: int | None = None
    requires_validation: bool = False
    allowed_resources: list[str] = Field(default_factory=list)
    description: str | None = None
