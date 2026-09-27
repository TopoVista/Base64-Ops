from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutionRequest(BaseModel):
    id: str
    run_id: str
    repository_id: str
    purpose: Literal["validation", "test", "build", "static_analysis"]
    command_id: str
    executable: str
    arguments: list[str] = Field(default_factory=list)
    working_directory: str
    timeout_seconds: int = 120
    network_policy: Literal["disabled", "restricted", "allowed"] = "disabled"
    environment_allowlist: list[str] = Field(default_factory=list)
    max_stdout_bytes: int = 100_000
    max_stderr_bytes: int = 100_000
    cpu_limit: float | None = 1.5
    memory_limit_mb: int | None = 1024
    read_only_repository: bool = False


class ExecutionResult(BaseModel):
    request_id: str
    status: Literal["success", "failed", "timeout", "policy_denied", "killed", "runtime_error"]
    exit_code: int | None = None
    duration_ms: int
    stdout_summary: str = ""
    stderr_summary: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = None
    failure_type: str | None = None


class ValidatorDefinition(BaseModel):
    id: str
    executable: str
    argument_builder: str
    executes_repository_code: bool
    requires_network: bool = False
    default_timeout_seconds: int = 120
    default_memory_limit_mb: int = 1024
    allowed_working_directories: list[str] = Field(default_factory=list)
