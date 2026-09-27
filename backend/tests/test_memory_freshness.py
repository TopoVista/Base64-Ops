"""Integration-style path freshness tests using real temporary Git history."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.git.pr_status import PullRequestStatus
from app.memory.freshness import MemoryFreshnessService
from app.memory.memory_service import MemoryService
from app.memory.models import OperationalMemoryKind


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tests@example.invalid")
    _git(tmp_path, "config", "user.name", "Base64 Tests")
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("port = 8000\n")
    (tmp_path / "frontend" / "app.ts").write_text("export const app = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    return tmp_path


async def _evaluate(repo: Path, base: str, head: str, paths: list[str]):
    return await MemoryFreshnessService().evaluate(
        memory_id="mem_test",
        kind=OperationalMemoryKind.REPOSITORY_FACT,
        source_commit_sha=base,
        affected_paths=paths,
        current_sha=head,
        repo_path=repo,
        repository_id="owner/repository",
    )


@pytest.mark.asyncio
async def test_irrelevant_change_keeps_memory_current(git_repo: Path):
    base = _git(git_repo, "rev-parse", "HEAD")
    (git_repo / "frontend" / "app.ts").write_text("export const app = 2\n")
    _git(git_repo, "commit", "-am", "frontend only")

    result = await _evaluate(git_repo, base, _git(git_repo, "rev-parse", "HEAD"), ["backend/main.py"])

    assert result.freshness == "current"
    assert result.source == "local_git"


@pytest.mark.asyncio
async def test_relevant_change_requires_revalidation(git_repo: Path):
    base = _git(git_repo, "rev-parse", "HEAD")
    (git_repo / "backend" / "main.py").write_text("port = 8001\n")
    _git(git_repo, "commit", "-am", "change backend")

    result = await _evaluate(git_repo, base, _git(git_repo, "rev-parse", "HEAD"), ["backend/main.py"])

    assert result.freshness == "needs_revalidation"
    assert result.changed_relevant_paths == ["backend/main.py"]


@pytest.mark.asyncio
async def test_deleted_repository_fact_is_invalidated(git_repo: Path):
    base = _git(git_repo, "rev-parse", "HEAD")
    (git_repo / "backend" / "main.py").unlink()
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "remove backend")

    result = await _evaluate(git_repo, base, _git(git_repo, "rev-parse", "HEAD"), ["backend/main.py"])

    assert result.freshness == "stale"
    assert result.recommended_verification_status == "invalidated"
    assert result.deleted_paths == ["backend/main.py"]


@pytest.mark.asyncio
async def test_rename_is_retained_as_history(git_repo: Path):
    base = _git(git_repo, "rev-parse", "HEAD")
    _git(git_repo, "mv", "backend/main.py", "backend/server.py")
    _git(git_repo, "commit", "-m", "rename backend")

    result = await _evaluate(git_repo, base, _git(git_repo, "rev-parse", "HEAD"), ["backend/main.py"])

    assert result.freshness == "needs_revalidation"
    assert [(r.old_path, r.new_path) for r in result.renamed_paths] == [("backend/main.py", "backend/server.py")]


@pytest.mark.asyncio
async def test_unreachable_commit_fails_safe(git_repo: Path):
    result = await _evaluate(git_repo, "f" * 40, _git(git_repo, "rev-parse", "HEAD"), ["backend/main.py"])

    assert result.freshness == "needs_revalidation"
    assert result.comparison_complete is False
    assert "unavailable" in result.reason.lower()


def _delivered_doc() -> dict:
    return {
        "_id": "mongo-id", "id": "mem_delivered", "user_id": "user_a",
        "repository_id": "owner/repository", "branch": "main", "kind": "delivered_fix",
        "key": "fix", "value": "Applied binding fix", "tags": [], "confidence": 0.9,
        "applicability": {}, "deleted_at": None, "superseded_by": None,
        "provenance": {
            "run_id": "run_1",
            "evidence_ids": ["evd_1"],
            "source_commit_sha": "base",
            "affected_paths": ["backend/main.py"],
            "pr_number": 17,
        },
    }


@pytest.mark.asyncio
async def test_merge_promotion_requires_verified_github_merge():
    db = MagicMock()
    db.operational_memory.find_one = AsyncMock(side_effect=[_delivered_doc(), None])
    db.operational_memory.insert_one = AsyncMock()
    db.operational_memory.update_one = AsyncMock()
    merged = PullRequestStatus(
        repository_id="owner/repository", number=17, state="closed", draft=False,
        merged=True, merge_commit_sha="merged-sha", merged_at=datetime.now(UTC),
    )

    with patch("app.memory.memory_service.get_db", return_value=db), patch(
        "app.git.pr_status.PullRequestStatusProvider.get", new=AsyncMock(return_value=merged)
    ):
        result = await MemoryService().promote_to_merged_fix(
            entry_id="mem_delivered", user_id="user_a", repository_id="owner/repository"
        )

    assert result["promoted"] is True
    inserted = db.operational_memory.insert_one.await_args.args[0]
    assert inserted["kind"] == "merged_fix"
    assert inserted["metadata"]["source_memory_id"] == "mem_delivered"


@pytest.mark.asyncio
async def test_closed_unmerged_never_creates_merged_fix():
    db = MagicMock()
    db.operational_memory.find_one = AsyncMock(return_value=_delivered_doc())
    db.operational_memory.insert_one = AsyncMock()
    db.operational_memory.update_one = AsyncMock()
    closed = PullRequestStatus(repository_id="owner/repository", number=17, state="closed", draft=False, merged=False)

    with patch("app.memory.memory_service.get_db", return_value=db), patch(
        "app.git.pr_status.PullRequestStatusProvider.get", new=AsyncMock(return_value=closed)
    ):
        result = await MemoryService().promote_to_merged_fix(
            entry_id="mem_delivered", user_id="user_a", repository_id="owner/repository"
        )

    assert result["promoted"] is False
    assert "closed_unmerged" in result["reason"]
    db.operational_memory.insert_one.assert_not_awaited()
