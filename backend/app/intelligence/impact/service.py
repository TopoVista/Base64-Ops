"""Deterministic, bounded potential-impact analysis over graph snapshots."""

from __future__ import annotations

from collections import deque

from app.intelligence.graph.models import EngineeringGraphSnapshot, GraphEdge, GraphNode, GraphNodeType
from app.intelligence.impact.models import (
    ChangeImpactAnalysis,
    ChangeImpactStatus,
    ImpactRelationship,
    ImpactTarget,
)
from app.intelligence.impact.store import ChangeImpactStore
from app.schemas.delivery import DeliveryPlan
from app.utils.datetime import utc_now
from app.utils.ids import new_id

DEFAULT_IMPACT_DEPTH = 3
DEFAULT_IMPACT_LIMIT = 80
MAX_IMPACT_DEPTH = 4
MAX_IMPACT_LIMIT = 200


class ChangeImpactService:
    """Explain potential structural blast radius without inferring causality.

    Graph edges point from a dependent to what it imports/depends on.  Impact
    analysis therefore walks *incoming* edges from a changed file: an importer
    is potentially affected when one of its dependencies changes.  The graph
    supplies observed structural links; the possible runtime impact remains an
    inference and is worded accordingly in every result.
    """

    def __init__(self, store: ChangeImpactStore | None = None) -> None:
        self.store = store or ChangeImpactStore()

    async def analyze_and_store(
        self,
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
        plan: DeliveryPlan,
        snapshot: EngineeringGraphSnapshot | None,
        depth: int = DEFAULT_IMPACT_DEPTH,
        limit: int = DEFAULT_IMPACT_LIMIT,
    ) -> ChangeImpactAnalysis:
        analysis = self.analyze(
            user_id=user_id,
            session_id=session_id,
            repository_id=repository_id,
            plan=plan,
            snapshot=snapshot,
            depth=depth,
            limit=limit,
        )
        return await self.store.save(analysis)

    def analyze(
        self,
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
        plan: DeliveryPlan,
        snapshot: EngineeringGraphSnapshot | None,
        depth: int = DEFAULT_IMPACT_DEPTH,
        limit: int = DEFAULT_IMPACT_LIMIT,
    ) -> ChangeImpactAnalysis:
        changed_paths = sorted({change.path.strip().replace("\\", "/") for change in plan.files if change.path.strip()})
        base = self._empty_analysis(
            user_id=user_id,
            session_id=session_id,
            repository_id=repository_id,
            plan=plan,
            changed_paths=changed_paths,
            graph_commit_sha=snapshot.commit_sha if snapshot else None,
        )
        if not snapshot:
            base.limitations.append(
                "No revision-bound engineering graph is available for this proposal. "
                "Reindex the repository before analyzing impact."
            )
            return base
        if snapshot.commit_sha != plan.base_sha:
            base.limitations.append(
                "The available engineering graph is for a different revision than this exact proposal; "
                "impact analysis is unavailable rather than mixing revisions."
            )
            return base

        safe_depth = max(1, min(depth, MAX_IMPACT_DEPTH))
        safe_limit = max(1, min(limit, MAX_IMPACT_LIMIT))
        nodes = {node.id: node for node in snapshot.nodes}
        files_by_path = {node.path: node for node in snapshot.nodes if node.type == GraphNodeType.FILE and node.path}
        roots = [files_by_path[path] for path in changed_paths if path in files_by_path]
        base.unmapped_paths = [path for path in changed_paths if path not in files_by_path]
        if base.unmapped_paths:
            base.limitations.append(
                "Some proposed paths are not represented in the graph snapshot, so no structural impact "
                "is claimed for them."
            )
        if not roots:
            base.limitations.append("None of the proposed files mapped to the revision-bound engineering graph.")
            return base

        incoming: dict[str, list[GraphEdge]] = {}
        for edge in snapshot.edges:
            incoming.setdefault(edge.target_node_id, []).append(edge)
        selected: dict[str, ImpactTarget] = {}
        truncated = False
        for root in sorted(roots, key=lambda item: item.path or item.id):
            queue: deque[tuple[str, int, list[ImpactRelationship]]] = deque([(root.id, 0, [])])
            seen_distances: dict[str, int] = {root.id: 0}
            while queue:
                current_id, distance, chain = queue.popleft()
                if distance >= safe_depth:
                    continue
                for edge in sorted(incoming.get(current_id, []), key=lambda item: item.id):
                    dependent = nodes.get(edge.source_node_id)
                    # The repository root contains every file.  It is useful
                    # provenance, not a meaningful potentially affected
                    # component, so it must not dominate user-facing results.
                    if not dependent or dependent.type is GraphNodeType.REPOSITORY:
                        continue
                    next_distance = distance + 1
                    prior_distance = seen_distances.get(dependent.id)
                    if prior_distance is not None and prior_distance <= next_distance:
                        continue
                    if dependent.id not in selected and len(selected) >= safe_limit:
                        truncated = True
                        continue
                    relation = ImpactRelationship(
                        relation_type=edge.relation_type,
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        epistemic_status=edge.epistemic_status,
                    )
                    next_chain = [*chain, relation]
                    seen_distances[dependent.id] = next_distance
                    selected[dependent.id] = self._target(dependent, root, next_distance, next_chain)
                    queue.append((dependent.id, next_distance, next_chain))

        targets = sorted(selected.values(), key=lambda item: (item.distance, item.path or "", item.name, item.node_id))
        base.status = ChangeImpactStatus.AVAILABLE
        base.truncated = truncated
        base.directly_affected = [item for item in targets if item.distance == 1]
        base.indirectly_affected = [item for item in targets if item.distance > 1]
        base.related_tests = [item for item in targets if item.node_type == GraphNodeType.TEST]
        # Test functions are contained by test files rather than importing
        # production code themselves.  Once a test file is reached through a
        # structural dependency path, surface its parsed test nodes as related
        # checks, without presenting that association as causality.
        impacted_paths = {item.path: item for item in targets if item.path}
        for edge in snapshot.edges:
            container = nodes.get(edge.source_node_id)
            test = nodes.get(edge.target_node_id)
            file_target = impacted_paths.get(container.path) if container else None
            if (
                edge.relation_type.value == "contains"
                and test
                and test.type is GraphNodeType.TEST
                and file_target
            ):
                base.related_tests.append(
                    ImpactTarget(
                        node_id=test.id,
                        node_type=test.type,
                        name=test.name,
                        path=test.path,
                        qualified_name=test.qualified_name,
                        distance=file_target.distance,
                        relationship_path=file_target.relationship_path,
                        explanation=(
                            f"Related test: {test.qualified_name or test.name} is declared in a test file "
                            "reached by the structural dependency traversal. This identifies a review target, "
                            "not a claim that the test will fail."
                        ),
                    )
                )
        base.related_tests.sort(key=lambda item: (item.distance, item.path or "", item.name, item.node_id))
        base.related_workflows = [
            item for item in targets if item.node_type in {GraphNodeType.WORKFLOW, GraphNodeType.CI_JOB}
        ]
        if truncated:
            base.limitations.append(
                "Impact traversal reached its configured result limit; only the nearest potential dependents are shown."
            )
        if snapshot.coverage.truncated:
            base.limitations.append(
                "The underlying engineering graph is partial because its static-analysis limits were reached."
            )
        if snapshot.coverage.limitations:
            base.limitations.extend(item for item in snapshot.coverage.limitations if item not in base.limitations)
        return base

    @staticmethod
    def _empty_analysis(
        *,
        user_id: str,
        session_id: str,
        repository_id: str,
        plan: DeliveryPlan,
        changed_paths: list[str],
        graph_commit_sha: str | None,
    ) -> ChangeImpactAnalysis:
        return ChangeImpactAnalysis(
            id=new_id("impact_"),
            user_id=user_id,
            session_id=session_id,
            repository_id=repository_id,
            delivery_plan_id=plan.id,
            plan_base_sha=plan.base_sha,
            graph_commit_sha=graph_commit_sha,
            status=ChangeImpactStatus.UNAVAILABLE,
            changed_paths=changed_paths,
            created_at=utc_now(),
        )

    @staticmethod
    def _target(
        node: GraphNode,
        changed_root: GraphNode,
        distance: int,
        relationship_path: list[ImpactRelationship],
    ) -> ImpactTarget:
        subject = node.path or node.qualified_name or node.name
        changed = changed_root.path or changed_root.name
        relation = relationship_path[-1].relation_type.value.replace("_", " ") if relationship_path else "relates to"
        return ImpactTarget(
            node_id=node.id,
            node_type=node.type,
            name=node.name,
            path=node.path,
            qualified_name=node.qualified_name,
            distance=distance,
            relationship_path=relationship_path,
            explanation=(
                f"Potentially affected: {subject} {relation} a path leading to changed file {changed}. "
                "This is a structural dependency signal, not proof of a runtime failure."
            ),
        )
