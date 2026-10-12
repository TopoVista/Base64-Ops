"""Mongo persistence for immutable, tenant-scoped graph snapshots."""

from __future__ import annotations

from typing import Any

from app.db.mongo import get_db
from app.intelligence.graph.models import EngineeringGraphSnapshot


class EngineeringGraphStore:
    """Persist snapshots without replacing historical repository facts."""

    collection_name = "engineering_graph_snapshots"

    async def ensure_indexes(self) -> None:
        collection = getattr(get_db(), self.collection_name)
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("repositoryId", 1), ("commitSha", 1)],
            unique=True,
            name="engineering_graph_one_snapshot_per_revision",
        )
        await collection.create_index(
            [("userId", 1), ("sessionId", 1), ("builtAt", -1)],
            name="engineering_graph_latest_snapshot_per_session",
        )
        await collection.create_index(
            [("userId", 1), ("repositoryId", 1), ("commitSha", 1)],
            name="engineering_graph_revision_lookup",
        )

    async def save(self, snapshot: EngineeringGraphSnapshot) -> EngineeringGraphSnapshot:
        """Insert once for a revision; never destructively replace it."""
        collection = getattr(get_db(), self.collection_name)
        document = snapshot.model_dump(by_alias=True)
        document["_id"] = snapshot.id
        document["userId"] = document.pop("user_id")
        document["sessionId"] = document.pop("session_id")
        document["repositoryId"] = document.pop("repository_id")
        document["commitSha"] = document.pop("commit_sha")
        document["builtAt"] = document.pop("built_at")
        document["schemaVersion"] = document.pop("schema_version")
        await collection.update_one(
            {
                "userId": snapshot.user_id,
                "sessionId": snapshot.session_id,
                "repositoryId": snapshot.repository_id,
                "commitSha": snapshot.commit_sha,
            },
            {"$setOnInsert": document},
            upsert=True,
        )
        stored = await collection.find_one(
            {
                "userId": snapshot.user_id,
                "sessionId": snapshot.session_id,
                "repositoryId": snapshot.repository_id,
                "commitSha": snapshot.commit_sha,
            }
        )
        return self._snapshot(stored) if stored else snapshot

    async def latest(self, *, user_id: str, session_id: str, repository_id: str) -> EngineeringGraphSnapshot | None:
        document = await getattr(get_db(), self.collection_name).find_one(
            {"userId": user_id, "sessionId": session_id, "repositoryId": repository_id},
            sort=[("builtAt", -1)],
        )
        return self._snapshot(document) if document else None

    async def by_commit(
        self, *, user_id: str, session_id: str, repository_id: str, commit_sha: str
    ) -> EngineeringGraphSnapshot | None:
        document = await getattr(get_db(), self.collection_name).find_one(
            {
                "userId": user_id,
                "sessionId": session_id,
                "repositoryId": repository_id,
                "commitSha": commit_sha,
            }
        )
        return self._snapshot(document) if document else None

    @staticmethod
    def _snapshot(document: dict[str, Any]) -> EngineeringGraphSnapshot:
        compatible = dict(document)
        compatible.pop("_id", None)
        compatible["user_id"] = compatible.pop("userId")
        compatible["session_id"] = compatible.pop("sessionId")
        compatible["repository_id"] = compatible.pop("repositoryId")
        compatible["commit_sha"] = compatible.pop("commitSha")
        compatible["built_at"] = compatible.pop("builtAt")
        compatible["schema_version"] = compatible.pop("schemaVersion", 1)
        return EngineeringGraphSnapshot.model_validate(compatible)
