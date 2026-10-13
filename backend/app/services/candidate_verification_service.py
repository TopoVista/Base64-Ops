"""Immutable verification records for exact delivery candidates.

This records what has and has not been independently tested. It deliberately
does not dispatch GitHub workflows under the OAuth-only integration.
"""

from __future__ import annotations

from app.schemas.delivery import DeliveryPlan
from app.services.delivery_service import DeliveryService
from app.utils.datetime import utc_now


def initial_candidate_verification(plan: DeliveryPlan) -> dict:
    return {
        "schemaVersion": "candidate-verification-v1",
        "status": "proposed",
        "verificationLevel": "not_independently_tested",
        "deliveryPlanId": plan.id,
        "baseSha": plan.base_sha,
        "candidatePatchHash": DeliveryService.diff_hash(plan),
        "workflowIdentity": None,
        "commands": [],
        "createdAt": utc_now(),
        "limitations": [
            "No GitHub App verification-dispatch permission is configured.",
            "Static validation and later delivery CI observation are not targeted candidate verification.",
        ],
    }
