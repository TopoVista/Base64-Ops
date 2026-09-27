"""Memory freshness evaluation service.

Determines whether a stored memory entry is still applicable given the
current repository state, using real Git history via GitChangeProvider.

Architecture
------------
1. For each candidate memory entry, extract source_commit_sha + affected_paths.
2. Look up changed paths between source_commit_sha and current HEAD using
   GitChangeProvider (local git first, GitHub compare fallback).
3. If affected paths overlap with changed paths → needs_revalidation.
4. If affected files were deleted → stale/invalidated per kind semantics.
5. If affected files were renamed → rename is tracked; history preserved.
6. If lookup fails (unreachable commit, unavailable) → needs_revalidation
   (never assume unchanged).

Caching
-------
Results are cached per (repository_id, base_sha, head_sha) within a single
service instance lifetime (a single graph run).  Cache key never contains
secrets or file content.

Invariants
----------
- source_commit_sha is NEVER overwritten (historical provenance preserved).
- last_verified_commit_sha is updated when revalidation confirms the fact.
- Memory cannot authorize mutations (no approval logic here).
- All DB writes are user-scoped (tenant isolation).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.git.change_provider import ChangedPathsResult, GitChangeProvider, RenamedPath
from app.memory.models import (
    FreshnessLevel,
    OperationalMemoryKind,
    VerificationStatus,
)
from app.utils.datetime import utc_now

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class MemoryFreshnessResult(BaseModel):
    """Result of a freshness evaluation for a single memory entry."""

    memory_id: str

    freshness: FreshnessLevel

    # verification_status to apply if the caller updates the entry.
    recommended_verification_status: VerificationStatus

    # Paths that changed AND overlap with this entry's affected_paths.
    changed_relevant_paths: list[str] = Field(default_factory=list)

    # Renamed paths relevant to this entry.
    renamed_paths: list[RenamedPath] = Field(default_factory=list)

    # Deleted paths relevant to this entry.
    deleted_paths: list[str] = Field(default_factory=list)

    # Human-readable explanation.
    reason: str

    # Which source produced the ChangedPathsResult ("local_git",
    # "github_compare", "unavailable", "same_sha", "no_affected_paths").
    source: str | None = None

    # Whether the underlying path comparison was complete.
    comparison_complete: bool = True


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def _cache_key(repository_id: str, base_sha: str, head_sha: str) -> str:
    return f"{repository_id}|{base_sha}|{head_sha}"


# ---------------------------------------------------------------------------
# Freshness service
# ---------------------------------------------------------------------------


class MemoryFreshnessService:
    """Evaluate freshness for memory entries, with per-run caching.

    Instantiate once per graph run so the diff cache covers all memory entries
    retrieved during that run without re-fetching the same commit pair.
    """

    def __init__(self) -> None:
        self._change_provider = GitChangeProvider()
        # Cache: key → ChangedPathsResult
        self._diff_cache: dict[str, ChangedPathsResult] = {}

    async def evaluate(
        self,
        *,
        memory_id: str,
        kind: OperationalMemoryKind,
        source_commit_sha: str | None,
        affected_paths: list[str],
        current_sha: str,
        repo_path: Path | None = None,
        user_id: str | None = None,
        repository_id: str | None = None,
    ) -> MemoryFreshnessResult:
        """Evaluate the freshness of a single memory entry.

        Decision table
        --------------
        source_commit_sha is None
            → needs_revalidation ("no source SHA recorded")

        source_commit_sha == current_sha
            → current ("same SHA, no changes possible")

        affected_paths is empty (legacy entry)
            → needs_revalidation (cannot tell which files matter)

        overlap(affected_paths, changed_paths)
            overlap contains deletions only
                kind is REPOSITORY_FACT → invalidated
                other kinds             → stale (historical incident still useful)
            overlap contains renames/modifications
                → needs_revalidation
            no overlap
                → current ("relevant paths unchanged")

        comparison unavailable / incomplete
            → needs_revalidation ("cannot determine changed paths")
        """
        # Case: no source SHA
        if not source_commit_sha:
            return MemoryFreshnessResult(
                memory_id=memory_id,
                freshness="needs_revalidation",
                recommended_verification_status="needs_revalidation",
                reason="No source_commit_sha recorded; cannot evaluate freshness.",
                source=None,
                comparison_complete=False,
            )

        # Case: same SHA
        if source_commit_sha == current_sha:
            return MemoryFreshnessResult(
                memory_id=memory_id,
                freshness="current",
                recommended_verification_status="verified",
                reason="Source SHA matches current HEAD; no changes possible.",
                source="same_sha",
                comparison_complete=True,
            )

        # Case: no affected paths
        if not affected_paths:
            return MemoryFreshnessResult(
                memory_id=memory_id,
                freshness="needs_revalidation",
                recommended_verification_status="needs_revalidation",
                reason=(
                    "No affected_paths recorded for this entry; "
                    "cannot perform path-aware freshness check."
                ),
                source="no_affected_paths",
                comparison_complete=False,
            )

        # Fetch changed paths (with caching)
        diff_result = await self._get_diff(
            base_sha=source_commit_sha,
            head_sha=current_sha,
            repo_path=repo_path,
            user_id=user_id,
            repository_id=repository_id,
        )

        # A partial or unavailable comparison can never prove that a memory is
        # current.  For example, an unreachable local commit is reported as
        # ``local_git`` with ``complete=False`` so that GitHub can be tried by
        # callers, but must still fail safe if no complete comparison exists.
        if not diff_result.complete:
            return MemoryFreshnessResult(
                memory_id=memory_id,
                freshness="needs_revalidation",
                recommended_verification_status="needs_revalidation",
                reason=diff_result.warning or "Changed paths unavailable; cannot evaluate freshness.",
                source=diff_result.source,
                comparison_complete=False,
            )

        # Compare affected_paths against changed_paths
        affected_set = set(affected_paths)
        changed_set = set(diff_result.changed_paths)
        deleted_set = set(diff_result.deleted_paths)

        relevant_changed = sorted(affected_set & changed_set)
        relevant_deleted = sorted(affected_set & deleted_set)
        relevant_renames = [
            r for r in diff_result.renamed_paths
            if r.old_path in affected_set or r.new_path in affected_set
        ]

        if not relevant_changed and not relevant_renames:
            # No overlap — entry is still applicable
            return MemoryFreshnessResult(
                memory_id=memory_id,
                freshness="current",
                recommended_verification_status="verified",
                reason=(
                    f"None of the {len(affected_paths)} affected path(s) changed "
                    f"between {source_commit_sha[:8]} and {current_sha[:8]}."
                ),
                source=diff_result.source,
                comparison_complete=diff_result.complete,
            )

        # Overlap detected — check if it is all-deletions
        if relevant_deleted and set(relevant_changed) == set(relevant_deleted) and not relevant_renames:
            # Every overlapping change is a deletion
            if kind == OperationalMemoryKind.REPOSITORY_FACT:
                status: VerificationStatus = "invalidated"
                freshness: FreshnessLevel = "stale"
                reason = (
                    f"File(s) supporting this repository fact were deleted: "
                    f"{', '.join(relevant_deleted)}."
                )
            else:
                status = "stale"
                freshness = "stale"
                reason = (
                    f"File(s) referenced by this entry were deleted: "
                    f"{', '.join(relevant_deleted)}. "
                    "Historical context is preserved but requires revalidation."
                )
        else:
            # Modifications or renames
            status = "needs_revalidation"
            freshness = "needs_revalidation"
            parts: list[str] = []
            if relevant_changed:
                parts.append(f"changed: {', '.join(relevant_changed[:5])}")
            if relevant_renames:
                rn_strs = [f"{r.old_path} → {r.new_path}" for r in relevant_renames[:3]]
                parts.append(f"renamed: {', '.join(rn_strs)}")
            reason = (
                f"{len(relevant_changed)} affected path(s) changed between "
                f"{source_commit_sha[:8]} and {current_sha[:8]}: "
                + "; ".join(parts)
            )

        return MemoryFreshnessResult(
            memory_id=memory_id,
            freshness=freshness,
            recommended_verification_status=status,
            changed_relevant_paths=relevant_changed,
            renamed_paths=relevant_renames,
            deleted_paths=relevant_deleted,
            reason=reason,
            source=diff_result.source,
            comparison_complete=diff_result.complete,
        )

    async def evaluate_many(
        self,
        entries: list[dict[str, Any]],
        *,
        current_sha: str,
        repo_path: Path | None = None,
        user_id: str | None = None,
        repository_id: str | None = None,
    ) -> list[MemoryFreshnessResult]:
        """Evaluate freshness for multiple entries, sharing the diff cache.

        ``entries`` is a list of dicts with keys:
            id, kind, provenance.source_commit_sha, provenance.affected_paths
        """
        results: list[MemoryFreshnessResult] = []
        for entry in entries:
            prov = entry.get("provenance") or {}
            try:
                r = await self.evaluate(
                    memory_id=entry.get("id", ""),
                    kind=OperationalMemoryKind(entry.get("kind", "repository_fact")),
                    source_commit_sha=prov.get("source_commit_sha"),
                    affected_paths=prov.get("affected_paths") or [],
                    current_sha=current_sha,
                    repo_path=repo_path,
                    user_id=user_id,
                    repository_id=repository_id,
                )
            except Exception as exc:
                logger.debug("freshness.evaluate_many: entry %s failed: %s", entry.get("id"), exc)
                r = MemoryFreshnessResult(
                    memory_id=entry.get("id", ""),
                    freshness="needs_revalidation",
                    recommended_verification_status="needs_revalidation",
                    reason=f"Freshness evaluation failed: {exc}",
                    source=None,
                    comparison_complete=False,
                )
            results.append(r)
        return results

    # ------------------------------------------------------------------
    # Cached diff lookup
    # ------------------------------------------------------------------

    async def _get_diff(
        self,
        *,
        base_sha: str,
        head_sha: str,
        repo_path: Path | None,
        user_id: str | None,
        repository_id: str | None,
    ) -> ChangedPathsResult:
        key = _cache_key(repository_id or "", base_sha, head_sha)
        if key in self._diff_cache:
            return self._diff_cache[key]

        result = await self._change_provider.changed_paths(
            base_sha=base_sha,
            head_sha=head_sha,
            repo_path=repo_path,
            user_id=user_id,
            repository_id=repository_id,
        )
        self._diff_cache[key] = result
        return result

    # ------------------------------------------------------------------
    # Persist freshness result to DB
    # ------------------------------------------------------------------

    async def apply_to_db(
        self,
        result: MemoryFreshnessResult,
        *,
        user_id: str,
        current_sha: str,
    ) -> bool:
        """Update the DB record for this memory entry based on freshness evaluation.

        Updates verification_status and staleness_status.
        Does NOT overwrite source_commit_sha (historical provenance preserved).
        Updates last_verified_commit_sha when result is "current".

        Returns True if the entry was updated.
        """
        from app.db.mongo import get_db

        db = get_db()
        now = utc_now()

        new_vs = result.recommended_verification_status
        new_staleness: Literal["current", "stale", "unverifiable"]
        if new_vs in ("verified", "partially_verified"):
            new_staleness = "current"
        elif new_vs in ("needs_revalidation", "stale", "invalidated"):
            new_staleness = "stale"
        else:
            new_staleness = "unverifiable"

        set_fields: dict[str, Any] = {
            "verification_status": new_vs,
            "staleness_status": new_staleness,
        }

        # Track last_verified_commit_sha separately from source_commit_sha
        if new_vs in ("verified", "partially_verified"):
            set_fields["last_verified_commit_sha"] = current_sha
            set_fields["last_confirmed_at"] = now

        r = await db.operational_memory.update_one(
            {"id": result.memory_id, "user_id": user_id, "deleted_at": None},
            {"$set": set_fields},
        )
        return r.modified_count > 0
