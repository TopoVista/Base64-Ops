import json
from types import SimpleNamespace

from app.api.routes import incidents as incident_routes
from app.api.routes.incidents import _incident_response
from app.git.actions import WorkflowJobSummary
from app.schemas.incident import EvidenceStrength
from app.services import incident_service
from app.services.incident_export_service import build_incident_export, incident_export_markdown
from app.services.incident_service import (
    GitHubWebhookService,
    IncidentDispatchService,
    _flaky_signal,
    build_incident_diagnosis,
    build_structured_incident_diagnosis,
    incident_dispatch_is_complete,
    incident_group_key,
    incident_memory_candidate,
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


def test_incident_group_key_requires_matching_repository_workflow_commit_and_failures() -> None:
    context = _context(
        failure_categories=["test_failure"],
        failed_jobs=[WorkflowJobSummary(id=1, run_id=9, name="test", failed_step="Run pytest")],
    )
    event = {
        "repositoryId": "42",
        "workflowPath": ".github/workflows/ci.yml",
        "workflowName": "CI",
        "branch": "main",
        "headSha": "a" * 40,
    }

    key = incident_group_key(event=event, context=context)
    assert key == incident_group_key(event=event, context=context)
    assert key != incident_group_key(event={**event, "headSha": "b" * 40}, context=context)
    assert key != incident_group_key(
        event=event,
        context=_context(
            failure_categories=["dependency_failure"],
            failed_jobs=[WorkflowJobSummary(id=1, run_id=9, name="test", failed_step="Run pytest")],
        ),
    )


def test_incident_dispatch_retries_an_ungrouped_collected_record() -> None:
    """A transient grouping failure must not turn a collected incident terminal."""

    assert not incident_dispatch_is_complete({"status": "evidence_collected"})
    assert incident_dispatch_is_complete(
        {"status": "evidence_collected", "groupKey": "igr_123"}
    )
    assert incident_dispatch_is_complete({"status": "grouped"})
    assert incident_dispatch_is_complete({"status": "ignored"})
    assert not incident_dispatch_is_complete({"status": "failed"})


def test_flaky_signal_requires_a_test_identity_and_mixed_same_sha_history() -> None:
    assert _flaky_signal(_context(history=SimpleNamespace(assessment="mixed_outcomes_same_sha"))) is None
    assert _flaky_signal(
        _context(
            history=SimpleNamespace(assessment="mixed_outcomes_same_sha"),
            failed_test_identifiers=["tests/test_login.py::test_login"],
        )
    ) == "suspected"


def test_only_evidence_supported_incidents_become_historical_memory() -> None:
    supported_context = _context(
        failure_categories=["dependency_failure"],
        workflow_path=".github/workflows/ci.yml",
        relevant_paths=["backend/requirements.txt"],
        workflow_evidence_ids=["evd_workflow"],
        changed_file_evidence_ids=["evd_requirements"],
    )
    diagnosis = build_structured_incident_diagnosis(supported_context)
    candidate = incident_memory_candidate(context=supported_context, diagnosis=diagnosis)

    assert candidate is not None
    assert str(candidate.kind) == "incident_outcome"
    assert candidate.confidence == 0.75
    assert candidate.value == diagnosis.summary
    assert "github-actions" in candidate.tags

    insufficient = build_structured_incident_diagnosis(_context(evidence_ids=[]))
    assert incident_memory_candidate(context=_context(evidence_ids=[]), diagnosis=insufficient) is None


def test_incident_export_re_redacts_and_omits_internal_routing_fields() -> None:
    export = build_incident_export(
        incident={
            "_id": "inc_1",
            "userId": "user_a",
            "sessionId": "ses_1",
            "eventId": "evt_1",
            "repositoryName": "acme/base64",
            "workflowRunId": 99,
            "headSha": "a" * 40,
            "status": "evidence_collected",
            "evidenceIds": ["evd_1"],
        },
        evidence=[
            {
                "_id": "evd_1",
                "id": "evd_1",
                "userId": "user_a",
                "sessionId": "ses_1",
                "sourceType": "ci_log",
                "path": ".github/workflows/ci.yml",
                "excerpt": "Authorization: Bearer very-secret-token",
                "metadata": {"token": "never export this"},
            }
        ],
    )

    assert export["incident"]["id"] == "inc_1"
    assert "userId" not in export["incident"]
    assert "eventId" not in export["incident"]
    assert "very-secret-token" not in export["evidence"][0]["excerpt"]
    assert "metadata" not in export["evidence"][0]
    markdown = incident_export_markdown(export)
    assert "very-secret-token" not in markdown
    assert "Authorization: Bearer [REDACTED]" in markdown


async def test_incident_export_scopes_evidence_to_the_incident_tenant_and_session(monkeypatch) -> None:  # noqa: ANN001
    class _EvidenceCursor:
        async def to_list(self, _limit):  # noqa: ANN001, ANN201
            return [
                {
                    "_id": "evd_1",
                    "id": "evd_1",
                    "sourceType": "ci_log",
                    "excerpt": "Bearer export-token-should-not-appear",
                }
            ]

    class _Evidence:
        query: dict | None = None

        def find(self, query):  # noqa: ANN001, ANN201
            self.query = query
            return _EvidenceCursor()

    class _Incidents:
        async def find_one(self, query):  # noqa: ANN001, ANN201
            assert query == {"_id": "inc_1", "userId": "user_a"}
            return {
                "_id": "inc_1",
                "userId": "user_a",
                "sessionId": "ses_1",
                "repositoryName": "acme/base64",
                "workflowRunId": 99,
                "headSha": "a" * 40,
                "evidenceIds": ["evd_1"],
            }

    evidence = _Evidence()
    monkeypatch.setattr(
        incident_routes,
        "get_db",
        lambda: SimpleNamespace(incidents=_Incidents(), evidence=evidence),
    )

    response = await incident_routes.export_incident("inc_1", format="json", user={"_id": "user_a"})
    body = json.loads(response.body)
    assert evidence.query == {"id": {"$in": ["evd_1"]}, "userId": "user_a", "sessionId": "ses_1"}
    assert body["incident"]["id"] == "inc_1"
    assert "export-token-should-not-appear" not in body["evidence"][0]["excerpt"]


async def test_incident_grouping_keeps_one_root_and_preserves_related_runs(monkeypatch) -> None:  # noqa: ANN001
    class _Incidents:
        def __init__(self) -> None:
            self.records = [
                {"_id": "inc_1", "userId": "user_a", "status": "evidence_collected"},
                {"_id": "inc_2", "userId": "user_a", "status": "evidence_collected"},
            ]

        async def find_one(self, query):  # noqa: ANN001, ANN201
            for record in self.records:
                matches = True
                for key, value in query.items():
                    if isinstance(value, dict) and "$ne" in value:
                        matches = matches and record.get(key) != value["$ne"]
                    else:
                        matches = matches and record.get(key) == value
                if matches:
                    return record
            return None

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            record = await self.find_one(query)
            assert record is not None
            set_values = update.get("$set", {})
            candidate_key = set_values.get("activeGroupKey")
            if candidate_key and any(
                item is not record and item.get("activeGroupKey") == candidate_key for item in self.records
            ):
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError("incident_one_active_group_per_user")
            record.update(set_values)
            for key, value in update.get("$addToSet", {}).items():
                record.setdefault(key, [])
                if value not in record[key]:
                    record[key].append(value)
            for key, value in update.get("$push", {}).items():
                record.setdefault(key, [])
                entries = value.get("$each", [value]) if isinstance(value, dict) else [value]
                record[key].extend(entries)
                if isinstance(value, dict) and "$slice" in value:
                    record[key] = record[key][value["$slice"] :]

    incidents = _Incidents()
    monkeypatch.setattr(incident_service, "get_db", lambda: SimpleNamespace(incidents=incidents))
    context = _context(
        failure_categories=["test_failure"],
        failed_jobs=[WorkflowJobSummary(id=1, run_id=9, name="test", failed_step="Run pytest")],
    )
    automation = {"_id": "aut_1", "userId": "user_a"}
    event_one = {
        "repositoryId": "42",
        "workflowPath": ".github/workflows/ci.yml",
        "workflowName": "CI",
        "branch": "main",
        "headSha": "a" * 40,
        "workflowRunId": 9,
    }
    event_two = {**event_one, "workflowRunId": 10}
    service = IncidentDispatchService()

    await service._assign_incident_group(
        incident_id="inc_1", event=event_one, automation=automation, context=context
    )
    await service._assign_incident_group(
        incident_id="inc_2", event=event_two, automation=automation, context=context
    )

    root, grouped = incidents.records
    assert root["isGroupRoot"] is True
    assert root["groupRunIds"] == [9, 10]
    assert [entry["workflowRunId"] for entry in root["groupTimeline"]] == [9, 10]
    assert root["firstObservedAt"] <= root["lastObservedAt"]
    assert grouped["status"] == "grouped"
    assert grouped["groupRootId"] == "inc_1"


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


async def test_incident_feedback_is_tenant_scoped_redacted_and_not_a_diagnosis_mutation(monkeypatch) -> None:  # noqa: ANN001
    from app.schemas.incident import IncidentFeedbackRequest

    class _Incidents:
        def __init__(self) -> None:
            self.record = {
                "_id": "inc_1", "userId": "user_a", "repositoryId": "repo_a",
                "repositoryName": "acme/base64", "workflowRunId": 1, "headSha": "a" * 40,
                "status": "evidence_collected", "failureCategories": [], "evidenceStrength": "observed",
                "evidenceIds": [], "workflowEvidenceIds": [], "changedFileEvidenceIds": [],
                "relevantPaths": [], "incomplete": False, "limitations": [],
                "diagnosisSummary": "Original immutable diagnosis", "createdAt": None, "updatedAt": None,
            }

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            if query.get("_id") != self.record["_id"] or query.get("userId") != self.record["userId"]:
                return SimpleNamespace(matched_count=0)
            self.record.update(update["$set"])
            return SimpleNamespace(matched_count=1)

        async def find_one(self, query):  # noqa: ANN001, ANN201
            return self.record if query.get("userId") == self.record["userId"] else None

    records = _Incidents()
    monkeypatch.setattr(incident_routes, "get_db", lambda: SimpleNamespace(incidents=records))

    response = await incident_routes.submit_incident_feedback(
        "inc_1",
        IncidentFeedbackRequest(disposition="incorrect", correction="Authorization: Bearer feedback-secret"),
        {"_id": "user_a"},
    )

    assert response["diagnosisSummary"] == "Original immutable diagnosis"
    assert response["feedback"]["disposition"] == "incorrect"
    assert "feedback-secret" not in response["feedback"]["correction"]
    assert response["feedback"]["untrusted"] is True


async def test_incorrect_incident_feedback_records_redacted_user_correction_memory(monkeypatch) -> None:  # noqa: ANN001
    from app.schemas.incident import IncidentFeedbackRequest

    class _Incidents:
        def __init__(self) -> None:
            self.record = {
                "_id": "inc_1",
                "userId": "user_a",
                "sessionId": "ses_1",
                "repositoryId": "repo_a",
                "workflowRunId": 42,
                "branch": "main",
                "headSha": "a" * 40,
                "failureCategories": ["test_failure"],
                "relevantPaths": ["backend/app/auth.py"],
                "limitations": [],
                "createdAt": None,
                "updatedAt": None,
            }

        async def update_one(self, _query, update):  # noqa: ANN001, ANN201
            self.record.update(update["$set"])
            return SimpleNamespace(matched_count=1)

        async def find_one(self, query):  # noqa: ANN001, ANN201
            return dict(self.record) if query.get("userId") == self.record["userId"] else None

    class _Sessions:
        async def find_one(self, query, _projection):  # noqa: ANN001, ANN201
            if query == {"_id": "ses_1", "userId": "user_a"}:
                return {"repoUrl": "https://github.com/acme/base64.git", "defaultBranch": "main"}
            return None

    class _Rules:
        async def invalidate_for_incident(self, **_kwargs):  # noqa: ANN003, ANN201
            return 1

        async def refresh_for_repository(self, **_kwargs):  # noqa: ANN003, ANN201
            return ["prv_1"]

    class _Memory:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def record_user_correction(self, **kwargs):  # noqa: ANN003, ANN201
            self.calls.append(kwargs)
            return ["mem_correction"]

    records = _Incidents()
    memory = _Memory()
    monkeypatch.setattr(
        incident_routes,
        "get_db",
        lambda: SimpleNamespace(incidents=records, sessions=_Sessions()),
    )
    monkeypatch.setattr(incident_routes, "PreventionRuleService", _Rules)
    monkeypatch.setattr(incident_routes, "MemoryService", lambda: memory)

    await incident_routes.submit_incident_feedback(
        "inc_1",
        IncidentFeedbackRequest(disposition="incorrect", correction="Authorization: Bearer feedback-secret"),
        {"_id": "user_a"},
    )

    assert len(memory.calls) == 1
    call = memory.calls[0]
    assert call["repository_id"] == "https://github.com/acme/base64.git"
    candidate = call["candidates"][0]
    assert candidate.kind.value == "user_correction"
    assert candidate.metadata["incidentId"] == "inc_1"
    assert "feedback-secret" not in candidate.value
    assert candidate.metadata["untrusted"] is True


async def test_incident_dispatch_records_only_a_policy_gated_memory_summary(monkeypatch) -> None:  # noqa: ANN001
    class _Memory:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def record_incident_outcome(self, **kwargs):  # noqa: ANN003, ANN201
            self.calls.append(kwargs)
            return ["mem_1"]

    class _Incidents:
        def __init__(self) -> None:
            self.updates: list[dict] = []

        async def update_one(self, _query, update):  # noqa: ANN001, ANN201
            self.updates.append(update)

    incidents = _Incidents()
    monkeypatch.setattr(incident_service, "get_db", lambda: SimpleNamespace(incidents=incidents))
    context = _context(
        failure_categories=["workflow_configuration"],
        workflow_path=".github/workflows/ci.yml",
        relevant_paths=[".github/workflows/ci.yml"],
        workflow_evidence_ids=["evd_workflow"],
        changed_file_evidence_ids=["evd_change"],
    )
    diagnosis = build_structured_incident_diagnosis(context)
    memory = _Memory()
    service = IncidentDispatchService(memory=memory)

    await service._record_incident_memory(
        incident_id="inc_1",
        event={"branch": "main", "headSha": "a" * 40},
        session={"userId": "user_a", "repoUrl": "https://github.com/acme/base64.git", "defaultBranch": "main"},
        context=context,
        diagnosis=diagnosis,
    )

    assert len(memory.calls) == 1
    assert memory.calls[0]["verified_evidence_ids"] == {"evd_log", "evd_workflow", "evd_change"}
    assert memory.calls[0]["candidates"][0].kind.value == "incident_outcome"
    assert incidents.updates[-1]["$set"]["memoryEntryCount"] == 1
