from datetime import UTC, datetime
from pathlib import Path

from app.policy import ToolRisk, get_policy, requires_approval
from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.schemas.patch import FileEditIntent, PatchProposal
from app.services.delivery_service import DeliveryService, canonical_json, content_hash
from app.services.patch_engine import PatchEngine, PatchSafetyError


def test_registered_tool_policy_is_deterministic() -> None:
    assert not requires_approval("git.status")
    assert not requires_approval("repo.read_file")
    assert not requires_approval("patch.generate")
    assert requires_approval("patch.apply")
    assert requires_approval("git.push")
    assert requires_approval("github.create_pull_request")
    assert get_policy("deploy").risk_level == ToolRisk.PRIVILEGED


def test_structured_edit_produces_exact_hashes_and_diff(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text('uvicorn.run(app, host="127.0.0.1")\n', encoding="utf-8")
    original = 'uvicorn.run(app, host="127.0.0.1")\n'
    intent = FileEditIntent(
        path="app.py",
        operation="modify",
        reason="Fix runtime binding",
        evidence_ids=["evd_a"],
        expected_original_hash=content_hash(original),
        proposed_content='uvicorn.run(app, host="0.0.0.0")\n',
    )
    proposal = PatchProposal(
        summary="Restore binding",
        rationale="Evidence shows the incorrect binding",
        evidence_ids=["evd_a"],
        edits=[intent],
    )
    plan, _surfaces = PatchEngine().build_delivery_plan(
        proposal=proposal,
        user_id="usr_a",
        session_id="ses_a",
        run_id="run_a",
        repository_id="repo_a",
        base_branch="main",
        base_sha="a" * 40,
        repo_path=tmp_path,
        evidence=[{"id": "evd_a", "path": "app.py"}],
        repo_map={},
    )
    assert plan.files[0].original_hash == content_hash('uvicorn.run(app, host="127.0.0.1")\n')
    assert '+uvicorn.run(app, host="0.0.0.0")' in plan.files[0].unified_diff
    assert plan.risk_level == "medium"
    assert all(result.status == "passed" for result in DeliveryService().validate_plan(plan))


def test_patch_engine_rejects_path_traversal_and_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    engine = PatchEngine()
    for path in ("../../secret.txt", ".env"):
        bad_edit = FileEditIntent(
            path=path,
            operation="create",
            reason="bad",
            evidence_ids=["evd"],
            proposed_content="x",
        )
        proposal = PatchProposal(summary="bad", rationale="bad", edits=[bad_edit])
        try:
            engine.build_delivery_plan(
                proposal=proposal,
                user_id="u",
                session_id="s",
                run_id="r",
                repository_id="repo",
                base_branch="main",
                base_sha="a",
                repo_path=tmp_path,
                evidence=[{"id": "evd", "path": "app.py"}],
                repo_map={},
            )
        except PatchSafetyError:
            continue
        raise AssertionError("unsafe path was accepted")


def test_approval_hash_is_stable_and_changes_with_diff() -> None:
    original = "host = '127.0.0.1'\n"
    proposed = "host = '0.0.0.0'\n"
    change = ProposedFileChange(
        path="server.py",
        change_type="modify",
        original_hash=content_hash(original),
        proposed_hash=content_hash(proposed),
        unified_diff="-127.0.0.1\n+0.0.0.0\n",
        proposed_content=proposed,
    )
    plan = DeliveryPlan(
        id="dpl_1",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="repo_1",
        base_branch="main",
        base_sha="a" * 40,
        title="Fix",
        rationale="Fix binding",
        files=[change],
        validation_steps=[],
        risk_level="medium",
        created_at=datetime.now(UTC),
    )
    service = DeliveryService()
    payload = service.approval_payload(plan, "create_draft_pull_request")
    assert service.approval_payload(plan, "create_draft_pull_request") == payload
    changed = plan.model_copy(update={"files": [change.model_copy(update={"unified_diff": "different"})]})
    assert service.approval_payload(changed, "create_draft_pull_request")["diff_hash"] != payload["diff_hash"]


def test_commit_message_is_part_of_the_delivery_approval_binding() -> None:
    change = ProposedFileChange(
        path="backend/app/main.py",
        change_type="modify",
        original_hash=content_hash("before\n"),
        proposed_hash=content_hash("after\n"),
        unified_diff="-before\n+after\n",
        proposed_content="after\n",
    )
    plan = DeliveryPlan(
        id="dpl_commit_message",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="repo_1",
        base_branch="main",
        base_sha="a" * 40,
        title="Fix CI working directory",
        rationale="The workflow needs the current application path.",
        files=[change],
        validation_steps=[],
        risk_level="high",
        created_at=datetime.now(UTC),
    )
    service = DeliveryService()
    initial = service.approval_payload(
        plan, "create_draft_pull_request", {"commit_message": plan.title}
    )
    renamed = plan.model_copy(update={"title": "ci: correct the working directory"})
    replacement = service.approval_payload(
        renamed, "create_draft_pull_request", {"commit_message": renamed.title}
    )

    assert initial["diff_hash"] == replacement["diff_hash"]
    assert initial["canonical_arguments"] != replacement["canonical_arguments"]
    assert canonical_json(initial) != canonical_json(replacement)


def test_changed_head_invalidates_approval(tmp_path: Path) -> None:
    service = DeliveryService()
    service.workspace.current_branch = lambda _path: "main"  # type: ignore[method-assign]
    service.workspace.head_commit = lambda _path: "b" * 40  # type: ignore[method-assign]
    plan = DeliveryPlan(
        id="dpl_1",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="repo_1",
        base_branch="main",
        base_sha="a" * 40,
        title="Fix",
        rationale="Fix",
        files=[],
        validation_steps=[],
        risk_level="low",
        created_at=datetime.now(UTC),
    )
    payload = service.approval_payload(plan, "create_draft_pull_request")
    approval = {
        **payload,
        "approval_hash": __import__("hashlib")
        .sha256(__import__("json").dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        .hexdigest(),
    }
    assert "HEAD changed" in (service.validate_binding(approval, plan.model_dump(), tmp_path) or "")


def test_changed_arguments_or_branch_invalidates_approval(tmp_path: Path) -> None:
    service = DeliveryService()
    service.workspace.current_branch = lambda _path: "other"  # type: ignore[method-assign]
    service.workspace.head_commit = lambda _path: "a" * 40  # type: ignore[method-assign]
    plan = DeliveryPlan(
        id="dpl_1",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        repository_id="repo_1",
        base_branch="main",
        base_sha="a" * 40,
        title="Fix",
        rationale="Fix",
        files=[],
        validation_steps=[],
        risk_level="low",
        created_at=datetime.now(UTC),
    )
    payload = service.approval_payload(plan, "create_draft_pull_request")
    approval = {
        **payload,
        "approval_hash": __import__("hashlib")
        .sha256(__import__("json").dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        .hexdigest(),
    }
    assert "branch changed" in (service.validate_binding(approval, plan.model_dump(), tmp_path) or "")
    service.workspace.current_branch = lambda _path: "main"  # type: ignore[method-assign]
    approval["canonical_arguments"] = {"other": True}
    assert "canonical binding changed" in (service.validate_binding(approval, plan.model_dump(), tmp_path) or "")
