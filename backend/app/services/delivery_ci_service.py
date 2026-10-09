"""Read-only verification of CI after an approved delivery reaches GitHub."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.git.actions import WorkflowRunSummary


@dataclass(frozen=True)
class DeliveryCIVerdict:
    status: str
    run_ids: list[int]
    message: str


def evaluate_delivery_ci(*, commit_sha: str | None, runs: Iterable[WorkflowRunSummary]) -> DeliveryCIVerdict:
    """Return a conservative CI verdict using exact delivery commit correlation.

    A branch-matching run is not enough: only Actions runs whose head SHA is
    exactly the delivery commit can change the result. No matching run means
    pending, not passed.
    """

    if not commit_sha:
        return DeliveryCIVerdict("unknown", [], "Delivery commit SHA was unavailable for CI verification.")
    matched = [run for run in runs if run.head_sha == commit_sha]
    if not matched:
        return DeliveryCIVerdict("pending", [], "Waiting for a GitHub Actions run for the delivery commit.")
    run_ids = [run.id for run in matched]
    if any(run.status != "completed" for run in matched):
        return DeliveryCIVerdict("running", run_ids, "GitHub Actions is still running for the delivery commit.")
    conclusions = {run.conclusion for run in matched}
    if "failure" in conclusions or "timed_out" in conclusions or "cancelled" in conclusions:
        return DeliveryCIVerdict("failed", run_ids, "At least one GitHub Actions run failed for the delivery commit.")
    if conclusions and conclusions.issubset({"success", "neutral", "skipped"}):
        return DeliveryCIVerdict("passed", run_ids, "All observed GitHub Actions runs passed for the delivery commit.")
    return DeliveryCIVerdict("unknown", run_ids, "GitHub Actions completed with an unrecognized conclusion.")
