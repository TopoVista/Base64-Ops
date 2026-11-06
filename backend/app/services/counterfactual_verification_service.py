"""Bounded counterfactual evidence from incident capsules and exact candidates.

This service does not equate a targeted candidate workflow with a proof of
correctness.  It records the narrower observable: a patch with digest P was
constructed from base SHA S, an incident at S was observed to fail, and a
configured candidate workflow later succeeded or did not.  Historical capsules
outside S remain explicitly unreplayed until an isolated replay runner exists.
"""

from __future__ import annotations

from typing import Any

from app.db.mongo import get_db
from app.schemas.delivery import DeliveryPlan
from app.services.assurance_ledger_service import AssuranceLedgerService, digest
from app.services.delivery_service import DeliveryService
from app.utils.datetime import utc_now
from app.utils.ids import new_id

_MAX_HISTORICAL_CAPSULES = 5


class CounterfactualVerificationService:
    async def ensure_indexes(self) -> None:
        collection = get_db().counterfactual_verifications
        await collection.create_index("analysisKey", unique=True, name="counterfactual_analysis_once")
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("deliveryPlanId", 1), ("createdAt", -1)],
            name="counterfactual_plan_timeline",
        )

    async def analyze_for_plan(self, *, user_id: str, session_id: str, plan: DeliveryPlan) -> dict[str, Any]:
        """Persist a matrix for one exact plan without dispatching any workflow.

        Candidate execution remains the separately approved GitHub App path.
        This method merely binds the already-observed result to eligible
        incident capsules and signs the resulting evidence statement.
        """

        db = get_db()
        patch_digest = DeliveryService.diff_hash(plan)
        target_query = {
            "userId": user_id,
            "sessionId": session_id,
            "headSha": plan.base_sha,
            "capsuleManifestHash": {"$exists": True, "$ne": None},
            # Legacy webhook incidents use GitHub's numeric repository ID,
            # while DeliveryPlan uses the canonical clone URL. Either can be
            # trusted only after the session boundary above has been checked.
            "$or": [{"repositoryId": plan.repository_id}, {"repositoryUrl": plan.repository_id}],
        }
        target = await db.incidents.find_one(target_query)
        historical = await (
            db.incidents.find(
                {
                    "userId": user_id,
                    "sessionId": session_id,
                    "headSha": {"$ne": plan.base_sha},
                    "capsuleManifestHash": {"$exists": True, "$ne": None},
                    "relevantPaths": {"$in": [change.path for change in plan.files]},
                    "$or": [{"repositoryId": plan.repository_id}, {"repositoryUrl": plan.repository_id}],
                },
                {"_id": 1, "headSha": 1, "capsuleManifestHash": 1, "status": 1, "updatedAt": 1},
            )
            .sort("updatedAt", -1)
            .limit(_MAX_HISTORICAL_CAPSULES)
            .to_list(_MAX_HISTORICAL_CAPSULES)
        )
        candidate = await db.verification_runs.find_one(
            {
                "userId": user_id,
                "sessionId": session_id,
                "deliveryPlanId": plan.id,
                "baseSha": plan.base_sha,
                "candidatePatchHash": patch_digest,
                "status": "succeeded",
            },
            {"_id": 1, "id": 1, "verificationLevel": 1, "workflowPath": 1, "githubRunId": 1},
        )
        matrix: list[dict[str, Any]] = []
        if target:
            matrix.append(
                {
                    "incidentId": str(target["_id"]),
                    "capsuleManifestHash": str(target["capsuleManifestHash"]),
                    "baseSha": plan.base_sha,
                    "baseline": "failure_observed",
                    "patched": (
                        "targeted_verification_passed" if candidate else "candidate_not_successful_or_unobserved"
                    ),
                    "replayed": bool(candidate),
                    "scope": "exact_failed_revision_targeted_workflow" if candidate else "not_replayed",
                }
            )
        for incident in historical:
            matrix.append(
                {
                    "incidentId": str(incident["_id"]),
                    "capsuleManifestHash": str(incident["capsuleManifestHash"]),
                    "baseSha": str(incident.get("headSha") or ""),
                    "baseline": "failure_observed",
                    "patched": "not_replayed",
                    "replayed": False,
                    "scope": "historical_capsule_selected_not_replayed",
                }
            )
        status = "targeted_evidence_available" if target and candidate else "inconclusive"
        limitations = [
            "A targeted candidate workflow is not proof that the original failed workflow or full CI would pass.",
            (
                "Historical capsules are selected by affected paths but are not replayed until an isolated replay "
                "runner is configured."
            ),
        ]
        if not target:
            limitations.append("No signed incident capsule was available at this plan's exact base SHA.")
        if not candidate:
            limitations.append(
                "No successful exact candidate verification was observed for this patch digest and base SHA."
            )
        verification = {
            "verificationRunId": str(candidate.get("id") or candidate.get("_id")) if candidate else None,
            "verificationLevel": str(candidate.get("verificationLevel")) if candidate else None,
            "workflowPath": (
                str(candidate.get("workflowPath")) if candidate and candidate.get("workflowPath") else None
            ),
            "githubRunId": (
                candidate.get("githubRunId")
                if candidate and isinstance(candidate.get("githubRunId"), int)
                else None
            ),
        }
        analysis_key = digest(
            {
                "userId": user_id,
                "sessionId": session_id,
                "deliveryPlanId": plan.id,
                "patchDigest": patch_digest,
                "matrix": matrix,
                "verification": verification,
            }
        )
        existing = await db.counterfactual_verifications.find_one({"analysisKey": analysis_key})
        if existing:
            return existing
        report_id = new_id("cfv_")
        report = {
            "_id": report_id,
            "id": report_id,
            "analysisKey": analysis_key,
            "schemaVersion": "counterfactual-verification-v1",
            "userId": user_id,
            "sessionId": session_id,
            "runId": plan.run_id,
            "repositoryId": plan.repository_id,
            "deliveryPlanId": plan.id,
            "patchDigest": patch_digest,
            "status": status,
            "verification": verification,
            "matrix": matrix,
            "limitations": limitations,
            "createdAt": utc_now(),
        }
        receipt = await AssuranceLedgerService().record_counterfactual_receipt(report)
        report["ledgerReceiptId"] = receipt["id"]
        report["ledgerManifestHash"] = receipt["manifestHash"]
        await db.counterfactual_verifications.insert_one(report)
        return report
