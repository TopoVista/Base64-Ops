import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_current_user_id
from app.api.routes import dev, session
from app.schemas.replay import RunReplayResponse, SessionRunHistoryResponse
from app.services import session_service
from app.services.serializers import serialize_doc


class _Cursor:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    def sort(self, *_args: Any, **_kwargs: Any) -> "_Cursor":
        return self

    def limit(self, _value: int) -> "_Cursor":
        return self

    async def to_list(self, limit: int) -> list[dict[str, Any]]:
        return self.values[:limit]


class _Collection:
    def __init__(self, values: list[dict[str, Any]] | None = None) -> None:
        self.values = values or []
        self.queries: list[dict[str, Any]] = []
        self.projections: list[dict[str, Any] | None] = []

    async def find_one(self, query: dict[str, Any], **_kwargs: Any) -> dict[str, Any] | None:
        self.queries.append(query)
        for value in self.values:
            if self._matches(value, query):
                return value
        return None

    def find(self, query: dict[str, Any], _projection: dict[str, Any] | None = None) -> _Cursor:
        self.queries.append(query)
        self.projections.append(_projection)
        matching = [value for value in self.values if self._matches(value, query)]
        return _Cursor(matching)

    @staticmethod
    def _matches(value: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            if key == "$or":
                if not any(_Collection._matches(value, option) for option in expected):
                    return False
                continue
            if isinstance(expected, dict) and "$in" in expected:
                if value.get(key) not in expected["$in"]:
                    return False
                continue
            if value.get(key) != expected:
                return False
        return True


class _ReplayDb:
    def __init__(self, *, with_trace: bool = True) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        self.sessions = _Collection(
            [{"_id": "ses_a", "userId": "user-a", "slugId": "repo-a", "repoName": "Repository A"}]
        )
        self.investigations = _Collection(
            [
                {
                    "userId": "user-a",
                    "sessionId": "ses_a",
                    "runId": "run-a",
                    "repository": {"headSha": "abc123"},
                    "investigation": {"diagnosis": "A persisted, evidence-backed result."},
                    "timeline": [{"label": "Run started", "detail": "Historical event"}],
                    "ci": {"run_id": 42, "head_sha": "abc123", "limitations": ["Logs were bounded."]},
                    "deliveryPlan": {
                        "id": "dlp_a",
                        "title": "Fix the issue",
                        "files": [{"path": "backend/app.py", "proposed_content": "secret source bytes"}],
                    },
                    "evidence": [{"id": "evd_snapshot", "path": "backend/app.py"}],
                    "createdAt": now,
                }
            ]
        )
        self.run_traces = _Collection(
            [
                {
                    "id": "trc_a",
                    "user_id": "user-a",
                    "session_id": "ses_a",
                    "run_id": "run-a",
                    "status": "completed",
                    "model_usage": {"total_tokens": 321, "model": "test-model"},
                    "metadata": {"internal_note": "never render this", "token": "secret-value"},
                    "started_at": now,
                }
            ]
            if with_trace
            else []
        )
        self.trace_spans = _Collection(
            [
                {
                    "id": "spn_a",
                    "trace_id": "trc_a",
                    "name": "agent.run",
                    "started_at": now,
                    "status": "ok",
                    "safe_output_summary": {"evidence_count": 1},
                    "metadata": {"internal_note": "never render this"},
                }
            ]
            if with_trace
            else []
        )
        self.evidence = _Collection(
            [
                {
                    "id": "evd_a",
                    "userId": "user-a",
                    "sessionId": "ses_a",
                    "runId": "run-a",
                    "path": "backend/app.py",
                    "sourceType": "repo_file",
                    "retrievalScore": 0.9,
                    "retrievedAt": now,
                }
            ]
        )
        self.delivery_plans = _Collection(
            [
                {
                    "id": "dlp_a",
                    "user_id": "user-a",
                    "session_id": "ses_a",
                    "run_id": "run-a",
                    "repository_id": "https://github.com/acme/private-repository.git",
                    "base_sha": "private-base-sha",
                    "title": "Fix the issue",
                    "validation_steps": [
                        {
                            "id": "test",
                            "kind": "test",
                            "description": "Run the safe test profile",
                            "command": "internal-command-argument-that-must-not-render",
                        }
                    ],
                    "files": [
                        {
                            "path": "backend/app.py",
                            "unified_diff": "@@ -1 +1 @@",
                            "proposed_content": "must never be returned by replay",
                            "proposedContent": "legacy source bytes must never be returned either",
                        }
                    ],
                }
            ]
        )
        self.approvals = _Collection(
            [
                {
                    "id": "apr_a",
                    "user_id": "user-a",
                    "session_id": "ses_a",
                    "run_id": "run-a",
                    "status": "approved",
                    "canonical_arguments": {"commit_message": "private execution argument"},
                    "approval_hash": "private-approval-hash",
                    "created_at": now,
                }
            ]
        )
        self.delivery_results = _Collection(
            [
                {
                    "id": "dlr_a",
                    "user_id": "user-a",
                    "repository_id": "private-repository-id",
                    "delivery_plan_id": "dlp_a",
                    "status": "success",
                    "validation_results": [
                        {"step_id": "lint", "status": "passed", "summary": "Ruff completed", "duration_ms": 18}
                    ],
                }
            ]
        )
        self.change_impact_analyses = _Collection(
            [
                {
                    "id": "impact_a",
                    "userId": "user-a",
                    "sessionId": "ses_a",
                    "deliveryPlanId": "dlp_a",
                    "status": "available",
                    "planBaseSha": "private-base-sha",
                    "graphCommitSha": "private-base-sha",
                    "changedPaths": ["backend/app.py"],
                    "directlyAffected": [
                        {
                            "node_id": "internal-node-id-must-not-render",
                            "node_type": "test",
                            "name": "test_health",
                            "path": "tests/test_health.py",
                            "distance": 1,
                            "relationship_path": [
                                {
                                    "source_node_id": "private-source-node",
                                    "target_node_id": "private-target-node",
                                    "relation_type": "imports",
                                }
                            ],
                            "explanation": "Potential structural dependent.",
                        }
                    ],
                    "indirectlyAffected": [],
                    "relatedTests": [],
                    "relatedWorkflows": [],
                    "limitations": [],
                    "createdAt": now,
                }
            ]
        )


def _replay_test_app(*, user_id: str) -> FastAPI:
    """Mount the real replay routers with only authentication substituted.

    The route handlers, response-model enforcement, and JSON serialization
    stay real. The fixture replaces the external Clerk dependency only.
    """

    app = FastAPI()
    app.include_router(dev.router, prefix="/api")
    app.include_router(session.router, prefix="/api/session")
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_current_user] = lambda: {"_id": user_id}
    return app


