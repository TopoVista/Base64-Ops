"""Deterministic applicability checks for historical workflow path failures."""

import re
from enum import StrEnum

from pydantic import BaseModel, Field


class CIFailureApplicability(StrEnum):
    CURRENT = "current"
    HISTORICAL_FIXED = "historical_fixed"
    HISTORICAL_NEEDS_VERIFICATION = "historical_needs_verification"


class CIApplicabilityResult(BaseModel):
    status: CIFailureApplicability
    reason: str
    failed_run_sha: str
    current_head_sha: str | None = None
    compared_paths: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


_WORKING_DIRECTORY = re.compile(r'^\s*working-directory:\s*["\']?([^\s"\'#]+)', re.MULTILINE)


def workflow_path_applicability(
    *, failed_workflow: str, current_workflow: str | None, failed_run_sha: str, current_head_sha: str | None
) -> CIFailureApplicability:
    return assess_workflow_path(
        failed_workflow=failed_workflow,
        current_workflow=current_workflow,
        failed_run_sha=failed_run_sha,
        current_head_sha=current_head_sha,
    ).status


def assess_workflow_path(
    *,
    failed_workflow: str,
    current_workflow: str | None,
    failed_run_sha: str,
    current_head_sha: str | None,
    evidence_ids: list[str] | None = None,
) -> CIApplicabilityResult:
    """Compare the failing working-directory condition, not whole YAML files."""
    common = {
        "failed_run_sha": failed_run_sha,
        "current_head_sha": current_head_sha,
        "compared_paths": ["working-directory"],
        "evidence_ids": evidence_ids or [],
    }
    if not current_head_sha or current_workflow is None:
        return CIApplicabilityResult(
            status=CIFailureApplicability.HISTORICAL_NEEDS_VERIFICATION,
            reason="Current workflow state is unavailable for comparison.",
            **common,
        )
    if failed_run_sha == current_head_sha:
        return CIApplicabilityResult(
            status=CIFailureApplicability.CURRENT,
            reason="The failed-run revision is the current revision.",
            **common,
        )
    failed = _WORKING_DIRECTORY.search(failed_workflow)
    current = _WORKING_DIRECTORY.search(current_workflow)
    # Only a changed relevant condition proves this particular path failure is
    # fixed.  Other workflow edits remain CURRENT until separately verified.
    if failed and current and failed.group(1) != current.group(1):
        return CIApplicabilityResult(
            status=CIFailureApplicability.HISTORICAL_FIXED,
            reason="The workflow working-directory changed after the failed run.",
            **common,
        )
    return CIApplicabilityResult(
        status=CIFailureApplicability.CURRENT,
        reason="The relevant workflow working-directory remains unchanged.",
        **common,
    )
