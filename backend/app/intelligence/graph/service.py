"""Tenant-scoped graph build and bounded neighborhood queries."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from fastapi import HTTPException, status

from app.intelligence.graph.builder import EngineeringGraphBuilder
from app.intelligence.graph.models import EngineeringGraphSnapshot, GraphEdge, GraphNode
from app.intelligence.graph.store import EngineeringGraphStore
from app.services.workspace_service import WorkspaceService

DEFAULT_NEIGHBORHOOD_LIMIT = 100
MAX_NEIGHBORHOOD_LIMIT = 250
MAX_NEIGHBORHOOD_DEPTH = 3


class EngineeringGraphService:
    """The graph boundary used by workers and authenticated API routes."""

    def __init__(self, store: EngineeringGraphStore | None = None, workspace: WorkspaceService | None = None) -> None:
        self.store = store or EngineeringGraphStore()
        self.workspace = workspace or WorkspaceService()
        self.builder = EngineeringGraphBuilder()

    async def build_and_store(self, *, session: dict, repo_path: Path) -> EngineeringGraphSnapshot:
        commit_sha = self.workspace.head_commit(repo_path)
        if not commit_sha:
            raise RuntimeError("Unable to determine repository revision for graph build")
        snapshot = self.builder.build(
            repo_path=repo_path,
            user_id=str(session["userId"]),
            session_id=str(session["_id"]),
            repository_id=str(session["repoUrl"]),
            commit_sha=commit_sha,
            branch=self.workspace.current_branch(repo_path),
            files=self.workspace.iter_indexable_files(repo_path),
        )
        return await self.store.save(snapshot)

    async def latest_for_session(self, *, user_id: str, session: dict) -> EngineeringGraphSnapshot | None:
        return await self.store.latest(
            user_id=user_id,
            session_id=str(session["_id"]),
            repository_id=str(session["repoUrl"]),
        )

    async def snapshot_for_session(
        self, *, user_id: str, session: dict, commit_sha: str | None = None
    ) -> EngineeringGraphSnapshot | None:
        if commit_sha:
            return await self.store.by_commit(
                user_id=user_id,
                session_id=str(session["_id"]),
                repository_id=str(session["repoUrl"]),
                commit_sha=commit_sha,
            )
        return await self.latest_for_session(user_id=user_id, session=session)

    @staticmethod
    def neighborhood(
        snapshot: EngineeringGraphSnapshot,
        *,
        node_id: str,
        depth: int = 1,
        limit: int = DEFAULT_NEIGHBORHOOD_LIMIT,
    ) -> tuple[list[GraphNode], list[GraphEdge], bool]:
        """Traverse both directions with deterministic, explicit truncation."""
        safe_depth = max(1, min(depth, MAX_NEIGHBORHOOD_DEPTH))
        safe_limit = max(1, min(limit, MAX_NEIGHBORHOOD_LIMIT))
        nodes = {node.id: node for node in snapshot.nodes}
        if node_id not in nodes:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Graph node not found")
        adjacency: dict[str, list[GraphEdge]] = {}
        for edge in snapshot.edges:
            adjacency.setdefault(edge.source_node_id, []).append(edge)
            adjacency.setdefault(edge.target_node_id, []).append(edge)
        visited = {node_id}
        selected_edge_ids: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        truncated = False
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= safe_depth:
                continue
            for edge in sorted(adjacency.get(current, []), key=lambda item: item.id):
                other = edge.target_node_id if edge.source_node_id == current else edge.source_node_id
                if other not in nodes:
                    continue
                if len(visited) >= safe_limit and other not in visited:
                    truncated = True
                    continue
                selected_edge_ids.add(edge.id)
                if other not in visited:
                    visited.add(other)
                    queue.append((other, current_depth + 1))
        return (
            sorted((nodes[item] for item in visited), key=lambda item: (item.path or "", item.name, item.id)),
            sorted((edge for edge in snapshot.edges if edge.id in selected_edge_ids), key=lambda item: item.id),
            truncated,
        )
