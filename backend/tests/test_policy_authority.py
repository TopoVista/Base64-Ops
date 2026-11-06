from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services import policy_authority_service
from app.services.policy_authority_service import PolicyAuthorityService


def _plan(*, ledger_manifest_hash: str | None = "a" * 64, risk_level: str = "high") -> DeliveryPlan:
    return DeliveryPlan(
        id="dpl_1",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="https://github.com/acme/repo.git",
        base_branch="main",
        base_sha="b" * 40,
        title="Fix workflow directory",
        rationale="Evidence supports an exact workflow edit.",
        evidence_ids=["evd_1"],
        files=[
            ProposedFileChange(
                path=".github/workflows/ci.yml",
                change_type="modify",
                original_hash="c" * 64,
                proposed_hash="d" * 64,
                unified_diff="-working-directory: ./server\n+working-directory: ./client\n",
            )
        ],
        validation_steps=[],
        risk_level=risk_level,
        ledger_manifest_hash=ledger_manifest_hash,
        created_at=datetime.now(UTC),
    )


def test_policy_action_requires_signed_manifest() -> None:
    with pytest.raises(HTTPException, match="signed evidence manifest"):
        PolicyAuthorityService().canonical_action(
            plan=_plan(ledger_manifest_hash=None),
            action_type="create_draft_pull_request",
            arguments={"commit_message": "Fix workflow directory"},
        )


def test_candidate_dispatch_does_not_require_itself_to_have_already_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        policy_authority_service,
        "get_settings",
        lambda: Settings(policy_require_candidate_verification_for_high_risk=True),
    )

    action = PolicyAuthorityService().canonical_action(
        plan=_plan(risk_level="high"),
        action_type="dispatch_candidate_verification",
        arguments={"candidate_branch": "base64-verify/candidate-abcdef"},
    )

    assert action["requirements"]["candidateVerification"] is False


def test_strict_policy_requires_distinct_two_person_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        policy_authority_service,
        "get_settings",
        lambda: Settings(policy_strict_separation_of_duties=True),
    )

    action = PolicyAuthorityService().canonical_action(
        plan=_plan(),
        action_type="create_draft_pull_request",
        arguments={"commit_message": "Fix workflow directory"},
    )

    assert action["requirements"] == {
        "approvalQuorum": 2,
        "distinctApprovers": True,
        "candidateVerification": False,
    }


async def test_unimplemented_reviewer_quorum_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        policy_authority_service,
        "get_settings",
        lambda: Settings(policy_strict_separation_of_duties=True),
    )
    approval = {
        "id": "apr_1",
        "user_id": "usr_1",
        "action_type": "create_draft_pull_request",
        "canonical_arguments": {"commit_message": "Fix workflow directory"},
        "ledger_manifest_hash": "a" * 64,
    }
    with pytest.raises(HTTPException, match="reviewer quorum"):
        await PolicyAuthorityService().authorize_approval(approval=approval, plan=_plan(), approver_id="usr_2")


async def test_executor_rejects_missing_or_changed_policy_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    class Decisions:
        async def find_one(self, _query):  # noqa: ANN001, ANN201
            return None

    monkeypatch.setattr(
        policy_authority_service,
        "get_db",
        lambda: SimpleNamespace(policy_decisions=Decisions()),
    )
    approval = {
        "id": "apr_1",
        "action_type": "create_draft_pull_request",
        "canonical_arguments": {"commit_message": "Fix workflow directory"},
    }
    with pytest.raises(HTTPException, match="No policy authorization"):
        await PolicyAuthorityService().assert_execution_authorized(approval=approval, plan=_plan())

    approval["policyDecisionId"] = "pdc_1"
    with pytest.raises(HTTPException, match="unavailable or revoked"):
        await PolicyAuthorityService().assert_execution_authorized(approval=approval, plan=_plan())
