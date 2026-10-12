"""Tenant-scoped persistence for deterministic impact analyses."""

from __future__ import annotations

from typing import Any

from app.db.mongo import get_db
from app.intelligence.impact.models import ChangeImpactAnalysis


class ChangeImpactStore:
    """Store analyses without exposing them across tenants or sessions."""

    collection_name = "change_impact_analyses"

    async def ensure_indexes(self) -> None:
        collection = getattr(get_db(), self.collection_name)
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("deliveryPlanId", 1), ("graphCommitSha", 1)],
            unique=True,
            name="impact_one_plan_per_graph_revision",
        )
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("createdAt", -1)],
            name="impact_by_tenant_session_recent",
        )

    async def save(self, analysis: ChangeImpactAnalysis) -> ChangeImpactAnalysis:
        collection = getattr(get_db(), self.collection_name)
        document = analysis.model_dump(mode="python")
        document["_id"] = analysis.id
        document["userId"] = document.pop("user_id")
        document["sessionId"] = document.pop("session_id")
        document["repositoryId"] = document.pop("repository_id")
        document["deliveryPlanId"] = document.pop("delivery_plan_id")
        document["planBaseSha"] = document.pop("plan_base_sha")
        document["graphCommitSha"] = document.pop("graph_commit_sha")
        document["changedPaths"] = document.pop("changed_paths")
        document["unmappedPaths"] = document.pop("unmapped_paths")
        document["directlyAffected"] = document.pop("directly_affected")
        document["indirectlyAffected"] = document.pop("indirectly_affected")
        document["relatedTests"] = document.pop("related_tests")
        document["relatedWorkflows"] = document.pop("related_workflows")
        document["createdAt"] = document.pop("created_at")
        await collection.update_one(
            {
                "userId": analysis.user_id,
                "sessionId": analysis.session_id,
                "deliveryPlanId": analysis.delivery_plan_id,
                "graphCommitSha": analysis.graph_commit_sha,
            },
            {"$setOnInsert": document},
            upsert=True,
        )
        stored = await collection.find_one(
            {
                "userId": analysis.user_id,
                "sessionId": analysis.session_id,
                "deliveryPlanId": analysis.delivery_plan_id,
                "graphCommitSha": analysis.graph_commit_sha,
            }
        )
        return self._analysis(stored) if stored else analysis

    async def by_id(self, *, user_id: str, session_id: str, analysis_id: str) -> ChangeImpactAnalysis | None:
        document = await getattr(get_db(), self.collection_name).find_one(
            {"_id": analysis_id, "userId": user_id, "sessionId": session_id}
        )
        return self._analysis(document) if document else None

    @staticmethod
    def _analysis(document: dict[str, Any]) -> ChangeImpactAnalysis:
        compatible = dict(document)
        compatible.pop("_id", None)
        for snake, camel in (
            ("user_id", "userId"),
            ("session_id", "sessionId"),
            ("repository_id", "repositoryId"),
            ("delivery_plan_id", "deliveryPlanId"),
            ("plan_base_sha", "planBaseSha"),
            ("graph_commit_sha", "graphCommitSha"),
            ("changed_paths", "changedPaths"),
            ("unmapped_paths", "unmappedPaths"),
            ("directly_affected", "directlyAffected"),
            ("indirectly_affected", "indirectlyAffected"),
            ("related_tests", "relatedTests"),
            ("related_workflows", "relatedWorkflows"),
            ("created_at", "createdAt"),
        ):
            compatible[snake] = compatible.pop(camel)
        return ChangeImpactAnalysis.model_validate(compatible)
