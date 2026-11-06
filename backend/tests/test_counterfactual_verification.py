from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services import counterfactual_verification_service as module
from app.services.counterfactual_verification_service import CounterfactualVerificationService


def _plan() -> DeliveryPlan:
    return DeliveryPlan(
        id="dpl_counterfactual",
        user_id="user_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="https://github.com/acme/service.git",
        base_branch="main",
        base_sha="a" * 40,
        title="Repair failing test",
        rationale="Exact incident evidence supports the patch.",
        evidence_ids=["evd_1"],
        files=[
            ProposedFileChange(
                path="backend/service.py",
                change_type="modify",
                original_hash="b" * 64,
                proposed_hash="c" * 64,
                unified_diff="--- a/backend/service.py\n+++ b/backend/service.py\n@@\n-old\n+new\n",
                proposed_content="new\n",
            )
        ],
        validation_steps=[],
        risk_level="medium",
        ledger_manifest_hash="d" * 64,
        created_at=datetime.now(UTC),
    )


class _Cursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def sort(self, *_args):  # noqa: ANN002, ANN201
        return self

    def limit(self, _value):  # noqa: ANN001, ANN201
        return self

    async def to_list(self, _value):  # noqa: ANN001, ANN201
        return [dict(row) for row in self.rows]


@pytest.mark.asyncio
async def test_counterfactual_matrix_binds_exact_capsule_and_candidate_without_claiming_proof(monkeypatch) -> None:
    plan = _plan()
    target = {
        "_id": "inc_target",
        "repositoryUrl": plan.repository_id,
        "headSha": plan.base_sha,
        "capsuleManifestHash": "e" * 64,
    }
    historical = {
        "_id": "inc_history",
        "repositoryUrl": plan.repository_id,
        "headSha": "f" * 40,
        "capsuleManifestHash": "f" * 64,
        "relevantPaths": ["backend/service.py"],
    }

    class Incidents:
        async def find_one(self, query):  # noqa: ANN001, ANN201
            assert query["headSha"] == plan.base_sha
            assert query["$or"] == [{"repositoryId": plan.repository_id}, {"repositoryUrl": plan.repository_id}]
            return target

        def find(self, query, _projection):  # noqa: ANN001, ANN201
            assert query["headSha"] == {"$ne": plan.base_sha}
            assert query["relevantPaths"] == {"$in": ["backend/service.py"]}
            return _Cursor([historical])

    class VerificationRuns:
        async def find_one(self, query, _projection):  # noqa: ANN001, ANN201
            assert query["baseSha"] == plan.base_sha
            assert query["status"] == "succeeded"
            return {
                "_id": "vrf_1",
                "id": "vrf_1",
                "verificationLevel": "targeted_verification_passed",
                "workflowPath": ".github/workflows/verify.yml",
                "githubRunId": 99,
            }

    class Reports:
        def __init__(self) -> None:
            self.inserted: dict | None = None

        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return None

        async def insert_one(self, document):  # noqa: ANN001, ANN201
            self.inserted = dict(document)

    reports = Reports()
    monkeypatch.setattr(
        module,
        "get_db",
        lambda: SimpleNamespace(
            incidents=Incidents(),
            verification_runs=VerificationRuns(),
            counterfactual_verifications=reports,
        ),
    )

    async def receipt(_self, report):  # noqa: ANN001, ANN202
        assert report["status"] == "targeted_evidence_available"
        return {"id": "lgr_counter", "manifestHash": "1" * 64}

    monkeypatch.setattr(module.AssuranceLedgerService, "record_counterfactual_receipt", receipt)

    report = await CounterfactualVerificationService().analyze_for_plan(
        user_id="user_1", session_id="ses_1", plan=plan
    )

    assert report["status"] == "targeted_evidence_available"
    assert report["matrix"][0] == {
        "incidentId": "inc_target",
        "capsuleManifestHash": "e" * 64,
        "baseSha": plan.base_sha,
        "baseline": "failure_observed",
        "patched": "targeted_verification_passed",
        "replayed": True,
        "scope": "exact_failed_revision_targeted_workflow",
    }
    assert report["matrix"][1]["patched"] == "not_replayed"
    assert report["matrix"][1]["scope"] == "historical_capsule_selected_not_replayed"
    assert all("proved" not in limitation.lower() for limitation in report["limitations"])
    assert reports.inserted and reports.inserted["ledgerReceiptId"] == "lgr_counter"


@pytest.mark.asyncio
async def test_counterfactual_is_inconclusive_without_exact_candidate_success(monkeypatch) -> None:
    plan = _plan()

    class Incidents:
        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return None

        def find(self, _query, _projection):  # noqa: ANN001, ANN201
            return _Cursor([])

    class VerificationRuns:
        async def find_one(self, _query, _projection):  # noqa: ANN001, ANN201
            return None

    class Reports:
        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return None

        async def insert_one(self, _document):  # noqa: ANN001, ANN201
            return None

    monkeypatch.setattr(
        module,
        "get_db",
        lambda: SimpleNamespace(
            incidents=Incidents(),
            verification_runs=VerificationRuns(),
            counterfactual_verifications=Reports(),
        ),
    )

    async def receipt(_self, _report):  # noqa: ANN001, ANN202
        return {"id": "lgr_counter", "manifestHash": "1" * 64}

    monkeypatch.setattr(module.AssuranceLedgerService, "record_counterfactual_receipt", receipt)
    report = await CounterfactualVerificationService().analyze_for_plan(
        user_id="user_1", session_id="ses_1", plan=plan
    )

    assert report["status"] == "inconclusive"
    assert report["matrix"] == []
    assert any("No signed incident capsule" in limitation for limitation in report["limitations"])
    assert any("No successful exact candidate verification" in limitation for limitation in report["limitations"])
