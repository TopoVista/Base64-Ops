"""Immutable verification records for exact delivery candidates.

This records what has and has not been independently tested. It deliberately
does not dispatch GitHub workflows under the OAuth-only integration.
"""

from __future__ import annotations

from typing import Any

from app.schemas.delivery import DeliveryPlan
from app.utils.datetime import utc_now


def initial_candidate_verification(
    plan: DeliveryPlan,
    *,
    candidate_patch_hash: str,
    static_validation: list[dict[str, Any]] | None = None,
) -> dict:
    """Create the immutable verification contract for an exact candidate.

    The caller owns patch hashing so this small, persistence-safe module does
    not depend on the delivery service that invokes it.  The record is an
    honest statement of what was *not* remotely verified; it is never a
    substitute for an immutable GitHub check result.
    """

    validation = static_validation or []
    required_failures = [
        item
        for item in validation
        if item.get("status") in {"failed", "error"} and item.get("step_id") != "review"
    ]
    static_validation_passed = bool(validation) and not required_failures
    return {
        "schemaVersion": "candidate-verification-v1",
        "status": "static_validation_passed" if static_validation_passed else "proposed",
        "verificationLevel": "static_validation" if static_validation_passed else "not_independently_tested",
        "deliveryPlanId": plan.id,
        "baseSha": plan.base_sha,
        "candidatePatchHash": candidate_patch_hash,
        "workflowIdentity": None,
        "commands": [],
        # Keep an auditable, command-free outcome summary. Commands are owned
        # by the existing registry and are never reconstructed from this
        # candidate record or from untrusted workflow content.
        "staticValidation": [
            {
                "stepId": str(item.get("step_id") or ""),
                "status": str(item.get("status") or "unknown"),
                "summary": str(item.get("summary") or "")[:300],
                "durationMs": item.get("duration_ms") if isinstance(item.get("duration_ms"), int) else None,
            }
            for item in validation[:20]
        ],
        "createdAt": utc_now(),
        "limitations": [
            "No GitHub App verification-dispatch permission is configured.",
            "Static validation and later delivery CI observation are not targeted candidate verification.",
        ],
    }
