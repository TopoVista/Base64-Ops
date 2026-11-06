"""Tenant-isolated, evidence-backed organization incident graph.

The graph is deliberately modest while MongoDB remains the primary store.  It
records observed repository/commit/workflow/runner/incident relationships and
derives only bounded *correlations* between independently observed incidents.
It does not represent correlation as causation and it never stores raw logs,
source files, prompts, or provider payloads.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from app.db.mongo import get_db
from app.utils.datetime import utc_now

_MAX_RELATED_INCIDENTS = 100
_MAX_CORRELATIONS_PER_INCIDENT = 25


def _node_id(tenant_id: str, node_type: str, key: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\x1f{node_type}\x1f{key}".encode()).hexdigest()
    return f"ogn_{digest}"


def _edge_id(tenant_id: str, source_id: str, target_id: str, relation: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\x1f{source_id}\x1f{target_id}\x1f{relation}".encode()).hexdigest()
    return f"oge_{digest}"


def _bounded_strings(values: Iterable[object], *, limit: int = 20, max_length: int = 240) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()[:max_length]
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) == limit:
            break
    return result


def correlation_basis(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """Return observed shared signals without making a causal claim."""

    basis: list[str] = []
    left_workflow = left.get("workflowKey")
    if isinstance(left_workflow, str) and left_workflow and left_workflow == right.get("workflowKey"):
        basis.append("same_workflow")
    if set(_bounded_strings(left.get("runnerLabels") or [])) & set(_bounded_strings(right.get("runnerLabels") or [])):
        basis.append("same_runner_label")
    if set(_bounded_strings(left.get("failureCategories") or [])) & set(
        _bounded_strings(right.get("failureCategories") or [])
    ):
        basis.append("same_failure_category")
    return basis


class OrganizationIncidentGraphService:
    """Project incident evidence into a compact, tenant-scoped temporal graph."""

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.organization_graph_nodes.create_index(
            [("tenantId", 1), ("nodeType", 1), ("key", 1)],
            unique=True,
            name="organization_graph_node_identity",
        )
        await db.organization_graph_nodes.create_index(
            [("tenantId", 1), ("nodeType", 1), ("updatedAt", -1)], name="organization_graph_tenant_nodes"
        )
        await db.organization_graph_edges.create_index(
            [("tenantId", 1), ("sourceNodeId", 1), ("targetNodeId", 1), ("relation", 1)],
            unique=True,
            name="organization_graph_edge_identity",
        )
        await db.organization_graph_edges.create_index(
            [("tenantId", 1), ("relation", 1), ("updatedAt", -1)], name="organization_graph_tenant_edges"
        )

    @staticmethod
    def incident_signals(*, incident: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        capsule_inputs = context.get("capsule_inputs") or context.get("capsuleInputs") or {}
        if not isinstance(capsule_inputs, dict):
            capsule_inputs = {}
        workflow_key = str(
            incident.get("workflowPath")
            or context.get("workflow_path")
            or incident.get("workflowName")
            or context.get("workflow_name")
            or ""
        )[:240]
        return {
            "workflowKey": workflow_key or None,
            "runnerLabels": _bounded_strings(capsule_inputs.get("runnerLabels") or []),
            "failureCategories": _bounded_strings(
                incident.get("failureCategories") or context.get("failure_categories") or []
            ),
            "relevantPaths": _bounded_strings(
                incident.get("relevantPaths") or context.get("relevant_paths") or [], limit=30
            ),
        }

    async def ingest_incident(
        self,
        *,
        tenant_id: str,
        incident: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Upsert observed facts then attach bounded evidence-based correlations."""

        repository_id = str(incident.get("repositoryId") or "")
        incident_id = str(incident.get("_id") or incident.get("id") or "")
        if not tenant_id or not repository_id or not incident_id:
            raise ValueError(
                "Tenant, repository, and incident identities are required for organization graph ingestion."
            )
        now = utc_now()
        signals = self.incident_signals(incident=incident, context=context)
        source_refs = _bounded_strings(
            [incident_id, incident.get("capsuleManifestHash"), *(incident.get("evidenceIds") or [])], limit=30
        )
        repository = self._node(tenant_id, "repository", repository_id, repository_id, now, source_refs)
        incident_node = self._node(
            tenant_id,
            "incident",
            incident_id,
            str(incident.get("diagnosisSummary") or incident.get("workflowName") or "CI incident")[:240],
            now,
            source_refs,
            signals=signals,
        )
        nodes = [repository, incident_node]
        edges = [
            self._edge(
                tenant_id, incident_node["_id"], repository["_id"], "occurred_in", "observed", now, source_refs
            )
        ]

        commit_sha = str(incident.get("headSha") or context.get("head_sha") or "")
        if commit_sha:
            commit = self._node(tenant_id, "commit", f"{repository_id}:{commit_sha}", commit_sha, now, source_refs)
            nodes.append(commit)
            edges.append(
                self._edge(tenant_id, incident_node["_id"], commit["_id"], "at_revision", "observed", now, source_refs)
            )
        workflow_key = signals["workflowKey"]
        if workflow_key:
            workflow = self._node(
                tenant_id, "workflow", f"{repository_id}:{workflow_key}", workflow_key, now, source_refs
            )
            nodes.append(workflow)
            edges.extend(
                [
                    self._edge(
                        tenant_id, repository["_id"], workflow["_id"], "contains", "observed", now, source_refs
                    ),
                    self._edge(
                        tenant_id, incident_node["_id"], workflow["_id"], "failed_in", "observed", now, source_refs
                    ),
                ]
            )
        for runner_label in signals["runnerLabels"]:
            runner = self._node(tenant_id, "runner", runner_label, runner_label, now, source_refs)
            nodes.append(runner)
            edges.append(
                self._edge(tenant_id, incident_node["_id"], runner["_id"], "used_runner", "observed", now, source_refs)
            )
        for category in signals["failureCategories"]:
            category_node = self._node(tenant_id, "failure_category", category, category, now, source_refs)
            nodes.append(category_node)
            edges.append(
                self._edge(
                    tenant_id,
                    incident_node["_id"],
                    category_node["_id"],
                    "has_failure_category",
                    "observed",
                    now,
                    source_refs,
                )
            )

        db = get_db()
        for node in nodes:
            await db.organization_graph_nodes.update_one(
                {"_id": node["_id"], "tenantId": tenant_id},
                {"$set": node, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )
        for edge in edges:
            await db.organization_graph_edges.update_one(
                {"_id": edge["_id"], "tenantId": tenant_id},
                {"$set": edge, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )

        candidates = await (
            db.organization_graph_nodes.find(
                {"tenantId": tenant_id, "nodeType": "incident", "_id": {"$ne": incident_node["_id"]}},
                {"_id": 1, "signals": 1},
            )
            .sort("updatedAt", -1)
            .limit(_MAX_RELATED_INCIDENTS)
            .to_list(_MAX_RELATED_INCIDENTS)
        )
        correlations = 0
        for candidate in candidates:
            candidate_signals = candidate.get("signals") if isinstance(candidate.get("signals"), dict) else {}
            basis = correlation_basis(signals, candidate_signals)
            if not basis:
                continue
            correlation = self._edge(
                tenant_id,
                incident_node["_id"],
                str(candidate["_id"]),
                "correlated_with",
                "correlated",
                now,
                source_refs,
                basis=basis,
            )
            await db.organization_graph_edges.update_one(
                {"_id": correlation["_id"], "tenantId": tenant_id},
                {"$set": correlation, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )
            correlations += 1
            if correlations >= _MAX_CORRELATIONS_PER_INCIDENT:
                break
        return {"nodes": len(nodes), "observedEdges": len(edges), "correlations": correlations}

    async def fleet_view(self, *, tenant_id: str, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
        """Read a safe fleet view; all queries include the tenant boundary."""

        bounded_limit = max(1, min(int(limit), 100))
        db = get_db()
        incidents = await (
            db.organization_graph_nodes.find(
                {"tenantId": tenant_id, "nodeType": "incident"},
                {"_id": 1, "key": 1, "label": 1, "signals": 1, "updatedAt": 1},
            )
            .sort("updatedAt", -1)
            .limit(bounded_limit)
            .to_list(bounded_limit)
        )
        incident_ids = [str(item["_id"]) for item in incidents]
        correlations = []
        if incident_ids:
            correlations = await (
                db.organization_graph_edges.find(
                    {
                        "tenantId": tenant_id,
                        "relation": "correlated_with",
                        "$or": [{"sourceNodeId": {"$in": incident_ids}}, {"targetNodeId": {"$in": incident_ids}}],
                    },
                    {"sourceNodeId": 1, "targetNodeId": 1, "relation": 1, "certainty": 1, "basis": 1, "updatedAt": 1},
                )
                .sort("updatedAt", -1)
                .limit(bounded_limit)
                .to_list(bounded_limit)
            )
        return {"incidents": incidents, "correlations": correlations}

    @staticmethod
    def _node(
        tenant_id: str,
        node_type: str,
        key: str,
        label: str,
        now: object,
        source_refs: list[str],
        *,
        signals: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "_id": _node_id(tenant_id, node_type, key),
            "tenantId": tenant_id,
            "nodeType": node_type,
            "key": key,
            "label": label,
            "signals": signals or {},
            "sourceRefs": source_refs,
            "updatedAt": now,
        }

    @staticmethod
    def _edge(
        tenant_id: str,
        source_id: str,
        target_id: str,
        relation: str,
        certainty: str,
        now: object,
        source_refs: list[str],
        *,
        basis: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "_id": _edge_id(tenant_id, source_id, target_id, relation),
            "tenantId": tenant_id,
            "sourceNodeId": source_id,
            "targetNodeId": target_id,
            "relation": relation,
            "certainty": certainty,
            "basis": basis or [],
            "sourceRefs": source_refs,
            "updatedAt": now,
        }
