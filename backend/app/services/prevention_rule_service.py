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

from app.db.mongo import get_db
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
                {"$set": document, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )
            rule_ids.append(rule_id)
        return rule_ids

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
