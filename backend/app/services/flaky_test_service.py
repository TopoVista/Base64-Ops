"""Bounded, evidence-scoped flaky-test observations.

This module deliberately treats GitHub Actions outcomes as observations, not
test rerun commands.  A workflow-level success cannot prove that a single test
is flaky, so every result preserves the scope and limitations of its evidence.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.db.mongo import get_db
from app.utils.datetime import utc_now
from app.utils.ids import new_id


class FlakyTestAssessment(StrEnum):
    """Conservative classifications derived from bounded Actions history."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SUSPECTED_FLAKINESS = "suspected_flakiness"
    REPRODUCIBLE_REGRESSION = "reproducible_regression"


class FlakyTestObservation(BaseModel):
    """Safe public shape for one test identity and its workflow history."""

    schema_version: str = "flaky-test-observation-v1"
    test_identifier: str
    workflow_key: str
    head_sha: str
    assessment: FlakyTestAssessment
    same_sha_failure_count: int = 0
    same_sha_success_count: int = 0
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def build_flaky_test_observations(context: Any) -> list[FlakyTestObservation]:
    """Build deterministic observations from exact test IDs and Actions history.

    The test identifier is observed in a failed log.  The outcome counts are
    only workflow-level signals, because GitHub Actions metadata does not
    expose individual test results for successful runs.  That distinction is
    persisted rather than inflated into a test-level causal claim.
    """

    identifiers = list(
        dict.fromkeys(str(value) for value in getattr(context, "failed_test_identifiers", []) if value)
    )[:10]
    history = getattr(context, "history", None)
    workflow_key = str(
        getattr(context, "workflow_path", None) or getattr(context, "workflow_name", None) or "unknown-workflow"
    )
    head_sha = str(getattr(context, "head_sha", ""))
    failures = int(getattr(history, "same_sha_failure_count", 0) or 0)
    successes = int(getattr(history, "same_sha_success_count", 0) or 0)
    evidence_ids = list(
        dict.fromkeys(
            [
                *getattr(context, "evidence_ids", []),
                *getattr(context, "history_evidence_ids", []),
            ]
        )
    )[:50]
    observations: list[FlakyTestObservation] = []
    for identifier in identifiers:
        if failures >= 2 and successes >= 1:
            assessment = FlakyTestAssessment.SUSPECTED_FLAKINESS
            limitations = [
                "The mixed outcomes are workflow-level evidence; successful runs did not identify "
                "this test individually.",
                "This observation does not trigger an automatic rerun, quarantine, or repair.",
            ]
        elif failures >= 2:
            assessment = FlakyTestAssessment.REPRODUCIBLE_REGRESSION
            limitations = [
                "Repeated failures are workflow-level evidence and do not prove this test alone "
                "caused the run to fail.",
            ]
        else:
            assessment = FlakyTestAssessment.INSUFFICIENT_EVIDENCE
            limitations = [
                "At least two comparable failed workflow outcomes are required before a "
                "repeated-failure classification.",
            ]
        observations.append(
            FlakyTestObservation(
                test_identifier=identifier,
                workflow_key=workflow_key,
                head_sha=head_sha,
                assessment=assessment,
                same_sha_failure_count=failures,
                same_sha_success_count=successes,
                evidence_ids=evidence_ids,
                limitations=limitations,
            )
        )
    return observations


class FlakyTestObservationService:
    """Persist and retrieve small tenant-scoped observation summaries."""

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.flaky_test_observations.create_index(
            [("userId", 1), ("repositoryId", 1), ("workflowKey", 1), ("testIdentifier", 1), ("headSha", 1)],
            unique=True,
            name="flaky_test_observation_once",
        )
        await db.flaky_test_observations.create_index(
            [("userId", 1), ("updatedAt", -1)], name="flaky_test_observation_user_timeline"
        )

    async def record(
        self,
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
        incident_id: str,
        context: Any,
    ) -> list[dict[str, Any]]:
        """Upsert observations without storing raw logs or provider payloads."""

        now = utc_now()
        persisted: list[dict[str, Any]] = []
        for observation in build_flaky_test_observations(context):
            value = observation.model_dump(mode="json")
            fingerprint = hashlib.sha256(
                "\x1f".join(
                    [user_id, repository_id, value["workflow_key"], value["test_identifier"], value["head_sha"]]
                ).encode("utf-8")
            ).hexdigest()
            record_id = f"flt_{fingerprint[:40]}"
            document = {
                "userId": user_id,
                "sessionId": session_id,
                "repositoryId": repository_id,
                "workflowKey": value["workflow_key"],
                "testIdentifier": value["test_identifier"],
                "headSha": value["head_sha"],
                "assessment": value["assessment"],
                "sameShaFailureCount": value["same_sha_failure_count"],
                "sameShaSuccessCount": value["same_sha_success_count"],
                "limitations": value["limitations"],
                "updatedAt": now,
            }
            await get_db().flaky_test_observations.update_one(
                {
                    "userId": user_id,
                    "repositoryId": repository_id,
                    "workflowKey": value["workflow_key"],
                    "testIdentifier": value["test_identifier"],
                    "headSha": value["head_sha"],
                },
                {
                    "$set": document,
                    "$setOnInsert": {"_id": record_id or new_id("flt_"), "createdAt": now},
                    "$addToSet": {"incidentIds": incident_id, "evidenceIds": {"$each": value["evidence_ids"]}},
                },
                upsert=True,
            )
            persisted.append(
                {
                    "id": record_id,
                    "testIdentifier": value["test_identifier"],
                    "assessment": value["assessment"],
                    "sameShaFailureCount": value["same_sha_failure_count"],
                    "sameShaSuccessCount": value["same_sha_success_count"],
                    "limitations": value["limitations"],
                }
            )
        return persisted
