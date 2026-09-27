"""API routes for repository-scoped operational memory.

All routes are scoped to the authenticated user.  No cross-user access.

GET    /api/memory/{repository_id}                          — list current memory entries
GET    /api/memory/{repository_id}/staleness                — return staleness report
POST   /api/memory/{repository_id}/check-staleness          — on-demand staleness check
DELETE /api/memory/entry/{entry_id}                         — soft-delete a single entry
POST   /api/memory/{repository_id}/entry/{entry_id}/refresh — refresh freshness or PR status
POST   /api/memory/{repository_id}/entry/{entry_id}/promote — promote DELIVERED_FIX→MERGED_FIX
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.routes.auth import get_current_user
from app.core.config import get_settings
from app.memory.memory_service import MemoryService
from app.services.workspace_service import WorkspaceService

router = APIRouter(tags=["memory"])

_memory = MemoryService()
_workspace = WorkspaceService()


def _repository_id_from_url(raw: str) -> str:
    """Normalize repository_id — accept either a full URL or a bare 'owner/repo'."""
    return raw.strip()


@router.get("/{repository_id:path}")
async def list_memory_entries(
    repository_id: str,
    include_stale: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """List operational memory entries for a repository, scoped to the current user."""
    settings = get_settings()
    if not settings.memory_enabled:
        return {"entries": [], "memory_enabled": False}

    entries = await _memory.list_entries(
        user_id=current_user["id"],
        repository_id=_repository_id_from_url(repository_id),
        include_stale=include_stale,
        limit=limit,
    )
    return {
        "repository_id": repository_id,
        "count": len(entries),
        "include_stale": include_stale,
        "entries": entries,
    }


@router.get("/{repository_id:path}/staleness")
async def get_staleness_report(
    repository_id: str,
    branch: str = Query(default="main"),
    commit_sha: str = Query(default=""),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a staleness summary for the repository without triggering an update."""
    settings = get_settings()
    if not settings.memory_enabled:
        return {"memory_enabled": False}

    from app.db.mongo import get_db

    db = get_db()
    base_filter = {
        "user_id": current_user["id"],
        "repository_id": _repository_id_from_url(repository_id),
        "branch": branch,
        "deleted_at": None,
    }
    total = await db.operational_memory.count_documents(base_filter)
    current_count = await db.operational_memory.count_documents({**base_filter, "staleness_status": "current"})
    stale_count = await db.operational_memory.count_documents({**base_filter, "staleness_status": "stale"})
    unverifiable_count = await db.operational_memory.count_documents(
        {**base_filter, "staleness_status": "unverifiable"}
    )
    needs_rev_count = await db.operational_memory.count_documents(
        {**base_filter, "verification_status": "needs_revalidation"}
    )
    return {
        "repository_id": repository_id,
        "branch": branch,
        "total_entries": total,
        "current_count": current_count,
        "stale_count": stale_count,
        "needs_revalidation_count": needs_rev_count,
        "unverifiable_count": unverifiable_count,
    }


@router.post("/{repository_id:path}/check-staleness")
async def trigger_staleness_check(
    repository_id: str,
    branch: str = Query(default="main"),
    commit_sha: str = Query(default=""),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """On-demand staleness check: bulk-flags stale entries for the given repository HEAD."""
    settings = get_settings()
    if not settings.memory_enabled:
        return {"memory_enabled": False}

    if not commit_sha:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="commit_sha is required for staleness check",
        )

    report = await _memory.check_staleness(
        user_id=current_user["id"],
        repository_id=_repository_id_from_url(repository_id),
        branch=branch,
        current_commit_sha=commit_sha,
    )
    return report.model_dump()


@router.delete("/entry/{entry_id}")
async def delete_memory_entry(
    entry_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Soft-delete a single memory entry.  Only the owning user may delete their entries."""
    deleted = await _memory.delete_entry(
        user_id=current_user["id"],
        entry_id=entry_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory entry not found or already deleted",
        )
    return {"deleted": True, "entry_id": entry_id}


@router.post("/{repository_id:path}/entry/{entry_id}/refresh")
async def refresh_memory_entry(
    repository_id: str,
    entry_id: str,
    commit_sha: str = Query(default=""),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Refresh freshness for a single memory entry against the current repository state.

    For REPOSITORY_FACT and similar entries: performs a path-aware git diff.
    commit_sha is the current HEAD SHA of the repository workspace.

    Returns the new freshness verdict and changed relevant paths.
    """
    settings = get_settings()
    if not settings.memory_enabled:
        return {"memory_enabled": False, "refreshed": False}

    if not commit_sha:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="commit_sha is required for freshness refresh",
        )

    result = await _memory.refresh_entry_freshness(
        entry_id=entry_id,
        user_id=current_user["id"],
        current_sha=commit_sha,
        repository_id=_repository_id_from_url(repository_id),
    )

    if not result.get("refreshed") and result.get("reason") == "entry not found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory entry not found or not owned by current user",
        )

    return {
        "entry_id": entry_id,
        "repository_id": repository_id,
        **result,
    }


@router.post("/{repository_id:path}/entry/{entry_id}/promote")
async def promote_memory_entry(
    repository_id: str,
    entry_id: str,
    pr_number: int | None = Query(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Attempt to promote a DELIVERED_FIX entry to MERGED_FIX.

    Performs a read-only GitHub PR status lookup.  If the PR is confirmed
    merged, a new MERGED_FIX entry is created and linked to the original.
    If GitHub is unavailable, the current state is left unchanged.

    Requires a connected GitHub account for the current user.
    """
    settings = get_settings()
    if not settings.memory_enabled:
        return {"memory_enabled": False, "promoted": False}

    result = await _memory.promote_to_merged_fix(
        entry_id=entry_id,
        user_id=current_user["id"],
        repository_id=_repository_id_from_url(repository_id),
        pr_number=pr_number,
        user_id_for_github=current_user["id"],
    )

    return {
        "entry_id": entry_id,
        "repository_id": repository_id,
        **result,
    }
