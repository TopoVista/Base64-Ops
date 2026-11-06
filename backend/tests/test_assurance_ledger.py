from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.services import assurance_ledger_service
from app.services.assurance_ledger_service import AssuranceLedgerService


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def sort(self, field: str, direction: int) -> _Cursor:
        self.rows.sort(key=lambda item: item.get(field), reverse=direction < 0)
        return self

    def limit(self, value: int) -> _Cursor:
        self.rows = self.rows[:value]
        return self

    async def to_list(self, value: int) -> list[dict[str, Any]]:
        return list(self.rows[:value])


class _Evidence:
    def __init__(self) -> None:
        self.rows = [
            {
                "id": "evd_1",
                "userId": "usr_1",
                "sessionId": "ses_1",
                "runId": "run_1",
                "repositoryId": "https://github.com/acme/repo.git",
                "contentHash": "a" * 64,
                "sourceType": "ci_log",
                "commitSha": "b" * 40,
                "path": ".github/workflows/ci.yml",
            }
        ]

    def find(self, query: dict[str, Any], _projection: dict[str, int]) -> _Cursor:
        evidence_ids = set(query["id"]["$in"])
        return _Cursor(
            [
                item
                for item in self.rows
                if item["id"] in evidence_ids
                and all(item.get(key) == value for key, value in query.items() if key != "id")
            ]
        )


class _Receipts:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any], _projection: dict[str, int]) -> _Cursor:
        return _Cursor([item for item in self.rows if all(item.get(key) == value for key, value in query.items())])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next((item for item in self.rows if all(item.get(key) == value for key, value in query.items())), None)

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.rows.append(document)


def _plan() -> DeliveryPlan:
    return DeliveryPlan(
        id="dpl_1",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="https://github.com/acme/repo.git",
        base_branch="main",
        base_sha="b" * 40,
        title="Fix CI path",
        rationale="Evidence identifies the wrong working directory.",
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
        risk_level="high",
        risk_reasons=["CI/CD configuration modified"],
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def ledger_db(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    database = SimpleNamespace(evidence=_Evidence(), assurance_ledger_receipts=_Receipts())
    monkeypatch.setattr(assurance_ledger_service, "get_db", lambda: database)
    return database


async def test_plan_receipt_is_publicly_verifiable_and_binds_evidence(ledger_db: SimpleNamespace) -> None:
    receipt = await AssuranceLedgerService().record_plan_receipt(_plan())

    assert AssuranceLedgerService.verify_receipt(receipt, expected_user_id="usr_1")
    assert receipt["manifest"]["evidence"] == [
        {
            "id": "evd_1",
            "contentHash": "a" * 64,
            "sourceType": "ci_log",
            "commitSha": "b" * 40,
            "path": ".github/workflows/ci.yml",
        }
    ]
    assert receipt["manifest"]["patch"]["files"][0]["proposedHash"] == "d" * 64


async def test_tampering_or_cross_tenant_reuse_invalidates_receipt(ledger_db: SimpleNamespace) -> None:
    receipt = await AssuranceLedgerService().record_plan_receipt(_plan())
    receipt["manifest"]["patch"]["files"][0]["proposedHash"] = "e" * 64

    assert not AssuranceLedgerService.verify_receipt(receipt, expected_user_id="usr_1")
    other_tenant = {**receipt, "manifest": {**receipt["manifest"], "userId": "usr_2"}}
    assert not AssuranceLedgerService.verify_receipt(other_tenant, expected_user_id="usr_1")


async def test_receipts_chain_within_one_investigation(ledger_db: SimpleNamespace) -> None:
    service = AssuranceLedgerService()
    first = await service.record_plan_receipt(_plan())
    second = await service.record_approval_receipt(
        {
            "id": "apr_1",
            "user_id": "usr_1",
            "session_id": "ses_1",
            "run_id": "run_1",
            "repository_id": "https://github.com/acme/repo.git",
            "delivery_plan_id": "dpl_1",
            "approval_hash": "f" * 64,
            "ledger_manifest_hash": first["manifestHash"],
            "action_type": "create_draft_pull_request",
            "canonical_arguments": {"commit_message": "Fix CI path"},
            "base_branch": "main",
            "base_sha": "b" * 40,
            "diff_hash": "c" * 64,
        }
    )

    assert second["previousReceiptHash"] == first["receiptHash"]
    assert AssuranceLedgerService.verify_receipt(second, expected_user_id="usr_1")
    verified = await service.get_verified_receipt(receipt_id=second["id"], user_id="usr_1", session_id="ses_1")
    assert verified and verified["chainVerified"]

    # A signature on the newest record cannot hide a missing predecessor.
    ledger_db.assurance_ledger_receipts.rows.remove(first)
    broken = await service.get_verified_receipt(receipt_id=second["id"], user_id="usr_1", session_id="ses_1")
    assert broken and broken["verified"] and not broken["chainVerified"]


async def test_incident_capsule_binds_exact_revision_and_evidence_hashes(ledger_db: SimpleNamespace) -> None:
    receipt = await AssuranceLedgerService().record_incident_capsule(
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="https://github.com/acme/repo.git",
        context={
            "run_id": 42,
            "head_sha": "b" * 40,
            "current_head_sha": "c" * 40,
            "workflow_name": "CI",
            "workflow_path": ".github/workflows/ci.yml",
            "evidence_ids": ["evd_1"],
            "failure_categories": ["test_failure"],
            "relevant_paths": ["backend/main.py"],
            "capsule_inputs": {
                "reproducibility": "partial",
                "inputs": [{"path": "poetry.lock", "contentHash": "c" * 64, "bytes": 12}],
                "runnerLabels": ["ubuntu-latest"],
                "limitations": ["Runner image digest unavailable."],
            },
        },
    )

    capsule = receipt["manifest"]["capsule"]
    assert receipt["kind"] == "incident_capsule"
    assert capsule["reproducibility"] == "partial"
    assert capsule["failedRevision"] == "b" * 40
    assert capsule["evidence"][0]["contentHash"] == "a" * 64
    assert capsule["reproductionInputs"]["runnerLabels"] == ["ubuntu-latest"]
    assert AssuranceLedgerService.verify_receipt(receipt, expected_user_id="usr_1")


async def test_prevention_policy_receipt_has_a_dedicated_signed_rule_chain(ledger_db: SimpleNamespace) -> None:
    receipt = await AssuranceLedgerService().record_prevention_policy_receipt(
        {
            "userId": "usr_1",
            "sessionId": "ses_1",
            "runId": "prevention-policy:prv_1",
            "repositoryId": "https://github.com/acme/repo.git",
            "ruleId": "prv_1",
            "action": "approve",
            "policyVersion": 1,
            "deploymentScope": "advisory_only",
            "supportingIncidentIds": ["inc_1", "inc_2"],
            "supportingRunShas": ["a" * 40, "b" * 40],
            "limitations": ["Historical correlation is not proof of a future failure."],
        }
    )

    assert receipt["kind"] == "prevention_policy"
    assert receipt["runId"] == "prevention-policy:prv_1"
    assert receipt["manifest"]["action"] == "approve"
    assert receipt["manifest"]["supportingIncidentIds"] == ["inc_1", "inc_2"]
    assert AssuranceLedgerService.verify_receipt(receipt, expected_user_id="usr_1")
