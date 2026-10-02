from pathlib import Path

import pytest

from app.execution.policy import ExecutionPolicy, ExecutionPolicyError
from app.execution.runtime import ExecutionRuntimeManager


def test_environment_sanitization_strips_secrets() -> None:
    dirty_env = {
        "PATH": "/usr/bin",
        "LANG": "en_US.UTF-8",
        "OPENAI_API_KEY": "sk-proj-secretkey123",
        "CLERK_SECRET_KEY": "clerk_sec_456",
        "GITHUB_CLIENT_SECRET": "gh_secret_789",
        "AWS_SECRET_ACCESS_KEY": "aws_secret_abc",
        "DATABASE_URL": "postgres://user:pass@localhost/db",
    }

    clean_env = ExecutionPolicy.sanitize_environment(dirty_env)

    assert "PATH" in clean_env
    assert "LANG" in clean_env
    assert clean_env["CI"] == "true"

    assert "OPENAI_API_KEY" not in clean_env
    assert "CLERK_SECRET_KEY" not in clean_env
    assert "GITHUB_CLIENT_SECRET" not in clean_env
    assert "AWS_SECRET_ACCESS_KEY" not in clean_env
    assert "DATABASE_URL" not in clean_env


def test_workspace_integrity_rejects_modified_candidate(tmp_path: Path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text("HOST = '127.0.0.1'\n", encoding="utf-8")

    expected_hashes = {"app.py": "expected_hash_value_123"}
    before_snapshot = ExecutionPolicy.snapshot_workspace(tmp_path)

    # Modify file to simulate validator tampering
    app_file.write_text("HOST = 'tampered'\n", encoding="utf-8")

    with pytest.raises(ExecutionPolicyError, match="Candidate integrity check failed"):
        ExecutionPolicy.verify_workspace_integrity(tmp_path, expected_hashes, before_snapshot)


def test_workspace_integrity_rejects_sensitive_side_effects(tmp_path: Path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text("x = 1\n", encoding="utf-8")

    from app.services.delivery_service import content_hash

    expected_hashes = {"app.py": content_hash("x = 1\n")}
    before_snapshot = ExecutionPolicy.snapshot_workspace(tmp_path)

    # Create unexpected secret file side-effect
    (tmp_path / ".env").write_text("SECRET=leaked\n", encoding="utf-8")

    with pytest.raises(ExecutionPolicyError, match="sensitive file side-effect"):
        ExecutionPolicy.verify_workspace_integrity(tmp_path, expected_hashes, before_snapshot)


def test_runtime_manager_executes_registered_validator(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello world')\n", encoding="utf-8")
    manager = ExecutionRuntimeManager()

    res = manager.execute_validator(
        validator_id="python.syntax",
        arguments=["-m", "py_compile", "app.py"],
        repo_path=tmp_path,
        run_id="run_test",
        repository_id="repo_test",
    )

    assert res.status == "success"
    assert res.exit_code == 0


def test_runtime_manager_rejects_unregistered_validator(tmp_path: Path) -> None:
    manager = ExecutionRuntimeManager()

    res = manager.execute_validator(
        validator_id="unregistered.malicious_validator",
        arguments=["arbitrary_cmd"],
        repo_path=tmp_path,
        run_id="run_test",
        repository_id="repo_test",
    )

    assert res.status == "policy_denied"
    assert res.failure_type == "policy_denied"


def test_disabled_runtime_never_falls_back_to_local_repository_execution(tmp_path: Path, monkeypatch) -> None:
    """A small hosted API must fail closed when Docker isolation is absent."""
    (tmp_path / "app.py").write_text("print('hello world')\n", encoding="utf-8")
    manager = ExecutionRuntimeManager()
    monkeypatch.setattr(manager.settings, "execution_mode", "disabled")

    res = manager.execute_validator(
        validator_id="typescript.tsc",
        arguments=[],
        repo_path=tmp_path,
        run_id="run_test",
        repository_id="repo_test",
    )

    assert res.status == "policy_denied"
    assert res.failure_type == "policy_denied"
