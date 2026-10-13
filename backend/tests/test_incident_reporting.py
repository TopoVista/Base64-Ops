from types import SimpleNamespace

import pytest

from app.services import incident_reporting_service
from app.services.incident_reporting_service import IncidentReportingService


def _incident() -> dict:
    return {
        "_id": "inc_1",
        "sessionId": "ses_1",
        "repositoryId": "42",
        "headSha": "a" * 40,
        "failureCategories": ["dependency_failure"],
        "evidenceStrength": "supported",
        "diagnosisSummary": "Authorization: Bearer synthetic-secret was redacted before reporting.",
    }


@pytest.mark.asyncio
async def test_native_incident_report_is_a_neutral_redacted_check(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []

    async def validate(*, installation_id: str, repository_id: str):  # noqa: ANN202
        assert (installation_id, repository_id) == ("123", "42")
        return {"fullName": "acme/base64", "cloneUrl": "https://github.com/acme/base64.git"}

    async def github_call(installation_id: str, method: str, path: str, **kwargs):  # noqa: ANN202
        assert installation_id == "123"
        calls.append((method, path, kwargs))
        return {"id": 99, "html_url": "https://github.com/acme/base64/runs/99"}

    monkeypatch.setattr(incident_reporting_service, "github_incident_reporting_is_configured", lambda: True)
    monkeypatch.setattr(incident_reporting_service, "validate_github_app_installation_repository", validate)
    monkeypatch.setattr(incident_reporting_service, "github_installation_api", github_call)
    monkeypatch.setattr(
        incident_reporting_service,
        "get_settings",
        lambda: SimpleNamespace(frontend_origin="https://base64.example"),
    )

    result = await IncidentReportingService().publish_check(
        incident=_incident(),
        automation={"githubCheckReportingEnabled": True},
        candidate_settings={"enabled": True, "repositoryId": "42", "installationId": "123"},
    )

    assert result["status"] == "published"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/repos/acme/base64/check-runs"
    payload = calls[0][2]["json"]
    assert payload["conclusion"] == "neutral"
    assert payload["head_sha"] == "a" * 40
    assert payload["details_url"] == "https://base64.example/incidents/inc_1"
    assert "synthetic-secret" not in payload["output"]["summary"]
    assert "not a successful repair" in payload["output"]["text"]


@pytest.mark.asyncio
async def test_native_incident_report_requires_existing_verified_app_binding(monkeypatch) -> None:
    monkeypatch.setattr(incident_reporting_service, "github_incident_reporting_is_configured", lambda: True)

    result = await IncidentReportingService().publish_check(
        incident=_incident(),
        automation={"githubCheckReportingEnabled": True},
        candidate_settings=None,
    )

    assert result["status"] == "unavailable"
    assert "binding" in result["message"].lower()


@pytest.mark.asyncio
async def test_native_incident_report_updates_an_existing_check(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def validate(**_kwargs):  # noqa: ANN202
        return {"fullName": "acme/base64", "cloneUrl": "https://github.com/acme/base64.git"}

    async def github_call(_installation_id: str, method: str, path: str, **_kwargs):  # noqa: ANN202
        calls.append((method, path))
        return {"id": 99, "html_url": "https://github.com/acme/base64/runs/99"}

    monkeypatch.setattr(incident_reporting_service, "github_incident_reporting_is_configured", lambda: True)
    monkeypatch.setattr(incident_reporting_service, "validate_github_app_installation_repository", validate)
    monkeypatch.setattr(incident_reporting_service, "github_installation_api", github_call)
    incident = {**_incident(), "githubCheckReport": {"checkRunId": 99}}

    result = await IncidentReportingService().publish_check(
        incident=incident,
        automation={"githubCheckReportingEnabled": True},
        candidate_settings={"enabled": True, "repositoryId": "42", "installationId": "123"},
    )

    assert result["status"] == "published"
    assert calls == [("PATCH", "/repos/acme/base64/check-runs/99")]
