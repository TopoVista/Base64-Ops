from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.prevention_rule_service import PreventionRuleService


def _incident(*, incident_id: str, sha: str, path: str = ".github/workflows/ci.yml") -> dict:
    return {
        "_id": incident_id,
        "headSha": sha,
        "relevantPaths": [path],
        "failureCategories": ["configuration_failure"],
        "evidenceStrength": "supported",
    }


def test_rule_candidates_require_two_independent_supported_run_shas() -> None:
    service = PreventionRuleService()
    assert not service.candidates([_incident(incident_id="inc_1", sha="a" * 40)])
    assert not service.candidates(
        [
            _incident(incident_id="inc_1", sha="a" * 40),
            _incident(incident_id="inc_2", sha="a" * 40),
        ]
    )

    candidates = service.candidates(
        [
            _incident(incident_id="inc_1", sha="a" * 40),
            _incident(incident_id="inc_2", sha="b" * 40),
            {**_incident(incident_id="weak", sha="c" * 40), "evidenceStrength": "correlated"},
        ]
    )
    assert len(candidates) == 1
    assert candidates[0]["affectedPath"] == ".github/workflows/ci.yml"
    assert candidates[0]["occurrenceCount"] == 2
    assert candidates[0]["historicalIncidentIds"] == ["inc_1", "inc_2"]


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


class _Rules:
    def __init__(self) -> None:
        self.writes: list[tuple[dict, dict, bool]] = []

    async def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        self.writes.append((query, update, upsert))


@pytest.mark.asyncio
async def test_rule_refresh_is_scoped_and_persists_only_advisory_metadata(monkeypatch) -> None:
    from app.services import prevention_rule_service as module

    incidents = _Incidents(
        [_incident(incident_id="inc_1", sha="a" * 40), _incident(incident_id="inc_2", sha="b" * 40)]
    )
    rules = _Rules()
    monkeypatch.setattr(module, "get_db", lambda: SimpleNamespace(incidents=incidents, prevention_rules=rules))

    ids = await PreventionRuleService().refresh_for_repository(
        user_id="user_a", session_id="session_a", repository_id="repo_123"
    )

    assert len(ids) == 1
    assert incidents.query == {
        "userId": "user_a",
        "sessionId": "session_a",
        "repositoryId": "repo_123",
        "isGroupRoot": {"$ne": False},
        "evidenceStrength": "supported",
    }
    query, update, upsert = rules.writes[0]
    assert query["userId"] == "user_a"
    assert query["repositoryId"] == "repo_123"
    assert upsert is True
    stored = update["$set"]
    assert stored["status"] == "active"
    assert stored["threshold"] == {"minimumSupportedRuns": 2, "distinctFailedRunShas": True}
    assert "command" not in str(stored).lower()
