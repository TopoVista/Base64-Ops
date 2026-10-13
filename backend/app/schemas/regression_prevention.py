"""Typed, conservative advisories derived from historical CI incidents.

The models deliberately describe an advisory, rather than a prediction or a
policy gate.  Historical incident correlation is useful review context, but it
cannot prove a new change will fail or grant/deny a delivery approval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RegressionPreventionAdvisory(BaseModel):
    """A repeated, path-scoped failure pattern eligible for human review."""

    path: str
    failure_category: str
    historical_incident_ids: list[str] = Field(default_factory=list, max_length=12)
    historical_run_shas: list[str] = Field(default_factory=list, max_length=12)
    occurrence_count: int = Field(ge=2)
    message: str
    recommendation: str


class RegressionPreventionReport(BaseModel):
    """Tenant-safe result for one exact DeliveryPlan.

    ``available`` means the analysis completed, not that a regression is
    expected.  ``insufficient_history`` means no pattern cleared the explicit
    two-independent-run threshold.
    """

    delivery_plan_id: str
    plan_base_sha: str
    status: Literal["available", "insufficient_history"]
    changed_paths: list[str] = Field(default_factory=list)
    advisories: list[RegressionPreventionAdvisory] = Field(default_factory=list)
    threshold_description: str
    limitations: list[str] = Field(default_factory=list)
    analyzed_at: datetime