@pytest.mark.asyncio
async def test_run_replay_reconstructs_snapshot_trace_and_delivery_without_source_bytes(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")

    assert replay["historical"] is True
    assert replay["replay_mode"] == "read_only"
    assert replay["trace"]["id"] == "trc_a"
    assert replay["trace"]["model_usage"]["total_tokens"] == 321
    assert replay["spans"][0]["name"] == "agent.run"
    assert replay["investigation"]["diagnosis"].startswith("A persisted")
    assert replay["timeline"][0]["label"] == "Run started"
    assert replay["ci_investigation"]["run_id"] == 42
    assert replay["evidence"][0]["path"] == "backend/app.py"
    assert replay["delivery_result"]["status"] == "success"
    assert replay["delivery_result"]["validation_results"][0]["status"] == "passed"
    assert "proposed_content" not in replay["delivery_plan"]["files"][0]
    assert "proposedContent" not in replay["delivery_plan"]["files"][0]
    assert "user_id" not in replay["trace"]
    assert "metadata" not in replay["trace"]
    assert "trace_id" not in replay["spans"][0]
    assert "metadata" not in replay["spans"][0]
    assert "repository_id" not in replay["delivery_plan"]
    assert "base_sha" not in replay["delivery_plan"]
    assert "command" not in replay["delivery_plan"]["validation_steps"][0]
    assert "canonical_arguments" not in replay["approval"]
    assert "approval_hash" not in replay["approval"]
    assert "user_id" not in replay["delivery_result"]
    assert "repository_id" not in replay["delivery_result"]
    assert all(query["userId"] == "user-a" for query in db.evidence.queries)
    evidence_projection = db.evidence.projections[-1]
    assert evidence_projection is not None
    assert evidence_projection["metadata.truncated"] == 1
    assert "excerpt" not in evidence_projection
    assert "content" not in evidence_projection
    assert replay["change_impact"]["id"] == "impact_a"
    assert replay["change_impact"]["directly_affected"][0]["relations"] == ["imports"]
    assert "node_id" not in replay["change_impact"]["directly_affected"][0]
    assert "source_node_id" not in json.dumps(replay["change_impact"])


@pytest.mark.asyncio
async def test_trace_less_persisted_run_is_replayable_with_an_honest_limitation(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb(with_trace=False)
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")

    assert replay["trace"] is None
    assert replay["delivery_plan"]["id"] == "dlp_a"
    assert any("Trace telemetry was not recorded" in item for item in replay["limitations"])


@pytest.mark.asyncio
async def test_replay_degrades_a_legacy_trace_without_an_identifier(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    db.run_traces.values[0].pop("id")
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")

    assert replay["trace"]["id"] is None
    assert replay["spans"] == []
    assert any("has no identifier" in item for item in replay["limitations"])
    assert db.trace_spans.queries == []


@pytest.mark.asyncio
async def test_trace_only_run_replay_is_honest_about_missing_snapshot(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    db.investigations.values = []
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay_by_id("user-a", "run-a")

    assert replay["trace"]["id"] == "trc_a"
    assert replay["investigation"] is None
    assert any("investigation snapshot was not recorded" in item for item in replay["limitations"])


@pytest.mark.asyncio
async def test_replay_normalizes_partial_legacy_records_instead_of_breaking_the_inspector(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _ReplayDb()
    snapshot = db.investigations.values[0]
    snapshot["timeline"] = None
    snapshot["evidence"] = None
    snapshot["ci"]["limitations"] = "Historical log access was unavailable."
    db.run_traces.values[0]["model_usage"] = None
    db.delivery_plans.values[0]["files"] = None
    db.delivery_plans.values[0]["validation_steps"] = None
    db.delivery_results.values[0]["validation_results"] = None
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")

    assert replay["timeline"] == []
    assert replay["trace"]["model_usage"] == {}
    assert replay["delivery_plan"]["files"] == []
    assert replay["delivery_plan"]["validation_steps"] == []
    assert replay["delivery_result"]["validation_results"] == []
    assert "Historical log access was unavailable." in replay["limitations"]


@pytest.mark.asyncio
async def test_replay_degrades_malformed_legacy_field_types_without_breaking_the_inspector(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _ReplayDb()
    snapshot = db.investigations.values[0]
    snapshot["repository"] = {"headSha": 101, "branch": ["main"]}
    snapshot["investigation"] = {"summary": ["unexpected"], "diagnosis": 7, "status": {"bad": True}}
    snapshot["timeline"] = [{"label": 8, "detail": {"bad": True}, "status": [], "createdAt": 9}]
    snapshot["ci"] = {
        "run_id": "not-an-integer",
        "workflow_name": 9,
        "failed_jobs": "not-a-list",
        "limitations": [{"not": "a string"}],
    }
    snapshot["toolResult"] = "not-an-object"
    db.run_traces.values[0]["model_usage"] = ["not", "an", "object"]
    db.trace_spans.values[0]["safe_output_summary"] = ["not", "an", "object"]
    db.delivery_plans.values[0]["files"] = [{"path": 1, "unified_diff": {"bad": True}}]
    db.approvals.values[0]["status"] = {"unexpected": True}
    db.delivery_results.values[0]["pull_request_number"] = "not-an-integer"
    db.evidence.values[0]["retrievalScore"] = "not-a-score"
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")

    assert replay["repository"] == {"headSha": None, "head_sha": None, "branch": None}
    assert replay["investigation"] == {"summary": None, "diagnosis": None, "status": None}
    assert replay["timeline"] == [{"label": None, "detail": None, "status": None, "timestamp": None}]
    assert replay["ci_investigation"]["run_id"] is None
    assert replay["ci_investigation"]["failed_jobs"] == []
    assert replay["ci_investigation"]["limitations"] == []
    assert replay["tool_result"] is None
    assert replay["trace"]["model_usage"] == {}
    assert replay["spans"][0]["safe_output_summary"] == {}
    assert replay["delivery_plan"]["files"] == [
        {
            "path": None,
            "change_type": None,
            "original_hash": None,
            "proposed_hash": None,
            "unified_diff": None,
            "diff_truncated": False,
        }
    ]
    assert replay["approval"]["status"] is None
    assert replay["delivery_result"]["pull_request_number"] is None
    assert replay["evidence"][0]["score"] is None


@pytest.mark.asyncio
async def test_replay_redacts_legacy_secret_bearing_strings_across_every_browser_section(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _ReplayDb()
    snapshot = db.investigations.values[0]
    snapshot["investigation"]["diagnosis"] = "Observed OPENAI_API_KEY=sk-this-must-not-reach-the-browser"
    snapshot["timeline"][0]["detail"] = "Authorization: Bearer ghp_this-must-not-reach-the-browser"
    snapshot["ci"]["applicability_reason"] = "mongodb://user:db-password@db.example.invalid/base64"
    db.run_traces.values[0]["model_usage"]["provider_note"] = "password=trace-password"
    db.trace_spans.values[0]["safe_output_summary"] = {"legacy": "Bearer ghp_span-secret"}
    db.delivery_plans.values[0]["rationale"] = "OPENAI_API_KEY=sk-plan-secret"
    db.delivery_plans.values[0]["files"][0]["unified_diff"] = (
        "+ MONGO_URI=mongodb://user:diff-password@db.example.invalid/base64"
    )
    db.delivery_results.values[0]["validation_results"][0]["summary"] = "password=validation-secret"
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")
    browser_payload = json.dumps(replay)

    for secret in (
        "sk-this-must-not-reach-the-browser",
        "ghp_this-must-not-reach-the-browser",
        "db-password",
        "trace-password",
        "ghp_span-secret",
        "sk-plan-secret",
        "diff-password",
        "validation-secret",
    ):
        assert secret not in browser_payload
    assert "proposed_content" not in browser_payload


@pytest.mark.asyncio
async def test_developer_replay_resolves_a_snapshot_only_run_to_its_owned_session(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _ReplayDb(with_trace=False)
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay_by_id("user-a", "run-a")

    assert replay["run_id"] == "run-a"
    assert replay["session"] == {"slug_id": "repo-a", "repository": "Repository A"}
    assert replay["trace"] is None


@pytest.mark.asyncio
async def test_developer_replay_accepts_a_trace_identifier_without_losing_the_run_snapshot(
    monkeypatch: pytest.MonkeyPatch,
):
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay_by_id("user-a", "trc_a")

    assert replay["run_id"] == "run-a"
    assert replay["trace"]["id"] == "trc_a"
    assert replay["investigation"]["diagnosis"].startswith("A persisted")


@pytest.mark.asyncio
async def test_run_replay_denies_cross_tenant_access_without_querying_run_records(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    with pytest.raises(HTTPException) as error:
        await session_service.get_run_replay("user-b", "repo-a", "run-a")

    assert error.value.status_code == 404
    assert db.run_traces.queries == []
    assert db.evidence.queries == []


@pytest.mark.asyncio
async def test_developer_replay_does_not_disclose_another_tenants_run(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    with pytest.raises(HTTPException) as error:
        await session_service.get_run_replay_by_id("user-b", "run-a")

    assert error.value.status_code == 404
    assert db.sessions.queries == []


def test_replay_serialization_normalizes_nested_timestamps() -> None:
    value = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)

    serialized = serialize_doc({"started_at": value, "metadata": {"observed_at": value}})

    assert serialized == {
        "started_at": "2026-01-01T12:30:00+00:00",
        "metadata": {"observed_at": "2026-01-01T12:30:00+00:00"},
    }


def test_replay_response_contract_drops_unexpected_persisted_fields() -> None:
    response = RunReplayResponse.model_validate(
        {
            "run_id": "run-safe",
            "historical": True,
            "replay_mode": "read_only",
            "session": {"slug_id": "repo-a", "repository": "Repository A", "user_id": "must-not-leak"},
            "trace": {"id": "trc-safe", "status": "completed", "metadata": {"private": True}},
            "approval": {"id": "apr-safe", "status": "approved", "canonical_arguments": {"secret": "no"}},
            "delivery_plan": {
                "id": "dlp-safe",
                "files": [{"path": "app.py", "unified_diff": "@@", "proposed_content": "no"}],
            },
            "evidence": [{"id": "evd-safe", "path": "app.py", "excerpt": "no source body"}],
            "change_impact": {
                "id": "impact-safe",
                "status": "available",
                "changed_paths": ["app.py"],
                "directly_affected": [
                    {"path": "tests/test_app.py", "node_id": "private", "relations": ["imports"]}
                ],
            },
        }
    ).model_dump(exclude_none=True)

    assert response["session"] == {"slug_id": "repo-a", "repository": "Repository A"}
    assert "metadata" not in response["trace"]
    assert "canonical_arguments" not in response["approval"]
    assert "proposed_content" not in response["delivery_plan"]["files"][0]
    assert "excerpt" not in response["evidence"][0]
    assert response["change_impact"]["directly_affected"] == [
        {"path": "tests/test_app.py", "relations": ["imports"]}
    ]


def test_replay_contract_refuses_mutable_or_non_historical_modes() -> None:
    payload = {
        "run_id": "run-safe",
        "historical": True,
        "replay_mode": "read_only",
        "session": {},
    }
    assert RunReplayResponse.model_validate(payload).replay_mode == "read_only"

    with pytest.raises(ValueError):
        RunReplayResponse.model_validate({**payload, "historical": False})
    with pytest.raises(ValueError):
        RunReplayResponse.model_validate({**payload, "replay_mode": "execute"})


@pytest.mark.asyncio
async def test_replay_allows_only_safe_model_usage_telemetry(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    db.run_traces.values[0]["model_usage"] = {
        "model": "test-model",
        "total_tokens": 321,
        "context_truncated": False,
        "provider_payload": {"Authorization": "Bearer should-not-render"},
        "prompt": "private prompt must never be replayed",
        "cost": float("inf"),
    }
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")

    assert replay["trace"]["model_usage"] == {
        "model": "test-model",
        "total_tokens": 321,
        "context_truncated": False,
    }


@pytest.mark.asyncio
async def test_replay_marks_oversized_legacy_diffs_as_a_bounded_preview(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    original_diff = "+" + ("âœ“" * 150_000)
    db.delivery_plans.values[0]["files"][0]["unified_diff"] = original_diff
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    replay = await session_service.get_run_replay("user-a", "repo-a", "run-a")
    replayed_change = replay["delivery_plan"]["files"][0]

    assert replayed_change["diff_truncated"] is True
    expected_preview = original_diff.encode("utf-8")[: session_service._MAX_REPLAY_DIFF_BYTES]
    assert replayed_change["unified_diff"].encode("utf-8") == expected_preview


def test_replay_routes_serve_the_strict_read_only_contract_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)
    app = _replay_test_app(user_id="user-a")

    with TestClient(app) as client:
        by_run = client.get("/api/dev/runs/run-a")
        by_session = client.get("/api/session/repo-a/runs/run-a")
        history = client.get("/api/session/repo-a/runs")

    assert by_run.status_code == 200
    assert by_session.status_code == 200
    assert history.status_code == 200
    assert by_run.json()["historical"] is True
    assert by_run.json()["replay_mode"] == "read_only"
    assert by_run.json()["session"] == {"slug_id": "repo-a", "repository": "Repository A"}
    assert history.json()["runs"][0]["run_id"] == "run-a"


def test_replay_routes_do_not_disclose_runs_across_tenants(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)
    app = _replay_test_app(user_id="user-b")

    with TestClient(app) as client:
        by_run = client.get("/api/dev/runs/run-a")
        by_session = client.get("/api/session/repo-a/runs/run-a")

    assert by_run.status_code == 404
    assert by_session.status_code == 404
    assert db.sessions.queries
    assert db.evidence.queries == []


def test_replay_routes_enforce_the_shared_browser_safe_contract() -> None:
    dev_route = next(route for route in dev.router.routes if route.path == "/dev/runs/{run_id}")
    session_route = next(route for route in session.router.routes if route.path == "/{slug_id}/runs/{run_id}")

    assert dev_route.response_model is RunReplayResponse
    assert session_route.response_model is RunReplayResponse
    assert dev_route.response_model_exclude_none is True
    assert session_route.response_model_exclude_none is True


def test_history_route_enforces_the_shared_browser_safe_contract() -> None:
    history_route = next(route for route in session.router.routes if route.path == "/{slug_id}/runs")

    assert history_route.response_model is SessionRunHistoryResponse
    assert history_route.response_model_exclude_none is True


@pytest.mark.asyncio
async def test_session_run_history_combines_snapshot_and_trace_metadata(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    response = await session_service.list_session_runs("user-a", "repo-a")

    assert response["limit"] == 50
    assert response["runs"] == [
        {
            "run_id": "run-a",
            "trace_id": "trc_a",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "success",
            "title": "Fix the issue",
            "kind": "ci",
            "evidence_count": 1,
            "has_trace": True,
            "ci_run_id": 42,
            "delivery_status": "success",
                "has_change_impact": True,
        }
    ]
    history_projection = db.investigations.projections[-1]
    assert history_projection is not None
    assert history_projection["deliveryPlan.title"] == 1
    assert history_projection["evidence.id"] == 1
    assert "deliveryPlan" not in history_projection
    assert "evidence" not in history_projection


@pytest.mark.asyncio
async def test_session_run_history_joins_each_visible_snapshot_to_its_trace_even_when_not_recent(
    monkeypatch: pytest.MonkeyPatch,
):
    """A recent but unrelated trace must not displace a selected run's telemetry."""

    db = _ReplayDb()
    db.run_traces.values.insert(
        0,
        {
            "id": "trc_unrelated",
            "user_id": "user-a",
            "session_id": "ses_a",
            "run_id": "run-unrelated",
            "status": "completed",
            "started_at": datetime(2026, 2, 1, tzinfo=UTC),
        },
    )
    # The snapshot may be written after delayed trace ingestion. It is still
    # the newest replayable investigation even when its trace is not in the
    # limited recent-trace feed above.
    db.investigations.values[0]["createdAt"] = datetime(2026, 3, 1, tzinfo=UTC)
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    response = await session_service.list_session_runs("user-a", "repo-a", limit=1)

    assert response["runs"] == [
        {
            "run_id": "run-a",
            "trace_id": "trc_a",
            "created_at": "2026-03-01T00:00:00+00:00",
            "status": "success",
            "title": "Fix the issue",
            "kind": "ci",
            "evidence_count": 1,
            "has_trace": True,
            "ci_run_id": 42,
            "delivery_status": "success",
                "has_change_impact": True,
        }
    ]
    assert {
        "user_id": "user-a",
        "session_id": "ses_a",
        "run_id": {"$in": ["run-a"]},
    } in db.run_traces.queries


@pytest.mark.asyncio
async def test_session_run_history_redacts_and_normalizes_legacy_metadata(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    snapshot = db.investigations.values[0]
    snapshot["deliveryPlan"]["title"] = "OPENAI_API_KEY=sk-history-secret"
    snapshot["ci"] = ["malformed legacy CI context"]
    snapshot["evidence"] = "malformed legacy evidence list"
    db.run_traces.values[0]["status"] = {"malformed": True}
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    response = await session_service.list_session_runs("user-a", "repo-a")
    browser_payload = json.dumps(response)

    assert "sk-history-secret" not in browser_payload
    assert response["runs"] == [
        {
            "run_id": "run-a",
            "trace_id": "trc_a",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "success",
            "title": "OPENAI_API_KEY=[REDACTED]",
            "kind": "delivery",
            "evidence_count": 0,
            "has_trace": True,
            "ci_run_id": None,
            "delivery_status": "success",
                "has_change_impact": True,
        }
    ]


@pytest.mark.asyncio
async def test_session_run_history_is_tenant_scoped(monkeypatch: pytest.MonkeyPatch):
    db = _ReplayDb()
    monkeypatch.setattr(session_service, "get_db", lambda: db)

    with pytest.raises(HTTPException) as error:
        await session_service.list_session_runs("user-b", "repo-a")

    assert error.value.status_code == 404
    assert db.investigations.queries == []
    assert db.run_traces.queries == []
