from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunTrace(BaseModel):
    id: str
    user_id: str
    session_id: str
    run_id: str
    repository_id: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "running"
    total_duration_ms: int | None = None
    model_usage: dict[str, Any] = Field(default_factory=dict)
    error_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceSpan(BaseModel):
    id: str
    trace_id: str
    parent_span_id: str | None = None
    name: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    status: Literal["ok", "error", "cancelled"] = "ok"
    safe_input_summary: dict[str, Any] = Field(default_factory=dict)
    safe_output_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
