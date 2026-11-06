from types import SimpleNamespace

import pytest

from app.services import incident_service
from app.services.incident_service import IncidentDispatchService


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"indexedRevision": "a" * 40, "snapshotStatus": "ready"}, "ready"),
        ({"indexedRevision": "b" * 40, "snapshotStatus": "ready"}, "failed"),
        ({"indexedRevision": "a" * 40, "snapshotStatus": "empty"}, "failed"),
        ({}, "failed"),
    ],
)
async def test_completed_revision_job_requires_an_exact_ready_snapshot(monkeypatch, result, expected) -> None:
    class Jobs:
        async def find_one(self, _query, _projection):  # noqa: ANN001, ANN201
            return {"status": "completed", "result": result}

    monkeypatch.setattr(incident_service, "get_db", lambda: SimpleNamespace(background_jobs=Jobs()))
    state = await IncidentDispatchService._revision_reindex_state(
        event={
            "headSha": "a" * 40,
            "revisionReindexJobs": [{"automationId": "aut_1", "jobId": "job_1"}],
        },
        automation_id="aut_1",
    )

    assert state == expected
