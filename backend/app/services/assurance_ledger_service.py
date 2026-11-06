"""Cryptographically verifiable, append-only assurance receipts.

The ledger is intentionally separate from agent traces.  It records stable
hashes and canonical metadata for the evidence, remediation plan, approval,
and delivery outcome that an operator needs to verify later.  It never stores
raw source, CI logs, credentials, or model output in a signature payload.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.db.mongo import get_db
from app.schemas.delivery import DeliveryPlan, DeliveryResult
from app.utils.datetime import utc_now
from app.utils.ids import new_id

_SCHEMA_VERSION = "base64-assurance-ledger-v1"


def canonical_json(value: Any) -> str:
    """Encode a value deterministically before hashing or signing it."""

    def default(item: Any) -> str:
        if isinstance(item, datetime | date):
            return item.isoformat()
        raise TypeError(f"Unsupported ledger value: {type(item).__name__}")

    return json.dumps(value, default=default, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class AssuranceLedgerService:
    """Create and verify receipts bound to Base64-controlled signing keys."""

    def _private_key(self) -> Ed25519PrivateKey:
        encoded = get_settings().ledger_signing_private_key.encode("ascii")
        try:
            raw = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        except Exception as exc:
            raise RuntimeError("Ledger signing key is invalid") from exc
        if len(raw) != 32:
            raise RuntimeError("Ledger signing key is invalid")
        return Ed25519PrivateKey.from_private_bytes(raw)

    def _signature(self, manifest_hash: str) -> dict[str, str]:
        key = self._private_key()
        public_key = key.public_key().public_bytes_raw()
        signature = key.sign(bytes.fromhex(manifest_hash))
        return {
            "algorithm": "ed25519-sha256",
            "keyId": get_settings().ledger_signing_key_id,
            "publicKey": base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("="),
            "value": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        }

    @staticmethod
    def verify_receipt(receipt: dict[str, Any], *, expected_user_id: str | None = None) -> bool:
        """Verify a receipt without access to the private signing key.

        Consumers may use the embedded public key to validate historical
        receipts after key rotation.  Callers can also assert tenant scope to
        prevent a valid receipt from another tenant being presented as theirs.
        """

        try:
            manifest = receipt["manifest"]
            signature = receipt["signature"]
            manifest_hash = str(receipt["manifestHash"])
            if expected_user_id is not None and manifest.get("userId") != expected_user_id:
                return False
            if digest(manifest) != manifest_hash or signature.get("algorithm") != "ed25519-sha256":
                return False
            public_raw = base64.urlsafe_b64decode(
                str(signature["publicKey"]) + "=" * (-len(str(signature["publicKey"])) % 4)
            )
            signature_raw = base64.urlsafe_b64decode(
                str(signature["value"]) + "=" * (-len(str(signature["value"])) % 4)
            )
            Ed25519PublicKey.from_public_bytes(public_raw).verify(signature_raw, bytes.fromhex(manifest_hash))
            return (
                digest(
                    {
                        "schemaVersion": receipt["schemaVersion"],
                        "id": receipt["id"],
                        "kind": receipt["kind"],
                        "manifestHash": manifest_hash,
                        "previousReceiptHash": receipt.get("previousReceiptHash"),
                        "signature": signature,
                    }
                )
                == receipt["receiptHash"]
            )
        except (KeyError, TypeError, ValueError, InvalidSignature):
            return False

    async def ensure_indexes(self) -> None:
        receipts = get_db().assurance_ledger_receipts
        await receipts.create_index("receiptHash", unique=True, name="assurance_receipt_hash_once")
        await receipts.create_index(
            [("userId", 1), ("sessionId", 1), ("runId", 1), ("createdAt", 1)], name="assurance_run_timeline"
        )
        await receipts.create_index([("userId", 1), ("deliveryPlanId", 1), ("kind", 1)], name="assurance_plan_receipts")

    async def record_plan_receipt(self, plan: DeliveryPlan) -> dict[str, Any]:
        evidence = await self._evidence_manifest(plan)
        manifest = {
            "schemaVersion": _SCHEMA_VERSION,
            "kind": "remediation_plan",
            "userId": plan.user_id,
            "sessionId": plan.session_id,
            "runId": plan.run_id,
            "repositoryId": plan.repository_id,
            "deliveryPlanId": plan.id,
            "base": {"branch": plan.base_branch, "sha": plan.base_sha},
            "evidence": evidence,
            "patch": {
                "diffHash": self.plan_diff_hash(plan),
                "files": [
                    {
                        "path": change.path,
                        "changeType": change.change_type,
                        "originalHash": change.original_hash,
                        "proposedHash": change.proposed_hash,
                        "diffHash": hashlib.sha256(change.unified_diff.encode()).hexdigest(),
                    }
                    for change in sorted(plan.files, key=lambda item: item.path)
                ],
            },
            "risk": {"level": plan.risk_level, "reasons": sorted(plan.risk_reasons)},
            "validation": [
                {"id": item.id, "kind": item.kind, "required": item.required}
                for item in sorted(plan.validation_steps, key=lambda item: item.id)
            ],
        }
        return await self._append(manifest=manifest, kind="remediation_plan", delivery_plan_id=plan.id)

    async def record_approval_receipt(self, approval: dict[str, Any]) -> dict[str, Any]:
        manifest = {
            "schemaVersion": _SCHEMA_VERSION,
            "kind": "approval",
            "userId": str(approval["user_id"]),
            "sessionId": str(approval["session_id"]),
            "runId": str(approval["run_id"]),
            "repositoryId": str(approval["repository_id"]),
            "approvalId": str(approval["id"]),
            "deliveryPlanId": str(approval["delivery_plan_id"]),
            "approvalHash": str(approval["approval_hash"]),
            "planManifestHash": approval.get("ledger_manifest_hash"),
            "action": {
                "type": str(approval["action_type"]),
                "arguments": approval.get("canonical_arguments") or {},
                "baseBranch": str(approval["base_branch"]),
                "baseSha": str(approval["base_sha"]),
                "diffHash": str(approval["diff_hash"]),
            },
        }
        return await self._append(
            manifest=manifest, kind="approval", delivery_plan_id=str(approval["delivery_plan_id"])
        )

    async def record_policy_receipt(self, decision: dict[str, Any]) -> dict[str, Any]:
        action = decision["action"]
        manifest = {
            "schemaVersion": _SCHEMA_VERSION,
            "kind": "policy_authorization",
            "userId": decision["userId"],
            "sessionId": decision["sessionId"],
            "runId": decision["runId"],
            "repositoryId": action["repositoryId"],
            "deliveryPlanId": decision["deliveryPlanId"],
            "policyDecisionId": decision["id"],
            "decisionHash": decision["decisionHash"],
            "approverId": decision["approverId"],
            "policy": action,
        }
        return await self._append(
            manifest=manifest, kind="policy_authorization", delivery_plan_id=decision["deliveryPlanId"]
        )

    async def record_incident_capsule(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        repository_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Sign a replayable, content-addressed CI incident capsule.

        A capsule deliberately captures identities, digests, exact revisions,
        and declared limitations—not raw logs, source, secrets, or prompts.
        Its confidence stays ``partial`` until an isolated runtime records the
        dependency/image and execution layers needed for an exact replay.
        """

        evidence_ids = list(
            dict.fromkeys(
                str(item)
                for key in (
                    "evidence_ids",
                    "history_evidence_ids",
                    "workflow_evidence_ids",
                    "changed_file_evidence_ids",
                )
                for item in context.get(key, [])
                if isinstance(item, str) and item
            )
        )
        evidence = await self._evidence_manifest_for_scope(
            evidence_ids=evidence_ids,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            repository_id=repository_id,
        )
        manifest = {
            "schemaVersion": _SCHEMA_VERSION,
            "kind": "incident_capsule",
            "userId": user_id,
            "sessionId": session_id,
            "runId": run_id,
            "repositoryId": repository_id,
            "capsule": {
                "formatVersion": "base64-incident-capsule-v1",
                "reproducibility": "partial",
                "failedRevision": context.get("head_sha"),
                "currentRevision": context.get("current_head_sha"),
                "baseRevision": context.get("base_sha"),
                "workflow": {
                    "runId": context.get("run_id"),
                    "name": context.get("workflow_name"),
                    "path": context.get("workflow_path"),
                    "event": context.get("event"),
                    "branch": context.get("branch"),
                },
                "failureCategories": sorted(str(item) for item in context.get("failure_categories", [])),
                "relevantPaths": sorted(
                    str(item) for item in context.get("relevant_paths", []) if isinstance(item, str)
                ),
                "incomplete": bool(context.get("incomplete")),
                "limitations": sorted(str(item) for item in context.get("limitations", []) if isinstance(item, str)),
                "evidence": evidence,
                "reproductionInputs": context.get("capsule_inputs") or {
                    "reproducibility": "impossible",
                    "inputs": [],
                    "runnerLabels": [],
                    "limitations": ["Capsule inputs were not available for this investigation."],
                },
            },
        }
        return await self._append(manifest=manifest, kind="incident_capsule", delivery_plan_id=None)

    async def record_delivery_receipt(
        self, *, plan: DeliveryPlan, approval: dict[str, Any], result: DeliveryResult
    ) -> dict[str, Any]:
        manifest = {
            "schemaVersion": _SCHEMA_VERSION,
            "kind": "delivery",
            "userId": plan.user_id,
            "sessionId": plan.session_id,
            "runId": plan.run_id,
            "repositoryId": plan.repository_id,
            "deliveryPlanId": plan.id,
            "approvalId": str(approval["id"]),
            "approvalHash": str(approval["approval_hash"]),
            "planManifestHash": plan.ledger_manifest_hash,
            "outcome": {
                "status": result.status,
                "failureStage": result.failure_stage,
                "branchName": result.branch_name,
                "commitSha": result.commit_sha,
                "pullRequestNumber": result.pull_request_number,
                "validation": [
                    {
                        "stepId": item.step_id,
                        "status": item.status,
                        "summaryHash": hashlib.sha256(item.summary.encode()).hexdigest(),
                        "durationMs": item.duration_ms,
                    }
                    for item in result.validation_results
                ],
            },
        }
        return await self._append(manifest=manifest, kind="delivery", delivery_plan_id=plan.id)

    async def get_verified_receipt(self, *, receipt_id: str, user_id: str, session_id: str) -> dict[str, Any] | None:
        receipt = await get_db().assurance_ledger_receipts.find_one(
            {"id": receipt_id, "userId": user_id, "sessionId": session_id}
        )
        if not receipt:
            return None
        verified = self.verify_receipt(receipt, expected_user_id=user_id)
        return {
            **receipt,
            "verified": verified,
            "chainVerified": verified
            and await self.verify_chain(receipt=receipt, user_id=user_id, session_id=session_id),
        }

    async def verify_chain(self, *, receipt: dict[str, Any], user_id: str, session_id: str) -> bool:
        """Verify every signed predecessor through the chain genesis.

        The current receipt's signature alone proves its manifest, but not
        that a retained receipt is connected to the original plan.  Traversing
        the hash-linked, tenant-scoped predecessors makes a deleted,
        substituted, cross-run, or cyclic chain fail closed.
        """

        current = receipt
        seen_hashes: set[str] = set()
        expected_run_id = current.get("runId")
        for _ in range(10_000):
            if (
                not self.verify_receipt(current, expected_user_id=user_id)
                or current.get("userId") != user_id
                or current.get("sessionId") != session_id
                or current.get("runId") != expected_run_id
            ):
                return False
            receipt_hash = current.get("receiptHash")
            if not isinstance(receipt_hash, str) or receipt_hash in seen_hashes:
                return False
            seen_hashes.add(receipt_hash)
            previous_hash = current.get("previousReceiptHash")
            if previous_hash is None:
                return True
            if not isinstance(previous_hash, str) or not previous_hash:
                return False
            current = await get_db().assurance_ledger_receipts.find_one(
                {
                    "receiptHash": previous_hash,
                    "userId": user_id,
                    "sessionId": session_id,
                    "runId": expected_run_id,
                }
            )
            if not current:
                return False
        # A practical guard against malicious/corrupt unbounded chains.
        return False

    async def _evidence_manifest(self, plan: DeliveryPlan) -> list[dict[str, Any]]:
        return await self._evidence_manifest_for_scope(
            evidence_ids=plan.evidence_ids,
            user_id=plan.user_id,
            session_id=plan.session_id,
            run_id=plan.run_id,
            repository_id=plan.repository_id,
        )

    async def _evidence_manifest_for_scope(
        self,
        *,
        evidence_ids: list[str],
        user_id: str,
        session_id: str,
        run_id: str,
        repository_id: str,
    ) -> list[dict[str, Any]]:
        if not evidence_ids:
            return []
        documents = await (
            get_db()
            .evidence.find(
                {
                    "id": {"$in": evidence_ids},
                    "userId": user_id,
                    "sessionId": session_id,
                    "runId": run_id,
                    "repositoryId": repository_id,
                },
                {"id": 1, "contentHash": 1, "sourceType": 1, "commitSha": 1, "path": 1},
            )
            .to_list(len(evidence_ids))
        )
        by_id = {str(item.get("id")): item for item in documents}
        missing = [item for item in evidence_ids if item not in by_id]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Evidence required for this remediation plan is missing or belongs to another investigation.",
            )
        return [
            {
                "id": evidence_id,
                "contentHash": str(by_id[evidence_id]["contentHash"]),
                "sourceType": str(by_id[evidence_id]["sourceType"]),
                "commitSha": by_id[evidence_id].get("commitSha"),
                "path": by_id[evidence_id].get("path"),
            }
            for evidence_id in sorted(evidence_ids)
        ]

    @staticmethod
    def plan_diff_hash(plan: DeliveryPlan) -> str:
        return hashlib.sha256("\n".join(change.unified_diff for change in plan.files).encode()).hexdigest()

    async def _append(self, *, manifest: dict[str, Any], kind: str, delivery_plan_id: str | None) -> dict[str, Any]:
        manifest_hash = digest(manifest)
        scope = {"userId": manifest["userId"], "sessionId": manifest["sessionId"], "runId": manifest["runId"]}
        previous = await (
            get_db().assurance_ledger_receipts.find(scope, {"receiptHash": 1}).sort("createdAt", -1).limit(1).to_list(1)
        )
        previous_hash = previous[0].get("receiptHash") if previous else None
        receipt_id = new_id("lgr_")
        signature = self._signature(manifest_hash)
        receipt_hash = digest(
            {
                "schemaVersion": _SCHEMA_VERSION,
                "id": receipt_id,
                "kind": kind,
                "manifestHash": manifest_hash,
                "previousReceiptHash": previous_hash,
                "signature": signature,
            }
        )
        receipt = {
            "_id": receipt_id,
            "id": receipt_id,
            "schemaVersion": _SCHEMA_VERSION,
            "kind": kind,
            **scope,
            "repositoryId": manifest["repositoryId"],
            "deliveryPlanId": delivery_plan_id,
            "manifest": manifest,
            "manifestHash": manifest_hash,
            "previousReceiptHash": previous_hash,
            "signature": signature,
            "receiptHash": receipt_hash,
            "createdAt": utc_now(),
        }
        await get_db().assurance_ledger_receipts.insert_one(receipt)
        return receipt
