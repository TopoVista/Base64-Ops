import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.services import incident_service
from app.services.incident_service import GitHubWebhookService


class _Cursor:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def __aiter__(self):  # noqa: ANN204
        self._iterator = iter(self.records)
        return self

    async def __anext__(self):  # noqa: ANN201
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Events:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def insert_one(self, document):  # noqa: ANN001, ANN201
        if any(record["deliveryId"] == document["deliveryId"] for record in self.records):
            raise DuplicateKeyError("github_delivery_once")
        self.records.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one(self, query):  # noqa: ANN001, ANN201
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                return record
        return None

    async def update_one(self, query, update):  # noqa: ANN001, ANN201
        record = await self.find_one(query)
        if record:
            record.update(update.get("$set", {}))


class _Automation:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def find(self, query):  # noqa: ANN001, ANN201
        records = [
            record
            for record in self.records
            if record.get("repositoryId") == query.get("repositoryId") and record.get("enabled") is True
        ]
        return _Cursor(records)


class _Incidents:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.queries: list[dict] = []

    async def update_many(self, query, update):  # noqa: ANN001, ANN201
        self.queries.append(query)
        updated = 0
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                record.update(update.get("$set", {}))
                updated += 1
        return SimpleNamespace(modified_count=updated)


def _payload(*, conclusion: str = "failure", branch: str = "main") -> bytes:
    return json.dumps(
        {
            "action": "completed",
            "repository": {
                "id": 42,
                "full_name": "acme/base64",
                "clone_url": "https://github.com/acme/base64.git",
            },
            "workflow_run": {
                "id": 99,
                "name": "Backend CI",
                "path": ".github/workflows/ci.yml",
                "head_branch": branch,
                "head_sha": "a" * 40,
                "conclusion": conclusion,
            },
        }
    ).encode()


def _signature(body: bytes, secret: str = "webhook-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def webhook_db(monkeypatch):  # noqa: ANN201
    events = _Events()
    automation = _Automation(
        [
            {
                "_id": "aut_1",
                "userId": "user_a",
                "sessionId": "ses_1",
                "repositoryId": "42",
                "enabled": True,
                "workflowNames": ["Backend CI"],
                "includeBranches": ["main"],
                "excludeBranches": [],
            }
        ]
    )
    database = SimpleNamespace(
        github_webhook_events=events,
        repository_automation_settings=automation,
        incidents=_Incidents(),
    )
    monkeypatch.setattr(incident_service, "get_db", lambda: database)
    monkeypatch.setattr(incident_service, "get_settings", lambda: Settings(github_webhook_secret="webhook-secret"))
    return database


async def test_signed_workflow_failure_is_durably_recorded_before_dispatch(monkeypatch, webhook_db) -> None:
    queued: list[str] = []

    async def enqueue(self, *, event_id: str):  # noqa: ANN001, ANN202
        queued.append(event_id)
        return {"id": "job_1", "status": "queued", "deduplicated": False}

    monkeypatch.setattr(incident_service.BackgroundJobService, "enqueue_incident_dispatch", enqueue)
    body = _payload()

    result = await GitHubWebhookService().accept_delivery(
        body=body,
        event_name="workflow_run",
        action=None,
        delivery_id="delivery-1",
        signature=_signature(body),
    )

    assert result == {"accepted": True, "eventId": queued[0], "status": "queued", "targets": 1}
    saved = webhook_db.github_webhook_events.records[0]
    assert saved["payloadHash"] == hashlib.sha256(body).hexdigest()
    assert "workflow_run" not in saved
    assert saved["targetAutomationIds"] == ["aut_1"]
    assert saved["dispatchJobId"] == "job_1"


async def test_invalid_signature_cannot_persist_or_dispatch(monkeypatch, webhook_db) -> None:
    called = False

    async def enqueue(self, *, event_id: str):  # noqa: ANN001, ANN202
        nonlocal called
        called = True
        return {"id": "job_1", "status": "queued", "deduplicated": False}

    monkeypatch.setattr(incident_service.BackgroundJobService, "enqueue_incident_dispatch", enqueue)
    with pytest.raises(HTTPException) as exc:
        await GitHubWebhookService().accept_delivery(
            body=_payload(),
            event_name="workflow_run",
            action=None,
            delivery_id="delivery-1",
            signature="sha256=forged",
        )
    assert exc.value.status_code == 401
    assert not webhook_db.github_webhook_events.records
    assert not called


async def test_duplicate_delivery_does_not_create_a_second_job(monkeypatch, webhook_db) -> None:
    calls = 0

    async def enqueue(self, *, event_id: str):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        return {"id": "job_1", "status": "queued", "deduplicated": False}

    monkeypatch.setattr(incident_service.BackgroundJobService, "enqueue_incident_dispatch", enqueue)
    body = _payload()
    service = GitHubWebhookService()
    first = await service.accept_delivery(
        body=body, event_name="workflow_run", action=None, delivery_id="delivery-1", signature=_signature(body)
    )
    second = await service.accept_delivery(
        body=body, event_name="workflow_run", action=None, delivery_id="delivery-1", signature=_signature(body)
    )

    assert first["accepted"]
    assert second["duplicate"]
    assert calls == 1
    assert len(webhook_db.github_webhook_events.records) == 1


async def test_successful_or_branch_filtered_events_are_safely_ignored(monkeypatch, webhook_db) -> None:
    async def enqueue(self, *, event_id: str):  # noqa: ANN001, ANN202
        raise AssertionError("ignored event must not dispatch")

    monkeypatch.setattr(incident_service.BackgroundJobService, "enqueue_incident_dispatch", enqueue)
    service = GitHubWebhookService()
    success = _payload(conclusion="success")
    success_result = await service.accept_delivery(
        body=success,
        event_name="workflow_run",
        action=None,
        delivery_id="delivery-success",
        signature=_signature(success),
    )
    assert success_result["ignored"]
    assert success_result["reconciledIncidents"] == 0

    branch = _payload(branch="release")
    result = await service.accept_delivery(
        body=branch,
        event_name="workflow_run",
        action=None,
        delivery_id="delivery-release",
        signature=_signature(branch),
    )
    assert result["status"] == "ignored"


async def test_success_on_the_same_sha_is_recorded_without_resolving_the_incident(monkeypatch, webhook_db) -> None:
    async def enqueue(self, *, event_id: str):  # noqa: ANN001, ANN202
        raise AssertionError("successful runs must not enqueue a failure investigation")

    monkeypatch.setattr(incident_service.BackgroundJobService, "enqueue_incident_dispatch", enqueue)
    webhook_db.incidents.records.append(
        {
            "_id": "inc_1",
            "userId": "user_a",
            "sessionId": "ses_1",
            "repositoryId": "42",
            "workflowPath": ".github/workflows/ci.yml",
            "branch": "main",
            "headSha": "a" * 40,
            "isGroupRoot": True,
            "status": "evidence_collected",
        }
    )
    body = _payload(conclusion="success")

    result = await GitHubWebhookService().accept_delivery(
        body=body,
        event_name="workflow_run",
        action=None,
        delivery_id="delivery-same-sha-success",
        signature=_signature(body),
    )

    assert result["reconciledIncidents"] == 1
    incident = webhook_db.incidents.records[0]
    assert incident["status"] == "evidence_collected"
    assert incident["sameShaSuccess"]["workflowRunId"] == 99
