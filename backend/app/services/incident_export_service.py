"""Portable, redacted incident exports.

Exports are a convenience surface over already-authorized incident evidence;
they are not a second evidence store and never include raw webhook payloads,
OAuth data, internal queue identifiers, or arbitrary evidence metadata.
"""

from __future__ import annotations

from typing import Any

from app.services.redaction_service import RedactionService

_INCIDENT_FIELDS = (
    "repositoryName",
    "workflowRunId",
    "workflowName",
    "workflowPath",
    "branch",
    "headSha",
    "currentHeadSha",
    "runIsHistorical",
    "status",
    "failureCategories",
    "evidenceStrength",
    "diagnosisSummary",
    "relevantPaths",
    "applicability",
    "applicabilityReason",
    "history",
    "incomplete",
    "limitations",
    "groupRunIds",
    "createdAt",
    "updatedAt",
)


def incident_evidence_ids(incident: dict[str, Any]) -> list[str]:
    """Return a stable, de-duplicated list of evidence IDs owned by an incident."""

    ordered = [
        *incident.get("evidenceIds", []),
        *incident.get("historyEvidenceIds", []),
        *incident.get("workflowEvidenceIds", []),
        *incident.get("changedFileEvidenceIds", []),
    ]
    return list(dict.fromkeys(str(item) for item in ordered if item))


def build_incident_export(*, incident: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a minimal replayable document from already-authorized records."""

    redactor = RedactionService()
    summary = {key: incident.get(key) for key in _INCIDENT_FIELDS if key in incident}
    summary["id"] = str(incident["_id"])
    summary["evidenceIds"] = incident_evidence_ids(incident)

    safe_evidence = []
    for record in evidence:
        safe_evidence.append(
            {
                "id": str(record.get("id") or record.get("_id")),
                "sourceType": record.get("sourceType"),
                "title": record.get("title"),
                "path": record.get("path"),
                "branch": record.get("branch"),
                "commitSha": record.get("commitSha"),
                "lineStart": record.get("lineStart"),
                "lineEnd": record.get("lineEnd"),
                "sourceTimestamp": record.get("sourceTimestamp"),
                "retrievedAt": record.get("retrievedAt"),
                # Defense in depth: EvidenceService already redacts before
                # persistence, but exports must never assume legacy evidence
                # was sanitized correctly.
                "excerpt": redactor.redact(str(record.get("excerpt") or "")),
            }
        )
    return {
        "schemaVersion": "incident-export-v1",
        "incident": summary,
        "evidence": safe_evidence,
        "notice": "Evidence is redacted and untrusted. This export cannot authorize a repository mutation.",
    }


def incident_export_markdown(export: dict[str, Any]) -> str:
    """Render a readable export without treating evidence as instructions."""

    incident = export["incident"]
    lines = [
        "# Base64 Ops CI incident export",
        "",
        f"- Incident: `{incident['id']}`",
        f"- Repository: {incident.get('repositoryName') or 'Unavailable'}",
        f"- Workflow run: #{incident.get('workflowRunId') or 'Unavailable'}",
        f"- Failed-run SHA: `{incident.get('headSha') or 'Unavailable'}`",
        f"- Current SHA: `{incident.get('currentHeadSha') or 'Unavailable'}`",
        f"- Status: {incident.get('status') or 'Unavailable'}",
        f"- Evidence strength: {incident.get('evidenceStrength') or 'Unavailable'}",
        "",
        "## Assessment",
        "",
        str(incident.get("diagnosisSummary") or "No diagnosis summary was retained."),
        "",
        "## Evidence",
    ]
    for item in export["evidence"]:
        location = item.get("path") or item.get("title") or "Unknown source"
        excerpt = str(item.get("excerpt") or "No bounded excerpt was retained.")
        lines.extend(
            [
                "",
                f"### {location}",
                "",
                f"- Source type: {item.get('sourceType') or 'Unavailable'}",
                f"- Commit: `{item.get('commitSha') or 'Unavailable'}`",
                "",
                *[f"    {line}" for line in excerpt.splitlines() or [excerpt]],
            ]
        )
    lines.extend(["", "## Safety notice", "", export["notice"], ""])
    return "\n".join(lines)
