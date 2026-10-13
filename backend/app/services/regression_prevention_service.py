"""Historical CI regression-prevention advisories for exact delivery plans.

This is intentionally a read-only, deterministic service.  It only promotes a
pattern to an advisory after two *different failed run SHAs* have supported the
same CI category for an exact file path that the new DeliveryPlan changes.  It
does not create commands, approvals, patches, or GitHub mutations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.db.mongo import get_db
from app.schemas.delivery import DeliveryPlan
from app.schemas.regression_prevention import (
    RegressionPreventionAdvisory,
    RegressionPreventionReport,
)

_HISTORY_LIMIT = 120
_MAX_ADVISORIES = 8
_MAX_OCCURRENCES_PER_ADVISORY = 12
_THRESHOLD_DESCRIPTION = (
    "A prevention advisory requires at least two supported historical CI incidents "
    "with different failed-run SHAs for the same failure category and exact changed path."
)


def _normalise_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.replace("\\", "/").strip().lstrip("./").strip("/")
    return path or None


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


class RegressionPreventionService:
    """Find repeated *supported* incident patterns without asserting causality."""

    def analyze(
        self,
        *,
        plan: DeliveryPlan,
        incidents: Iterable[dict[str, Any]],
    ) -> RegressionPreventionReport:
        changed_paths = sorted({path for file in plan.files if (path := _normalise_path(file.path))})
        changed_path_set = set(changed_paths)
        # (path, category) -> one distinct historical run SHA -> incident IDs.
        matches: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        inspected = 0
        ignored_same_revision = 0

        for incident in incidents:
            inspected += 1
            if incident.get("evidenceStrength") != "supported":
                continue
            sha = incident.get("headSha")
            if not isinstance(sha, str) or not sha or sha == plan.base_sha:
                ignored_same_revision += 1
                continue
            incident_id = incident.get("_id")
            if not isinstance(incident_id, str) or not incident_id:
                continue
            relevant_paths = {
                path
                for raw in _strings(incident.get("relevantPaths"))
                if (path := _normalise_path(raw)) in changed_path_set
            }
            if not relevant_paths:
                continue
            categories = sorted(set(_strings(incident.get("failureCategories"))))
            for path in sorted(relevant_paths):
                for category in categories:
                    if len(matches[(path, category)][sha]) < _MAX_OCCURRENCES_PER_ADVISORY:
                        matches[(path, category)][sha].append(incident_id)

        advisories: list[RegressionPreventionAdvisory] = []
        for (path, category), by_sha in sorted(matches.items()):
            run_shas = sorted(by_sha)
            if len(run_shas) < 2:
                continue
            selected_shas = run_shas[:_MAX_OCCURRENCES_PER_ADVISORY]
            incident_ids = list(dict.fromkeys(incident_id for sha in selected_shas for incident_id in by_sha[sha]))[
                :_MAX_OCCURRENCES_PER_ADVISORY
            ]
            advisories.append(
                RegressionPreventionAdvisory(
                    path=path,
                    failure_category=category,
                    historical_incident_ids=incident_ids,
                    historical_run_shas=selected_shas,
                    occurrence_count=len(run_shas),
                    message=(
                        f"{len(run_shas)} supported historical CI runs reported {category.replace('_', ' ')} "
                        f"while {path} was relevant. This is review context, not proof this change will fail."
                    ),
                    recommendation=(
                        "Review the existing exact validation plan and CI evidence before delivery; "
                        "do not add or execute commands from historical logs."
                    ),
                )
            )
            if len(advisories) >= _MAX_ADVISORIES:
                break

        limitations = [
            "Historical incidents are correlations only; this analysis does not predict CI outcomes "
            "or change approval requirements.",
            "Only incident summaries with supported evidence strength and exact path overlap are considered.",
        ]
        if not changed_paths:
            limitations.append("The DeliveryPlan has no usable repository paths to compare.")
        if ignored_same_revision:
            limitations.append(
                "Incidents from the DeliveryPlan base SHA were excluded to avoid treating the "
                "current investigation as history."
            )
        if inspected >= _HISTORY_LIMIT:
            limitations.append(f"Historical incident lookup was bounded to the most recent {_HISTORY_LIMIT} records.")
        if len(advisories) >= _MAX_ADVISORIES:
            limitations.append(f"Advisories were capped at {_MAX_ADVISORIES} for focused review.")

        return RegressionPreventionReport(
            delivery_plan_id=plan.id,
            plan_base_sha=plan.base_sha,
            status="available" if advisories else "insufficient_history",
            changed_paths=changed_paths,
            advisories=advisories,
            threshold_description=_THRESHOLD_DESCRIPTION,
            limitations=limitations,
            analyzed_at=datetime.now(UTC),
        )

    async def analyze_for_plan(
        self,
        *,
        user_id: str,
        session_id: str,
        plan: DeliveryPlan,
    ) -> RegressionPreventionReport:
        """Load only the caller's repository-scoped incident summaries."""

        # Webhook incidents deliberately retain GitHub's numeric repository ID,
        # while a DeliveryPlan is bound to the canonical clone URL. Resolve the
        # numeric identity only through this server-owned session binding; never
        # accept either identity from the browser or compare unlike values.
        automation = await get_db().repository_automation_settings.find_one(
            {"userId": user_id, "sessionId": session_id},
            {"repositoryId": 1},
        )
        repository_id = automation.get("repositoryId") if isinstance(automation, dict) else None
        if not isinstance(repository_id, str) or not repository_id:
            report = self.analyze(plan=plan, incidents=[])
            report.limitations.append(
                "No server-verified GitHub repository binding with incident history is available for this session."
            )
            return report

        cursor = (
            get_db()
            .incidents.find(
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
                    "failureCategories": 1,
                    "relevantPaths": 1,
                    "evidenceStrength": 1,
                    "updatedAt": 1,
                },
            )
            .sort("updatedAt", -1)
            .limit(_HISTORY_LIMIT)
        )
        incidents = [record async for record in cursor]
        return self.analyze(plan=plan, incidents=incidents)
