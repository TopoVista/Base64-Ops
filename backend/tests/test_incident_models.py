from types import SimpleNamespace

from app.api.routes.incidents import _incident_response
from app.schemas.incident import EvidenceStrength
from app.services import incident_service
from app.services.incident_service import (
    GitHubWebhookService,
    build_incident_diagnosis,
    build_structured_incident_diagnosis,
)


def _context(**overrides):  # noqa: ANN202
    values = {
        "failure_categories": [],
        "evidence_ids": ["evd_log"],
        "workflow_evidence_ids": [],
        "changed_file_evidence_ids": [],
        "incomplete": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_incident_diagnosis_never_claims_correlation_is_causation() -> None:
    strength, status, summary = build_incident_diagnosis(
        _context(workflow_evidence_ids=["evd_workflow"], changed_file_evidence_ids=["evd_file"])
    )

    assert strength is EvidenceStrength.SUPPORTED
    assert status == "supported"
    assert "not proof of causation" in summary


def test_incident_diagnosis_is_limited_when_ci_logs_are_unavailable() -> None:
    strength, status, summary = build_incident_diagnosis(_context(evidence_ids=[]))

    assert strength is EvidenceStrength.INSUFFICIENT
    assert status == "insufficient"
    assert "No failed-job log excerpt" in summary


def test_structured_incident_diagnosis_is_evidence_scoped() -> None:
    diagnosis = build_structured_incident_diagnosis(
        _context(
            failure_categories=["test_failure"],
            workflow_evidence_ids=["evd_workflow"],
            changed_file_evidence_ids=["evd_source"],
            relevant_paths=["backend/auth.py"],
        )
    )

    assert diagnosis.schema_version == "incident-diagnosis-v1"
    assert diagnosis.status == "supported"
    assert diagnosis.supporting_evidence_ids == ["evd_log", "evd_workflow", "evd_source"]
    assert diagnosis.relevant_paths == ["backend/auth.py"]
    assert "constrained proposal" in diagnosis.recommended_next_action


def test_incident_api_response_never_leaks_internal_tenant_or_queue_routing() -> None:
    response = _incident_response(
        {
            "_id": "inc_1",
            "userId": "user_a",
            "automationId": "aut_1",
            "eventId": "evt_1",
            "repositoryId": "42",
            "repositoryName": "acme/base64",
            "workflowRunId": 99,
            "headSha": "a" * 40,
            "status": "evidence_collected",
            "failureCategories": ["test_failure"],
            "evidenceStrength": "observed",
            "evidenceIds": ["evd_1"],
            "workflowEvidenceIds": [],
            "changedFileEvidenceIds": [],
            "relevantPaths": [],
            "limitations": [],
            "incomplete": False,
            "createdAt": "now",
            "updatedAt": "now",
        }
    )

    assert response["id"] == "inc_1"
    assert "userId" not in response
    assert "automationId" not in response
    assert "eventId" not in response


async def test_automation_settings_update_preserves_the_existing_mongo_id(monkeypatch) -> None:  # noqa: ANN001
    class _Settings:
        def __init__(self) -> None:
            self.record = {"_id": "aut_existing", "userId": "user_a", "sessionId": "ses_1"}

        async def find_one(self, query, _projection=None):  # noqa: ANN001, ANN201
            if all(self.record.get(key) == value for key, value in query.items()):
                return dict(self.record)
            return None

        async def update_one(self, query, update, upsert=False):  # noqa: ANN001, ANN201
            assert upsert is True
            assert query == {"userId": "user_a", "sessionId": "ses_1"}
            assert "_id" not in update["$set"]
            assert update["$setOnInsert"]["_id"] == "aut_existing"
            self.record.update(update["$set"])

    settings = _Settings()
    monkeypatch.setattr(
        incident_service,
        "get_db",
        lambda: SimpleNamespace(repository_automation_settings=settings),
    )

    async def _repositories(_user_id: str):  # noqa: ANN202
        return [{"id": 42, "cloneUrl": "https://github.com/acme/base64.git", "fullName": "acme/base64"}]

    monkeypatch.setattr(incident_service, "list_repositories", _repositories)
    result = await GitHubWebhookService().configure_repository(
        user_id="user_a",
        session={"_id": "ses_1", "repoUrl": "https://github.com/acme/base64", "repoName": "base64"},
        enabled=True,
        workflow_names=["Backend CI"],
        include_branches=["main"],
        exclude_branches=[],
        daily_investigation_limit=5,
    )

    assert result["id"] == "aut_existing"
    assert settings.record["_id"] == "aut_existing"
