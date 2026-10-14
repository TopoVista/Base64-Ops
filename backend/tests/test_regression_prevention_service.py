from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services.regression_prevention_service import RegressionPreventionService


def _plan(*, sha: str = "c" * 40, path: str = "backend/requirements.txt") -> DeliveryPlan:
    return DeliveryPlan(
        id="dpl_prevention",
        user_id="user_a",
        session_id="session_a",
        run_id="run_a",
        repository_id="https://github.com/acme/fixture",
        base_branch="main",
        base_sha=sha,
        title="Update dependency declaration",
        rationale="Fixture only",
        files=[
            ProposedFileChange(
                path=path,
                change_type="modify",
                original_hash="a",
                proposed_hash="b",
                unified_diff="diff",
            )
        ],
        validation_steps=[],
        risk_level="medium",
        created_at=datetime.now(UTC),
    )


def _incident(
    *,
    incident_id: str,
    sha: str,
    path: str = "backend/requirements.txt",
    category: str = "dependency_failure",
) -> dict:
    return {
        "_id": incident_id,
        "headSha": sha,
        "relevantPaths": [path],
        "failureCategories": [category],
        "evidenceStrength": "supported",
    }


def test_prevention_requires_two_independent_supported_run_shas() -> None:
    service = RegressionPreventionService()
    only_one = service.analyze(plan=_plan(), incidents=[_incident(incident_id="inc_1", sha="a" * 40)])
    assert only_one.status == "insufficient_history"
    assert not only_one.advisories

    report = service.analyze(
        plan=_plan(),
        incidents=[
            _incident(incident_id="inc_1", sha="a" * 40),
            _incident(incident_id="inc_2", sha="b" * 40),
            _incident(incident_id="inc_3", sha="a" * 40),
        ],
    )
    assert report.status == "available"
    assert len(report.advisories) == 1
    advisory = report.advisories[0]
    assert advisory.path == "backend/requirements.txt"
    assert advisory.occurrence_count == 2
    assert set(advisory.historical_incident_ids) == {"inc_1", "inc_2", "inc_3"}
    assert "not proof" in advisory.message


def test_prevention_excludes_user_corrected_incidents_from_live_patterns() -> None:
    report = RegressionPreventionService().analyze(
        plan=_plan(path="backend/app/auth.py"),
        incidents=[
            {
                **_incident(
                    incident_id="inc_corrected",
                    sha="a" * 40,
                    path="backend/app/auth.py",
                    category="test_failure",
                ),
                "feedback": {"disposition": "incorrect", "untrusted": True},
            },
            _incident(incident_id="inc_other", sha="b" * 40, path="backend/app/auth.py", category="test_failure"),
        ],
    )

    assert report.status == "insufficient_history"
    assert not report.advisories
    assert "User-corrected incidents" in " ".join(report.limitations)


def test_prevention_requires_exact_path_overlap_and_excludes_base_sha() -> None:
    report = RegressionPreventionService().analyze(
        plan=_plan(),
        incidents=[
            _incident(incident_id="same", sha="c" * 40),
            _incident(incident_id="other-path", sha="a" * 40, path="backend/app/service.py"),
            _incident(incident_id="different-category", sha="b" * 40, category="test_failure"),
        ],
    )
    assert report.status == "insufficient_history"
    assert not report.advisories
    assert any("excluded" in limitation for limitation in report.limitations)


class _Cursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def sort(self, *_args):  # noqa: ANN002
        return self

    def limit(self, _value: int):
        return self

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row

        return iterate()


class _Incidents:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.query: dict | None = None

    def find(self, query: dict, _projection: dict) -> _Cursor:
        self.query = query
        return _Cursor(self.rows)


class _AutomationSettings:
    async def find_one(self, _query: dict, _projection: dict) -> dict:
        return {"repositoryId": "1234"}


class _Rules:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def find(self, _query: dict, _projection: dict) -> _Cursor:
        return _Cursor(self.rows)


@pytest.mark.asyncio
async def test_prevention_history_query_is_tenant_session_and_repository_scoped(monkeypatch) -> None:
    from app.services import regression_prevention_service as module

    collection = _Incidents(
        [
            _incident(incident_id="inc_1", sha="a" * 40),
            _incident(incident_id="inc_2", sha="b" * 40),
        ]
    )
    monkeypatch.setattr(
        module,
        "get_db",
        lambda: SimpleNamespace(incidents=collection, repository_automation_settings=_AutomationSettings()),
    )
    report = await RegressionPreventionService().analyze_for_plan(
        user_id="user_a", session_id="session_a", plan=_plan()
    )
    assert report.status == "available"
    assert collection.query == {
        "userId": "user_a",
        "sessionId": "session_a",
        "repositoryId": "1234",
        "isGroupRoot": {"$ne": False},
        "evidenceStrength": "supported",
    }


@pytest.mark.asyncio
async def test_prevention_report_labels_a_matching_persisted_rule(monkeypatch) -> None:
    from app.services import regression_prevention_service as module

    collection = _Incidents(
        [_incident(incident_id="inc_1", sha="a" * 40), _incident(incident_id="inc_2", sha="b" * 40)]
    )
    rules = _Rules(
        [
            {
                "_id": "prv_1",
                "affectedPath": "backend/requirements.txt",
                "failureCategory": "dependency_failure",
            }
        ]
    )
    monkeypatch.setattr(
        module,
        "get_db",
        lambda: SimpleNamespace(
            incidents=collection,
            prevention_rules=rules,
            repository_automation_settings=_AutomationSettings(),
        ),
    )
    report = await RegressionPreventionService().analyze_for_plan(
        user_id="user_a", session_id="session_a", plan=_plan()
    )
    assert report.advisories[0].prevention_rule_id == "prv_1"
    assert report.advisories[0].promotion_state == "persisted_rule"


class _Documents:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def find_one(self, query: dict) -> dict | None:
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None


@pytest.mark.asyncio
async def test_session_prevention_refuses_a_plan_from_another_repository(monkeypatch) -> None:
    from app.services import session_service

    plan_document = _plan().model_dump(mode="python")
    plan_document["userId"] = "user_a"
    plan_document["sessionId"] = "session_a"
    database = SimpleNamespace(
        sessions=_Documents(
            [
                {
                    "_id": "session_a",
                    "userId": "user_a",
                    "slugId": "fixture",
                    "repoUrl": "https://github.com/acme/other",
                }
            ]
        ),
        delivery_plans=_Documents([plan_document]),
    )
    monkeypatch.setattr(session_service, "get_db", lambda: database)

    with pytest.raises(HTTPException) as exc:
        await session_service.analyze_session_regression_prevention("user_a", "fixture", "dpl_prevention")

    assert exc.value.status_code == 404
