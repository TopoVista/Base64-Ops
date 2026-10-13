"""Optional, bounded GitHub-native reporting for persisted incidents.

This service intentionally is not another GitHub client.  It uses the
existing GitHub App installation-token boundary and only creates or updates a
neutral Check Run.  It does not post PR comments, dispatch Actions, rereun
workflows, or change source code.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.core.config import get_settings
from app.services.github_service import (
    github_incident_reporting_is_configured,
    github_installation_api,
    validate_github_app_installation_repository,
)
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now


class IncidentReportingService:
    """Publish a redacted neutral Check only after durable incident storage."""

    def __init__(self, *, redactor: RedactionService | None = None) -> None:
        self.redactor = redactor or RedactionService()

    async def publish_check(
        self,
        *,
        incident: dict[str, Any],
        automation: dict[str, Any],
        candidate_settings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Attempt reporting without making incident collection dependent on it.

        The app installation must have been separately verified by candidate
        verification for the exact session/repository.  This deliberately
        avoids accepting an installation, owner, repository, SHA, or URL from
        a webhook or browser request.
        """

        if not automation.get("githubCheckReportingEnabled"):
            return {"status": "disabled"}
        if not github_incident_reporting_is_configured():
            return {"status": "unavailable", "message": "GitHub Check reporting is not configured."}
        if not candidate_settings or not candidate_settings.get("enabled"):
            return {
                "status": "unavailable",
                "message": "A verified GitHub App repository binding is required for Check reporting.",
            }
        if str(candidate_settings.get("repositoryId") or "") != str(incident.get("repositoryId") or ""):
            return {"status": "unavailable", "message": "GitHub App binding does not match this incident repository."}

        installation_id = str(candidate_settings.get("installationId") or "")
        repository_id = str(incident.get("repositoryId") or "")
        try:
            repository = await validate_github_app_installation_repository(
                installation_id=installation_id,
                repository_id=repository_id,
            )
            owner, name = str(repository["fullName"]).split("/", 1)
            payload = self._check_payload(incident)
            report = incident.get("githubCheckReport") or {}
            check_run_id = report.get("checkRunId")
            if isinstance(check_run_id, int):
                response = await github_installation_api(
                    installation_id,
                    "PATCH",
                    f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/check-runs/{check_run_id}",
                    json=payload,
                )
            else:
                response = await github_installation_api(
                    installation_id,
                    "POST",
                    f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/check-runs",
                    json=payload,
                )
        except Exception:
            # GitHub may reject missing Checks:write, an expired installation,
            # or a network call.  The caller persists only this generic state;
            # provider details/tokens never reach an incident, trace, or UI.
            return {"status": "unavailable", "message": "GitHub Check reporting was unavailable."}

        if not isinstance(response, dict) or not isinstance(response.get("id"), int):
            return {"status": "unavailable", "message": "GitHub did not return a Check Run."}
        return {
            "status": "published",
            "checkRunId": response["id"],
            "url": str(response["html_url"]) if response.get("html_url") else None,
            "publishedAt": utc_now(),
        }

    def _check_payload(self, incident: dict[str, Any]) -> dict[str, Any]:
        diagnosis = incident.get("diagnosis") if isinstance(incident.get("diagnosis"), dict) else {}
        summary = self.redactor.redact(
            str(diagnosis.get("summary") or incident.get("diagnosisSummary") or "Evidence collection completed.")
        )[:4_000]
        categories = ", ".join(str(item) for item in incident.get("failureCategories", [])[:5]) or "unknown"
        details_url = self._details_url(incident)
        output: dict[str, str] = {
            "title": "Base64 Ops CI investigation",
            "summary": summary,
            "text": (
                f"Evidence strength: {incident.get('evidenceStrength') or 'insufficient'}\n"
                f"Failure categories: {categories}\n"
                "This neutral Check reports investigation availability, not a successful repair or passing CI."
            )[:6_000],
        }
        payload: dict[str, Any] = {
            "name": "Base64 Ops CI investigation",
            "head_sha": str(incident["headSha"]),
            "status": "completed",
            "conclusion": "neutral",
            "external_id": str(incident["_id"]),
            "output": output,
        }
        if details_url:
            payload["details_url"] = details_url
        return payload

    @staticmethod
    def _details_url(incident: dict[str, Any]) -> str | None:
        origin = get_settings().frontend_origin.rstrip("/")
        incident_id = str(incident.get("_id") or "")
        if not origin.startswith(("http://", "https://")) or not incident_id:
            return None
        # The public client router can resolve this path only after its normal
        # Clerk authentication boundary; the Check never carries evidence.
        return f"{origin}/incidents/{quote(incident_id, safe='')}"
