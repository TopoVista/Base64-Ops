from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.routes import incidents
from app.services.flaky_test_service import (
    FlakyTestAssessment,
    FlakyTestObservationService,
    build_flaky_test_observations,
)


def _context(*, failures: int, successes: int, identifiers: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_path=".github/workflows/ci.yml",
        head_sha="a" * 40,
        failed_test_identifiers=identifiers,
        evidence_ids=["evd_log"],
        history_evidence_ids=["evd_history"],
        history=SimpleNamespace(
            same_sha_failure_count=failures,
            same_sha_success_count=successes,
        ),
    )


def test_mixed_same_sha_outcomes_produce_a_limited_flaky_observation() -> None:
    observations = build_flaky_test_observations(
        _context(
            failures=2,
            successes=1,
            identifiers=["tests/test_login.py::test_login"],
        )
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.assessment is FlakyTestAssessment.SUSPECTED_FLAKINESS
    assert observation.evidence_ids == ["evd_log", "evd_history"]
    assert any("workflow-level" in limitation for limitation in observation.limitations)


def test_repeated_failures_and_small_samples_are_distinguished() -> None:
    stable = build_flaky_test_observations(
        _context(failures=2, successes=0, identifiers=["tests/test_api.py::test_create"])
    )
    insufficient = build_flaky_test_observations(
        _context(failures=1, successes=0, identifiers=["tests/test_api.py::test_create"])
    )

    assert stable[0].assessment is FlakyTestAssessment.REPRODUCIBLE_REGRESSION
    assert insufficient[0].assessment is FlakyTestAssessment.INSUFFICIENT_EVIDENCE
    assert build_flaky_test_observations(_context(failures=4, successes=3, identifiers=[])) == []


@pytest.mark.asyncio
async def test_flaky_observation_list_is_tenant_scoped(monkeypatch) -> None:  # noqa: ANN001
    class Cursor:
        def __init__(self, items):  # noqa: ANN001
            self.items = items

        def sort(self, *_args):  # noqa: ANN001, ANN201
            return self

        def limit(self, _limit):  # noqa: ANN001, ANN201
            return self

        def __aiter__(self):  # noqa: ANN201
            async def values():  # noqa: ANN202
                for item in self.items:
                    yield item

            return values()

    class Observations:
        query: dict | None = None

        def find(self, query):  # noqa: ANN001, ANN201
            self.query = query
            return Cursor(
                [
                    {
                        "_id": "flt_1",
                        "repositoryId": "repo_a",
                        "workflowKey": ".github/workflows/ci.yml",
                        "testIdentifier": "tests/test_login.py::test_login",
                        "headSha": "a" * 40,
                        "assessment": "suspected_flakiness",
                        "sameShaFailureCount": 2,
                        "sameShaSuccessCount": 1,
                        "limitations": ["workflow-level only"],
                        "updatedAt": datetime(2026, 1, 1, tzinfo=UTC),
                    }
                ]
            )

    observations = Observations()
    monkeypatch.setattr(incidents, "get_db", lambda: SimpleNamespace(flaky_test_observations=observations))

    result = await incidents.list_flaky_test_observations(limit=10, user={"_id": "user_a"})

    assert observations.query == {"userId": "user_a"}
    assert result[0].id == "flt_1"
    assert result[0].test_identifier == "tests/test_login.py::test_login"


@pytest.mark.asyncio
async def test_observation_persistence_keeps_only_bounded_safe_fields(monkeypatch) -> None:  # noqa: ANN001
    class Observations:
        updates: list[tuple[dict, dict, bool]] = []

        async def update_one(self, query, update, upsert=False):  # noqa: ANN001, ANN201
            self.updates.append((query, update, upsert))

    observations = Observations()
    monkeypatch.setattr(
        "app.services.flaky_test_service.get_db",
        lambda: SimpleNamespace(flaky_test_observations=observations),
    )

    stored = await FlakyTestObservationService().record(
        user_id="user_a",
        session_id="session_a",
        repository_id="repo_a",
        incident_id="inc_a",
        context=_context(
            failures=2,
            successes=1,
            identifiers=["tests/test_login.py::test_login"],
        ),
    )

    query, update, upsert = observations.updates[0]
    assert upsert is True
    assert query["userId"] == "user_a"
    assert update["$set"]["testIdentifier"] == "tests/test_login.py::test_login"
    assert "excerpt" not in str(update)
    assert "Authorization" not in str(update)
    assert stored[0]["assessment"] == "suspected_flakiness"
