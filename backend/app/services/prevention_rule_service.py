"""Durable, non-blocking recurrence rules derived from CI incident history.

The service records a *recurrence pattern*, never a claim that a historical
repair or a new change is correct. A rule is promoted only after the explicit
threshold of two supported incident records with independent failed-run SHAs.
Rules are advisory metadata: they cannot register commands, block a merge,
alter risk, create an approval, or invoke GitHub.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.db.mongo import get_db
from app.services.assurance_ledger_service import AssuranceLedgerService
from app.utils.datetime import utc_now

_SCAN_LIMIT = 200
_MAX_RULES_PER_REFRESH = 40
_MAX_HISTORY_PER_RULE = 16
_SCHEMA_VERSION = "prevention-rule-v1"


def _normalise_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/").rstrip("/")
    return path or None


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def _rule_id(*, user_id: str, session_id: str, repository_id: str, path: str, category: str) -> str:
    encoded = json.dumps(
        {"user": user_id, "session": session_id, "repository": repository_id, "path": path, "category": category},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "prv_" + hashlib.sha256(encoded).hexdigest()[:32]


class PreventionRuleService:
    """Promote evidence-backed recurrence patterns into durable advisories."""

    async def ensure_indexes(self) -> None:
        collection = get_db().prevention_rules
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("repositoryId", 1), ("affectedPath", 1), ("failureCategory", 1)],
            unique=True,
            name="prevention_rule_one_path_category_per_session",
        )
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("updatedAt", -1)], name="prevention_rule_tenant_recent"
        )
        await get_db().prevention_policy_events.create_index(
            [("ruleId", 1), ("policyVersion", 1)], unique=True, name="prevention_policy_version_once"
        )
        await get_db().prevention_policy_observations.create_index(
            [("ruleId", 1), ("policyVersion", 1), ("incidentId", 1)],
            unique=True,
            name="prevention_policy_observation_once",
        )

    async def list_for_session(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Return bounded persisted advisories from one owned session.

        Session identity is resolved by the authenticated route, not supplied
        by a browser as a repository identifier. The projection deliberately
        omits internal tenant fields and all future administrative controls.
        """

        bounded_limit = min(max(limit, 1), 50)
        cursor = (
            get_db()
            .prevention_rules.find(
                {"userId": user_id, "sessionId": session_id},
                {
                    "_id": 1,
                    "repositoryId": 1,
                    "affectedPath": 1,
                    "failureCategory": 1,
                    "historicalIncidentIds": 1,
                    "historicalRunShas": 1,
                    "occurrenceCount": 1,
                    "status": 1,
                    "applicabilityLimitations": 1,
                    "invalidationReason": 1,
                    "governanceStatus": 1,
                    "policyVersion": 1,
                    "deploymentScope": 1,
                    "ledgerReceiptId": 1,
                    "effectivenessMetrics": 1,
                    "createdAt": 1,
                    "updatedAt": 1,
                },
            )
            .sort("updatedAt", -1)
            .limit(bounded_limit)
        )
        return [record async for record in cursor]

    @staticmethod
    def candidates(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Produce bounded candidates from supported group-root incidents.

        Exact path matching avoids turning weak directory-level similarity into
        a warning. The result is only a recurrence observation, not causality.
        """

        grouped: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for incident in incidents:
            if incident.get("evidenceStrength") != "supported":
                continue
            feedback = incident.get("feedback")
            if isinstance(feedback, dict) and feedback.get("disposition") == "incorrect":
                # A user correction is untrusted product input, not a new
                # causal claim. It does make this incident ineligible for an
                # automatically active historical rule until reviewed.
                continue
            incident_id = incident.get("_id")
            sha = incident.get("headSha")
            if not isinstance(incident_id, str) or not incident_id or not isinstance(sha, str) or not sha:
                continue
            paths = sorted({_normalise_path(raw) for raw in _strings(incident.get("relevantPaths"))} - {None})
            categories = sorted(set(_strings(incident.get("failureCategories"))))
            for path in paths:
                for category in categories:
                    ids = grouped[(path, category)][sha]
                    if len(ids) < _MAX_HISTORY_PER_RULE:
                        ids.append(incident_id)

        candidates: list[dict[str, Any]] = []
        for (path, category), by_sha in sorted(grouped.items()):
            shas = sorted(by_sha)
            if len(shas) < 2:
                continue
            selected_shas = shas[:_MAX_HISTORY_PER_RULE]
            incident_ids = list(
                dict.fromkeys(incident_id for sha in selected_shas for incident_id in by_sha[sha])
            )[:_MAX_HISTORY_PER_RULE]
            candidates.append(
                {
                    "affectedPath": path,
                    "failureCategory": category,
                    "historicalIncidentIds": incident_ids,
                    "historicalRunShas": selected_shas,
                    "occurrenceCount": len(shas),
                }
            )
            if len(candidates) >= _MAX_RULES_PER_REFRESH:
                break
        return candidates

    async def refresh_for_repository(
        self,
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
    ) -> list[str]:
        """Idempotently upsert rules after a durable incident update.

        The caller supplies only server-derived identities. This internal write
        is limited to Base64's MongoDB record and has no external effect.
        """

        db = get_db()
        cursor = (
            db.incidents.find(
                {
                    "userId": user_id,
                    "sessionId": session_id,
                    "repositoryId": repository_id,
                    "isGroupRoot": {"$ne": False},
                    "evidenceStrength": "supported",
                },
                {
                    "_id": 1,
                    "headSha": 1,
                    "relevantPaths": 1,
                    "failureCategories": 1,
                    "evidenceStrength": 1,
                    "feedback": 1,
                },
            )
            .sort("updatedAt", -1)
            .limit(_SCAN_LIMIT)
        )
        candidates = self.candidates([record async for record in cursor])
        now = utc_now()
        rule_ids: list[str] = []
        for candidate in candidates:
            rule_id = _rule_id(
                user_id=user_id,
                session_id=session_id,
                repository_id=repository_id,
                path=candidate["affectedPath"],
                category=candidate["failureCategory"],
            )
            document = {
                "_id": rule_id,
                "schemaVersion": _SCHEMA_VERSION,
                "userId": user_id,
                "sessionId": session_id,
                "repositoryId": repository_id,
                "affectedPath": candidate["affectedPath"],
                "failureCategory": candidate["failureCategory"],
                "historicalIncidentIds": candidate["historicalIncidentIds"],
                "historicalRunShas": candidate["historicalRunShas"],
                "occurrenceCount": candidate["occurrenceCount"],
                "threshold": {"minimumSupportedRuns": 2, "distinctFailedRunShas": True},
                "status": "active",
                "applicabilityLimitations": [
                    "A repeated historical incident is correlation, not proof that a new change will fail.",
                    "The rule is advisory only and cannot alter validation, approval, or delivery authority.",
                ],
                "updatedAt": now,
            }
            await db.prevention_rules.update_one(
                {
                    "userId": user_id,
                    "sessionId": session_id,
                    "repositoryId": repository_id,
                    "affectedPath": candidate["affectedPath"],
                    "failureCategory": candidate["failureCategory"],
                },
                {
                    "$set": document,
                    # Promotion to a recurrence advisory is automatic;
                    # promotion to policy is not. These lifecycle fields must
                    # be set only on creation so refreshes cannot undo a human
                    # approval or rollback.
                    "$setOnInsert": {
                        "createdAt": now,
                        "governanceStatus": "proposed",
                        "policyVersion": 0,
                        "deploymentScope": "advisory_only",
                        "effectivenessMetrics": {
                            "status": "not_measurable",
                            "observedRecurrenceCount": 0,
                            "violationEventCount": 0,
                            "limitations": [
                                "An advisory-only policy has no enforcement signal, so effectiveness "
                                "cannot be inferred.",
                                "Observed recurrence is tracked separately from causation and policy violations.",
                            ],
                        },
                    },
                },
                upsert=True,
            )
            rule_ids.append(rule_id)
        return rule_ids

    async def govern_rule(
        self,
        *,
        user_id: str,
        session_id: str,
        rule_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Approve or roll back an advisory-only prevention policy.

        There is no hidden GitHub write or merge gate here. Approval creates
        an immutable signed policy event, while rollback makes the policy
        inactive before any future enforcement adapter could consult it.
        """

        if action not in {"approve", "rollback"}:
            raise ValueError("Unsupported prevention policy action.")
        db = get_db()
        rule = await db.prevention_rules.find_one({"_id": rule_id, "userId": user_id, "sessionId": session_id})
        if not rule:
            raise LookupError("Prevention rule not found.")
        current = str(rule.get("governanceStatus") or "proposed")
        if action == "approve" and current == "approved":
            return self._governance_response(rule)
        if action == "rollback" and current != "approved":
            raise ValueError("Only an approved prevention policy can be rolled back.")
        version = int(rule.get("policyVersion") or 0) + 1
        limitations = [
            "Deployment scope is advisory only; this policy does not block merges or modify GitHub configuration.",
            "Effectiveness requires enforcement or violation telemetry and is not inferred "
            "from historical correlation.",
        ]
        policy_updated_at = utc_now()
        event = {
            "_id": f"{rule_id}:v{version}",
            "ruleId": rule_id,
            "policyVersion": version,
            "userId": user_id,
            "sessionId": session_id,
            # Policy events are not artifacts of a single CI run.  A stable
            # per-rule ledger scope preserves an append-only chain across
            # approval and rollback without misrepresenting it as run data.
            "runId": f"prevention-policy:{rule_id}",
            "repositoryId": str(rule["repositoryId"]),
            "action": action,
            "deploymentScope": "advisory_only",
            "supportingIncidentIds": list(rule.get("historicalIncidentIds") or []),
            "supportingRunShas": list(rule.get("historicalRunShas") or []),
            "limitations": limitations,
            "createdAt": policy_updated_at,
        }
        receipt = await AssuranceLedgerService().record_prevention_policy_receipt(event)
        event["ledgerReceiptId"] = receipt["id"]
        event["ledgerManifestHash"] = receipt["manifestHash"]
        await db.prevention_policy_events.insert_one(event)
        next_status = "approved" if action == "approve" else "rolled_back"
        await db.prevention_rules.update_one(
            {"_id": rule_id, "userId": user_id, "sessionId": session_id},
            {
                "$set": {
                    "governanceStatus": next_status,
                    "policyVersion": version,
                    "deploymentScope": "advisory_only",
                    "ledgerReceiptId": receipt["id"],
                    "ledgerManifestHash": receipt["manifestHash"],
                    "policyUpdatedAt": policy_updated_at,
                    "updatedAt": policy_updated_at,
                    "effectivenessMetrics": {
                        "status": "not_measurable",
                        "observedRecurrenceCount": 0,
                        "violationEventCount": 0,
                        "limitations": [
                            "An advisory-only policy has no enforcement signal, so effectiveness cannot be inferred.",
                            "Observed recurrence is tracked separately from causation and policy violations.",
                        ],
                    },
                }
            },
        )
        return {
            "rule_id": rule_id,
            "governance_status": next_status,
            "policy_version": version,
            "deployment_scope": "advisory_only",
            "ledger_receipt_id": receipt["id"],
            "limitations": limitations,
        }

    @staticmethod
    def _governance_response(rule: dict[str, Any]) -> dict[str, Any]:
        receipt_id = rule.get("ledgerReceiptId")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ValueError("Approved prevention policy is missing its signed receipt.")
        return {
            "rule_id": str(rule["_id"]),
            "governance_status": "approved",
            "policy_version": int(rule.get("policyVersion") or 1),
            "deployment_scope": "advisory_only",
            "ledger_receipt_id": receipt_id,
            "limitations": [
                "Deployment scope is advisory only; this policy does not block merges or modify GitHub configuration.",
                            "Effectiveness requires enforcement or violation telemetry and is not inferred "
                            "from historical correlation.",
            ],
        }

    async def observe_recurrence(
        self,
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
        incident_id: str,
        relevant_paths: list[str],
        failure_categories: list[str],
    ) -> int:
        """Record a post-approval recurrence observation without causal claims.

        Advisory policies do not enforce a check, so an incident cannot be
        called a policy violation. The immutable observation lets reviewers
        see recurrence after approval while the metric remains explicitly
        ``not_measurable`` for effectiveness.
        """

        paths = {_normalise_path(value) for value in relevant_paths} - {None}
        categories = {value for value in failure_categories if isinstance(value, str) and value}
        if not paths or not categories:
            return 0
        db = get_db()
        cursor = db.prevention_rules.find(
            {
                "userId": user_id,
                "sessionId": session_id,
                "repositoryId": repository_id,
                "governanceStatus": "approved",
                "status": "active",
            },
            {"_id": 1, "affectedPath": 1, "failureCategory": 1, "policyVersion": 1},
        ).limit(_MAX_RULES_PER_REFRESH)
        recorded = 0
        now = utc_now()
        async for rule in cursor:
            path = _normalise_path(rule.get("affectedPath"))
            category = rule.get("failureCategory")
            if path not in paths or category not in categories:
                continue
            version = int(rule.get("policyVersion") or 0)
            if version < 1:
                continue
            rule_id = str(rule["_id"])
            observation = {
                "_id": f"{rule_id}:v{version}:incident:{incident_id}",
                "ruleId": rule_id,
                "policyVersion": version,
                "incidentId": incident_id,
                "userId": user_id,
                "sessionId": session_id,
                "repositoryId": repository_id,
                "kind": "observed_recurrence",
                "createdAt": now,
                "limitations": [
                    "This is an observed matching incident, not proof of policy effectiveness or causation.",
                    "The advisory-only deployment has no violation telemetry.",
                ],
            }
            try:
                await db.prevention_policy_observations.insert_one(observation)
            except DuplicateKeyError:
                continue
            await db.prevention_rules.update_one(
                {
                    "_id": rule_id,
                    "userId": user_id,
                    "sessionId": session_id,
                    "governanceStatus": "approved",
                    "policyVersion": version,
                },
                {
                    "$inc": {"effectivenessMetrics.observedRecurrenceCount": 1},
                    "$set": {"effectivenessMetrics.observedAt": now, "updatedAt": now},
                },
            )
            recorded += 1
        return recorded

    async def invalidate_for_incident(
        self,
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
        incident_id: str,
    ) -> int:
        """Flag rules using a user-corrected incident for reevaluation.

        This operation has no effect outside the caller's tenant/session and
        does not destroy provenance. A following refresh can reactivate a rule
        only if sufficient independent supported history remains.
        """

        result = await get_db().prevention_rules.update_many(
            {
                "userId": user_id,
                "sessionId": session_id,
                "repositoryId": repository_id,
                "status": "active",
                "historicalIncidentIds": incident_id,
            },
            {
                "$set": {
                    "status": "needs_review",
                    "invalidatedAt": utc_now(),
                    "invalidationReason": "A user marked a contributing incident as incorrect.",
                }
            },
        )
        return int(result.modified_count)
