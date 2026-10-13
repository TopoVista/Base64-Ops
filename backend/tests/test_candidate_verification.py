from datetime import UTC, datetime

from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services.candidate_verification_service import initial_candidate_verification


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
    record = initial_candidate_verification(plan)
    assert record["status"] == "proposed"
    assert record["verificationLevel"] == "not_independently_tested"
    assert record["baseSha"] == plan.base_sha
    assert record["candidatePatchHash"]
    assert record["commands"] == []
