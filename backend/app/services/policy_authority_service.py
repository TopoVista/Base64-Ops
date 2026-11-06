"""Deterministic authorization authority for remediation delivery.

Models may suggest patches; they never decide if a patch can be delivered.
This authority derives a canonical action envelope from persisted plan data,
evaluates versioned policy, and persists a decision that the executor checks
again immediately before any Git mutation.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.db.mongo import get_db
from app.schemas.delivery import DeliveryPlan
from app.services.assurance_ledger_service import AssuranceLedgerService, digest
from app.utils.datetime import utc_now
from app.utils.ids import new_id

_ALLOWED_ACTIONS = frozenset({"create_draft_pull_request", "dispatch_candidate_verification"})
_HIGH_ASSURANCE_PATH_MARKERS = (".github/workflows/", "terraform", "infra/", "auth", "security")


class PolicyAuthorityService:
    """Policy is deterministic, versioned, and deliberately model-independent."""

    async def ensure_indexes(self) -> None:
        collection = get_db().policy_decisions
        await collection.create_index("decisionHash", unique=True, name="policy_decision_hash_once")
        await collection.create_index([("approvalId", 1), ("status", 1)], name="policy_approval_status")
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("runId", 1), ("createdAt", -1)], name="policy_run_timeline"
        )

    def canonical_action(self, *, plan: DeliveryPlan, action_type: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if action_type not in _ALLOWED_ACTIONS:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Policy does not authorize this action.")
        if not plan.ledger_manifest_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A signed evidence manifest is required before authorization.",
            )
        path_list = sorted(change.path for change in plan.files)
        high_assurance_surface = any(
            marker in path.lower() for path in path_list for marker in _HIGH_ASSURANCE_PATH_MARKERS
        )
        strict = get_settings().policy_strict_separation_of_duties
        requires_candidate = bool(
            get_settings().policy_require_candidate_verification_for_high_risk
            and action_type != "dispatch_candidate_verification"
            and (plan.risk_level in {"high", "critical"} or high_assurance_surface)
        )
        reviewer_quorum = 2 if strict and (plan.risk_level in {"high", "critical"} or high_assurance_surface) else 1
        return {
            "schemaVersion": "base64-policy-v1",
            "policyVersion": get_settings().policy_authority_version,
            "actionType": action_type,
            "userId": plan.user_id,
            "sessionId": plan.session_id,
            "runId": plan.run_id,
            "repositoryId": plan.repository_id,
            "deliveryPlanId": plan.id,
            "baseSha": plan.base_sha,
            "ledgerManifestHash": plan.ledger_manifest_hash,
            "diffHash": AssuranceLedgerService.plan_diff_hash(plan),
            "arguments": arguments,
            "riskLevel": plan.risk_level,
            "paths": path_list,
            "requirements": {
                "approvalQuorum": reviewer_quorum,
                "distinctApprovers": strict,
                "candidateVerification": requires_candidate,
            },
        }

    async def authorize_approval(
        self, *, approval: dict[str, Any], plan: DeliveryPlan, approver_id: str
    ) -> dict[str, Any]:
        """Authorize a pending approval only after deterministic policy checks."""

        action = self.canonical_action(
            plan=plan,
            action_type=str(approval["action_type"]),
            arguments=approval.get("canonical_arguments") or {},
        )
        if approval.get("ledger_manifest_hash") != plan.ledger_manifest_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approval is not bound to the current signed evidence manifest.",
            )
        requirements = action["requirements"]
        # A single ApprovalRecord can represent exactly one human decision.
        # Never claim a quorum that this persistence model cannot prove.
        # Enterprise quorum support must add immutable per-reviewer votes and
        # a policy decision bound to that vote set; until then, fail closed.
        if requirements["approvalQuorum"] > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This action requires a reviewer quorum that is not configured for this approval workflow.",
            )
        proposer_id = str(approval.get("user_id") or approval.get("userId") or "")
        if requirements["distinctApprovers"] and proposer_id == approver_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This policy requires a reviewer distinct from the remediation proposer.",
            )
        if requirements["candidateVerification"]:
            candidate = await get_db().verification_runs.find_one(
                {
                    "userId": plan.user_id,
                    "sessionId": plan.session_id,
                    "deliveryPlanId": plan.id,
                    # CandidateVerificationService's terminal successful
                    # state is ``succeeded``. Retain ``passed`` only for
                    # records created by the earlier pre-observer contract;
                    # without this compatibility gate a genuine targeted
                    # verification could never satisfy high-risk policy.
                    "status": {"$in": ["succeeded", "passed"]},
                    "candidatePatchHash": AssuranceLedgerService.plan_diff_hash(plan),
                },
                {"_id": 1},
            )
            if not candidate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Policy requires a successful exact candidate verification before delivery.",
                )
        decision_hash = digest({"approvalId": approval["id"], "approverId": approver_id, "action": action})
        existing = await get_db().policy_decisions.find_one({"decisionHash": decision_hash})
        if existing:
            return existing
        decision_id = new_id("pdc_")
        decision = {
            "_id": decision_id,
            "id": decision_id,
            "decisionHash": decision_hash,
            "status": "authorized",
            "approvalId": str(approval["id"]),
            "userId": plan.user_id,
            "sessionId": plan.session_id,
            "runId": plan.run_id,
            "deliveryPlanId": plan.id,
            "approverId": approver_id,
            "action": action,
            "createdAt": utc_now(),
        }
        await get_db().policy_decisions.insert_one(decision)
        receipt = await AssuranceLedgerService().record_policy_receipt(decision)
        await get_db().policy_decisions.update_one(
            {"_id": decision["_id"]},
            {"$set": {"ledgerReceiptId": receipt["id"], "ledgerManifestHash": receipt["manifestHash"]}},
        )
        return {
            **decision,
            "ledgerReceiptId": receipt["id"],
            "ledgerManifestHash": receipt["manifestHash"],
        }

    async def assert_execution_authorized(self, *, approval: dict[str, Any], plan: DeliveryPlan) -> dict[str, Any]:
        decision_id = approval.get("policyDecisionId")
        if not isinstance(decision_id, str) or not decision_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No policy authorization exists for this delivery.",
            )
        decision = await get_db().policy_decisions.find_one(
            {
                "id": decision_id,
                "approvalId": str(approval["id"]),
                "userId": plan.user_id,
                "status": "authorized",
            }
        )
        if not decision:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Policy authorization is unavailable or revoked.",
            )
        receipt_id = decision.get("ledgerReceiptId")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Policy authorization is missing its signed receipt.",
            )
        receipt = await AssuranceLedgerService().get_verified_receipt(
            receipt_id=receipt_id, user_id=plan.user_id, session_id=plan.session_id
        )
        if not receipt or not receipt.get("verified") or not receipt.get("chainVerified"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Policy authorization receipt chain integrity could not be verified.",
            )
        manifest = receipt.get("manifest") or {}
        if (
            manifest.get("kind") != "policy_authorization"
            or manifest.get("policyDecisionId") != decision["id"]
            or manifest.get("decisionHash") != decision.get("decisionHash")
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Policy authorization receipt does not bind this decision.",
            )
        expected = self.canonical_action(
            plan=plan,
            action_type=str(approval["action_type"]),
            arguments=approval.get("canonical_arguments") or {},
        )
        if decision.get("action") != expected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Policy authorization no longer matches delivery.",
            )
        return decision
