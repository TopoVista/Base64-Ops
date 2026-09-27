"""Repository-scoped operational memory service.

Architecture
------------
Write path  — kind-specific write gates enforced by MemoryWritePolicy.
Read path   — recall_for_context (current+fresh, capped 3–5 entries)
              recall_historical  (stale/needs-revalidation, always labelled)
Staleness   — path-aware: compares affected_paths against changed files
              between source_commit_sha and current HEAD.
              Bulk SHA equality is used only as a fast pre-filter when
              Git path-diff is unavailable.
History     — stale entries are never deleted; they remain queryable and
              are surfaced with [HISTORICAL — REQUIRES CURRENT VERIFICATION].
Precedence  — system policy > current evidence > verified memory >
              needs-revalidation memory > stale historical memory.

Critical invariants (all enforced here, not in callers)
--------------------------------------------------------
1. Every entry requires at least one verified evidence_id.
2. All value strings are redacted before persistence.
3. DELIVERED_FIX requires DeliveryResult.status == "success".
4. MERGED_FIX requires explicit GitHub merge confirmation.
5. Draft PR creation or push success do NOT produce MERGED_FIX.
6. Memory entries carry zero approval weight.
7. Stale entries are retained; freshness is communicated to callers.
8. Current evidence always overrides memory (callers must enforce this).
9. Learning can be disabled via Settings.memory_learning_enabled.
10. All reads are scoped to user_id (tenant isolation).
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

from app.db.mongo import get_db
from app.memory.models import (
    FreshnessLevel,
    MemoryApplicability,
    MemoryEntryCandidate,
    MemoryMatch,
    MemoryProvenance,
    MemorySummary,
    OperationalMemoryEntry,
    OperationalMemoryKind,
    ProvenanceSourceType,
    StalenessReport,
    VerificationStatus,
)
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now
from app.utils.ids import new_id

logger = logging.getLogger(__name__)

# Default context budget — tight by design.
# 3–5 entries is the intended range; callers may override downward.
_CONTEXT_CAP_DEFAULT = 5
_MIN_CONFIDENCE_DEFAULT = 0.6

# Historical recall cap — kept small so it never dominates context.
_HISTORICAL_CAP_DEFAULT = 3


# ---------------------------------------------------------------------------
# Path applicability helper
# ---------------------------------------------------------------------------


def _applicability_matches(applicability: MemoryApplicability, candidate_paths: list[str]) -> bool:
    """Return True when this entry is applicable to at least one candidate path.

    An entry with no rules (empty prefix, no globs) matches all paths.
    """
    if not candidate_paths:
        return True

    has_rules = bool(applicability.path_prefix or applicability.glob_patterns)
    if not has_rules:
        return True

    for path in candidate_paths:
        if applicability.path_prefix and path.startswith(applicability.path_prefix):
            return True
        for pattern in applicability.glob_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
    return False


def _affected_paths_match(affected_paths: list[str], candidate_paths: list[str]) -> bool:
    """Return True if any affected path overlaps with the candidate investigation paths."""
    if not affected_paths or not candidate_paths:
        return True  # No constraint → always relevant
    aff = set(affected_paths)
    return any(p in aff for p in candidate_paths)


def _to_document(entry: OperationalMemoryEntry) -> dict[str, Any]:
    """Convert an OperationalMemoryEntry to a MongoDB document."""
    doc = entry.model_dump()
    doc["_id"] = entry.id
    doc["provenance"] = entry.provenance.model_dump()
    doc["applicability"] = entry.applicability.model_dump()
    # Store kind as its string value for MongoDB querying.
    doc["kind"] = str(entry.kind)
    return doc


def _freshness_from_status(verification_status: VerificationStatus) -> FreshnessLevel:
    """Derive a simple FreshnessLevel from the full VerificationStatus."""
    if verification_status in ("verified", "partially_verified"):
        return "current"
    if verification_status == "needs_revalidation":
        return "needs_revalidation"
    return "stale"


def _to_summary(doc: dict[str, Any]) -> MemorySummary:
    """Project a stored document to the lightweight context-injection model."""
    provenance = doc.get("provenance", {})
    applicability = doc.get("applicability", {})
    globs = applicability.get("glob_patterns") or []
    path_hint = applicability.get("path_prefix") or (globs[0] if globs else None)
    v_status: VerificationStatus = doc.get("verification_status", "verified")
    return MemorySummary(
        id=doc["id"],
        kind=OperationalMemoryKind(doc["kind"]),
        key=doc["key"],
        value=doc["value"],
        tags=doc.get("tags", []),
        confidence=doc.get("confidence", 0.7),
        freshness=_freshness_from_status(v_status),
        verification_status=v_status,
        source_commit_sha=provenance.get("source_commit_sha"),
        last_verified_commit_sha=doc.get("last_verified_commit_sha"),
        last_confirmed_at=doc["last_confirmed_at"],
        confirmed_count=doc.get("confirmed_count", 1),
        evidence_ids=provenance.get("evidence_ids", []),
        affected_paths=provenance.get("affected_paths", []),
        path_hint=path_hint,
        pr_number=provenance.get("pr_number"),
        pr_url=provenance.get("pr_url"),
        delivery_outcome=doc.get("delivery_outcome"),
    )


def _to_match(doc: dict[str, Any], relevance_reasons: list[str]) -> MemoryMatch:
    """Project a stored document to a MemoryMatch retrieval result."""
    provenance = doc.get("provenance", {})
    v_status: VerificationStatus = doc.get("verification_status", "verified")
    return MemoryMatch(
        memory_id=doc["id"],
        kind=OperationalMemoryKind(doc["kind"]),
        key=doc["key"],
        value=doc["value"],
        tags=doc.get("tags", []),
        confidence=doc.get("confidence", 0.7),
        freshness=_freshness_from_status(v_status),
        verification_status=v_status,
        source_commit_sha=provenance.get("source_commit_sha"),
        affected_paths=provenance.get("affected_paths", []),
        relevance_reasons=relevance_reasons,
    )


# ---------------------------------------------------------------------------
# Write policy
# ---------------------------------------------------------------------------


class MemoryWritePolicy:
    """Deterministic gate controlling which candidates may be persisted.

    The LLM may produce a structured MemoryEntryCandidate, but only this
    class decides eligibility.  Rules per kind:

    REPOSITORY_FACT
        Allowed when supported by current evidence.
        Requires: repository_id, source_commit_sha, evidence_ids, affected_paths.

    INCIDENT_OUTCOME
        Allowed when investigation completed with evidence-supported diagnosis.
        A code change is NOT required.
        Requires: evidence_ids, run_id.

    DELIVERED_FIX
        Requires: DeliveryResult.status == "success" (delivery_result_id set).

    MERGED_FIX
        Requires: explicit github_merge_confirmed=True flag.
        Draft PR, push success, or branch creation do NOT qualify.

    REJECTED_FIX
        Requires: an actual proposal/approval rejection event.
        (delivery_result_id left None; rejection_confirmed=True required)

    USER_CORRECTION
        Requires: explicit user statement (is_explicit_user_statement=True).

    OPERATIONAL_PREFERENCE
        Requires: explicit user preference (is_explicit_preference=True).
        Single-action inference is not sufficient.
    """

    @staticmethod
    def check(
        candidate: MemoryEntryCandidate,
        verified_evidence_ids: set[str],
        *,
        delivery_result_id: str | None = None,
        github_merge_confirmed: bool = False,
        rejection_confirmed: bool = False,
        is_explicit_user_statement: bool = False,
        is_explicit_preference: bool = False,
        min_confidence: float = _MIN_CONFIDENCE_DEFAULT,
    ) -> tuple[bool, str]:
        """Return (allowed, reason).

        reason is empty when allowed=True.
        """
        kind = candidate.kind

        # Universal: evidence gate
        valid_eids = [e for e in candidate.evidence_ids if e in verified_evidence_ids]
        if not valid_eids and kind not in (
            OperationalMemoryKind.USER_CORRECTION,
            OperationalMemoryKind.OPERATIONAL_PREFERENCE,
        ):
            return False, f"No verified evidence IDs for kind={kind}"

        # Universal: confidence gate
        if candidate.confidence < min_confidence:
            return False, (
                f"Confidence {candidate.confidence:.2f} below threshold {min_confidence:.2f} for kind={kind}"
            )

        # Kind-specific gates
        if kind == OperationalMemoryKind.DELIVERED_FIX:
            if not delivery_result_id:
                return False, "DELIVERED_FIX requires a delivery_result_id (DeliveryResult.status==success)"

        elif kind == OperationalMemoryKind.MERGED_FIX:
            if not github_merge_confirmed:
                return False, (
                    "MERGED_FIX requires explicit GitHub merge confirmation. "
                    "Draft PR creation and push are not sufficient."
                )

        elif kind == OperationalMemoryKind.REJECTED_FIX:
            if not rejection_confirmed:
                return False, "REJECTED_FIX requires a confirmed proposal/approval rejection event"

        elif kind == OperationalMemoryKind.USER_CORRECTION:
            if not is_explicit_user_statement:
                return False, "USER_CORRECTION requires an explicit user statement"

        elif kind == OperationalMemoryKind.OPERATIONAL_PREFERENCE:
            if not is_explicit_preference:
                return False, (
                    "OPERATIONAL_PREFERENCE requires an explicit user preference. "
                    "Single-action inference is not sufficient."
                )

        return True, ""


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class MemoryService:
    """Single entry-point for all operational memory operations.

    Precedence rule enforced at read time:
        system / policy
        > current trusted repository/runtime evidence
        > verified applicable operational memory  (freshness="current")
        > needs-revalidation memory               (freshness="needs_revalidation")
        > stale historical memory                 (freshness="stale")

    This class enforces tenant isolation: every query is scoped to user_id.
    Cross-user access is architecturally impossible through this interface.
    """

    def __init__(
        self,
        *,
        max_context_entries: int = _CONTEXT_CAP_DEFAULT,
        max_historical_entries: int = _HISTORICAL_CAP_DEFAULT,
        min_confidence: float = _MIN_CONFIDENCE_DEFAULT,
    ) -> None:
        self.redactor = RedactionService()
        self.max_context_entries = max_context_entries
        self.max_historical_entries = max_historical_entries
        self.min_confidence = min_confidence
        self.policy = MemoryWritePolicy()

    # ------------------------------------------------------------------
    # Internal shared upsert logic
    # ------------------------------------------------------------------

    async def _upsert_candidate(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        source_type: ProvenanceSourceType,
        candidate: MemoryEntryCandidate,
        valid_eids: list[str],
        delivery_result_id: str | None = None,
        initial_verification_status: VerificationStatus = "verified",
    ) -> str | None:
        """Upsert a single validated candidate.  Returns the persisted entry ID or None."""
        db = get_db()
        safe_value = self.redactor.redact(candidate.value)

        composite_filter = {
            "user_id": user_id,
            "repository_id": repository_id,
            "branch": branch,
            "kind": str(candidate.kind),
            "key": candidate.key,
            "deleted_at": None,
        }

        existing = await db.operational_memory.find_one(composite_filter)

        now = utc_now()

        if existing is None:
            entry = OperationalMemoryEntry(
                id=new_id("mem_"),
                user_id=user_id,
                repository_id=repository_id,
                branch=branch,
                kind=candidate.kind,
                key=candidate.key,
                value=safe_value,
                tags=candidate.tags,
                confidence=candidate.confidence,
                provenance=MemoryProvenance(
                    source_type=source_type,
                    evidence_ids=valid_eids,
                    run_id=run_id,
                    delivery_result_id=delivery_result_id,
                    source_commit_sha=commit_sha,
                    affected_paths=candidate.affected_paths,
                    extracted_at=now,
                ),
                applicability=candidate.applicability,
                created_at=now,
                last_confirmed_at=now,
                confirmed_count=1,
                verification_status=initial_verification_status,
                staleness_status="current",
                metadata=candidate.metadata,
            )
            await db.operational_memory.insert_one(_to_document(entry))
            logger.debug(
                "memory: new entry %s kind=%s key=%s",
                entry.id,
                entry.kind,
                entry.key,
            )
            return entry.id

        elif existing.get("value") == safe_value:
            # Same value — confirm and update provenance.
            await db.operational_memory.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "provenance.source_commit_sha": commit_sha,
                        "last_confirmed_at": now,
                        "verification_status": initial_verification_status,
                        "staleness_status": "current",
                        "tags": list(set(existing.get("tags", []) + candidate.tags)),
                    },
                    "$inc": {"confirmed_count": 1},
                    "$addToSet": {"provenance.evidence_ids": {"$each": valid_eids}},
                },
            )
            logger.debug(
                "memory: confirmed entry %s kind=%s key=%s",
                existing["id"],
                existing["kind"],
                existing["key"],
            )
            return existing["id"]

        else:
            # Conflicting value — supersede old, insert new.
            new_entry = OperationalMemoryEntry(
                id=new_id("mem_"),
                user_id=user_id,
                repository_id=repository_id,
                branch=branch,
                kind=candidate.kind,
                key=candidate.key,
                value=safe_value,
                tags=candidate.tags,
                confidence=candidate.confidence,
                provenance=MemoryProvenance(
                    source_type=source_type,
                    evidence_ids=valid_eids,
                    run_id=run_id,
                    delivery_result_id=delivery_result_id,
                    source_commit_sha=commit_sha,
                    affected_paths=candidate.affected_paths,
                    extracted_at=now,
                ),
                applicability=candidate.applicability,
                created_at=now,
                last_confirmed_at=now,
                confirmed_count=1,
                verification_status=initial_verification_status,
                staleness_status="current",
                metadata=candidate.metadata,
            )
            await db.operational_memory.insert_one(_to_document(new_entry))
            await db.operational_memory.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "superseded_by": new_entry.id,
                        "verification_status": "invalidated",
                        "staleness_status": "stale",
                    }
                },
            )
            logger.info(
                "memory: superseded entry %s → new entry %s kind=%s key=%s",
                existing["id"],
                new_entry.id,
                new_entry.kind,
                new_entry.key,
            )
            return new_entry.id

    # ------------------------------------------------------------------
    # Write path — kind-gated
    # ------------------------------------------------------------------

    async def record_from_delivery(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        delivery_result_id: str,
        verified_evidence_ids: set[str],
        candidates: list[MemoryEntryCandidate],
    ) -> list[str]:
        """Persist REPOSITORY_FACT and DELIVERED_FIX candidates from a successful delivery.

        Only candidates that pass MemoryWritePolicy are accepted.
        DELIVERED_FIX entries require delivery_result_id (non-empty).
        REPOSITORY_FACT entries require verified evidence.
        """
        persisted_ids: list[str] = []

        for candidate in candidates:
            # Restrict to the kinds this method is authorised to write.
            if candidate.kind not in (
                OperationalMemoryKind.REPOSITORY_FACT,
                OperationalMemoryKind.DELIVERED_FIX,
            ):
                logger.warning(
                    "memory: record_from_delivery called with unsupported kind=%s key=%s — skipped",
                    candidate.kind,
                    candidate.key,
                )
                continue

            valid_eids = [e for e in candidate.evidence_ids if e in verified_evidence_ids]
            allowed, reason = self.policy.check(
                candidate,
                verified_evidence_ids,
                delivery_result_id=delivery_result_id,
                min_confidence=self.min_confidence,
            )
            if not allowed:
                logger.warning("memory: dropping candidate kind=%s key=%s — %s", candidate.kind, candidate.key, reason)
                continue

            try:
                entry_id = await self._upsert_candidate(
                    user_id=user_id,
                    repository_id=repository_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    run_id=run_id,
                    source_type="delivery_result",
                    candidate=candidate,
                    valid_eids=valid_eids,
                    delivery_result_id=delivery_result_id,
                    initial_verification_status="verified",
                )
                if entry_id:
                    persisted_ids.append(entry_id)
            except Exception:
                logger.exception("memory: upsert failed kind=%s key=%s", candidate.kind, candidate.key)

        return persisted_ids

    async def record_incident_outcome(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        verified_evidence_ids: set[str],
        candidates: list[MemoryEntryCandidate],
    ) -> list[str]:
        """Persist INCIDENT_OUTCOME and REPOSITORY_FACT candidates.

        Does NOT require a successful delivery.  Evidence is still required.
        """
        persisted_ids: list[str] = []

        for candidate in candidates:
            if candidate.kind not in (
                OperationalMemoryKind.INCIDENT_OUTCOME,
                OperationalMemoryKind.REPOSITORY_FACT,
            ):
                logger.warning(
                    "memory: record_incident_outcome called with unsupported kind=%s — skipped",
                    candidate.kind,
                )
                continue

            valid_eids = [e for e in candidate.evidence_ids if e in verified_evidence_ids]
            allowed, reason = self.policy.check(
                candidate,
                verified_evidence_ids,
                min_confidence=self.min_confidence,
            )
            if not allowed:
                logger.warning("memory: dropping candidate kind=%s key=%s — %s", candidate.kind, candidate.key, reason)
                continue

            try:
                entry_id = await self._upsert_candidate(
                    user_id=user_id,
                    repository_id=repository_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    run_id=run_id,
                    source_type="incident_outcome",
                    candidate=candidate,
                    valid_eids=valid_eids,
                    initial_verification_status="partially_verified",
                )
                if entry_id:
                    persisted_ids.append(entry_id)
            except Exception:
                logger.exception("memory: upsert failed kind=%s key=%s", candidate.kind, candidate.key)

        return persisted_ids

    async def record_rejected_fix(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        verified_evidence_ids: set[str],
        candidates: list[MemoryEntryCandidate],
    ) -> list[str]:
        """Persist REJECTED_FIX entries.

        These record what was proposed and that it was rejected.
        They do NOT store "this fix is wrong" as a general truth.
        """
        persisted_ids: list[str] = []

        for candidate in candidates:
            if candidate.kind != OperationalMemoryKind.REJECTED_FIX:
                continue

            valid_eids = [e for e in candidate.evidence_ids if e in verified_evidence_ids]
            allowed, reason = self.policy.check(
                candidate,
                verified_evidence_ids,
                rejection_confirmed=True,
                min_confidence=self.min_confidence,
            )
            if not allowed:
                logger.warning("memory: dropping rejected_fix candidate key=%s — %s", candidate.key, reason)
                continue

            try:
                entry_id = await self._upsert_candidate(
                    user_id=user_id,
                    repository_id=repository_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    run_id=run_id,
                    source_type="rejected_fix",
                    candidate=candidate,
                    valid_eids=valid_eids,
                    initial_verification_status="partially_verified",
                )
                if entry_id:
                    persisted_ids.append(entry_id)
            except Exception:
                logger.exception("memory: upsert failed kind=rejected_fix key=%s", candidate.key)

        return persisted_ids

    async def record_user_correction(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        candidates: list[MemoryEntryCandidate],
    ) -> list[str]:
        """Persist USER_CORRECTION entries from explicit user statements.

        Evidence IDs are optional here (user statement is the source).
        """
        persisted_ids: list[str] = []

        for candidate in candidates:
            if candidate.kind != OperationalMemoryKind.USER_CORRECTION:
                continue

            allowed, reason = self.policy.check(
                candidate,
                set(),  # Evidence not required for user corrections
                is_explicit_user_statement=True,
                min_confidence=self.min_confidence,
            )
            if not allowed:
                logger.warning("memory: dropping user_correction candidate key=%s — %s", candidate.key, reason)
                continue

            try:
                entry_id = await self._upsert_candidate(
                    user_id=user_id,
                    repository_id=repository_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    run_id=run_id,
                    source_type="user_correction",
                    candidate=candidate,
                    valid_eids=candidate.evidence_ids,
                    initial_verification_status="verified",
                )
                if entry_id:
                    persisted_ids.append(entry_id)
            except Exception:
                logger.exception("memory: upsert failed kind=user_correction key=%s", candidate.key)

        return persisted_ids

    async def record_operational_preference(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        candidates: list[MemoryEntryCandidate],
    ) -> list[str]:
        """Persist OPERATIONAL_PREFERENCE entries.

        These must be explicit user preferences, not inferred from a single action.
        """
        persisted_ids: list[str] = []

        for candidate in candidates:
            if candidate.kind != OperationalMemoryKind.OPERATIONAL_PREFERENCE:
                continue

            allowed, reason = self.policy.check(
                candidate,
                set(),
                is_explicit_preference=True,
                min_confidence=self.min_confidence,
            )
            if not allowed:
                logger.warning("memory: dropping preference candidate key=%s — %s", candidate.key, reason)
                continue

            try:
                entry_id = await self._upsert_candidate(
                    user_id=user_id,
                    repository_id=repository_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    run_id=run_id,
                    source_type="user_preference",
                    candidate=candidate,
                    valid_eids=[],
                    initial_verification_status="verified",
                )
                if entry_id:
                    persisted_ids.append(entry_id)
            except Exception:
                logger.exception("memory: upsert failed kind=operational_preference key=%s", candidate.key)

        return persisted_ids

    async def record_merged_fix(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        verified_evidence_ids: set[str],
        candidates: list[MemoryEntryCandidate],
        github_merge_confirmed: bool,
    ) -> list[str]:
        """Persist MERGED_FIX entries.

        Requires github_merge_confirmed=True — GitHub returning PR merged state.
        Draft PR or push alone do NOT qualify.
        """
        if not github_merge_confirmed:
            logger.warning("memory: record_merged_fix called without merge confirmation — all candidates dropped")
            return []

        persisted_ids: list[str] = []

        for candidate in candidates:
            if candidate.kind != OperationalMemoryKind.MERGED_FIX:
                continue

            valid_eids = [e for e in candidate.evidence_ids if e in verified_evidence_ids]
            allowed, reason = self.policy.check(
                candidate,
                verified_evidence_ids,
                github_merge_confirmed=True,
                min_confidence=self.min_confidence,
            )
            if not allowed:
                logger.warning("memory: dropping merged_fix candidate key=%s — %s", candidate.key, reason)
                continue

            try:
                entry_id = await self._upsert_candidate(
                    user_id=user_id,
                    repository_id=repository_id,
                    branch=branch,
                    commit_sha=commit_sha,
                    run_id=run_id,
                    source_type="merged_fix",
                    candidate=candidate,
                    valid_eids=valid_eids,
                    initial_verification_status="verified",
                )
                if entry_id:
                    persisted_ids.append(entry_id)
            except Exception:
                logger.exception("memory: upsert failed kind=merged_fix key=%s", candidate.key)

        return persisted_ids

    # ------------------------------------------------------------------
    # Path-aware staleness detection
    # ------------------------------------------------------------------

    async def check_staleness(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        current_commit_sha: str,
        changed_paths_since_source: list[str] | None = None,
    ) -> StalenessReport:
        """Path-aware staleness detection.

        Algorithm
        ---------
        1. For each entry, check if ``affected_paths`` overlap with
           ``changed_paths_since_source`` (diff between source_commit_sha
           and current HEAD).  When no diff is available, fall back to
           SHA equality as a conservative proxy.

        2. If affected_paths overlap with changed paths:
              → verification_status = "needs_revalidation"
              → staleness_status    = "stale" (excludes from current recall)

        3. If no affected_paths are stored (legacy entries):
              → use SHA equality as before.

        4. Entries whose affected_paths do NOT overlap remain "current"
           even if HEAD changed, provided SHA has not advanced in a way
           that invalidates them.

        Non-blocking — failure must never abort a graph run.
        """
        db = get_db()
        base_filter: dict[str, Any] = {
            "user_id": user_id,
            "repository_id": repository_id,
            "branch": branch,
            "deleted_at": None,
        }

        total = await db.operational_memory.count_documents(base_filter)
        if total == 0:
            return StalenessReport(
                repository_id=repository_id,
                branch=branch,
                checked_commit_sha=current_commit_sha,
                total_entries=0,
                current_count=0,
                stale_count=0,
                needs_revalidation_count=0,
                unverifiable_count=0,
                newly_stale_count=0,
                newly_needs_revalidation_count=0,
            )

        newly_needs_revalidation = 0
        newly_stale = 0

        changed_set = set(changed_paths_since_source or [])

        if changed_set:
            # Path-aware: fetch entries with stored affected_paths and check overlap.
            async for doc in db.operational_memory.find(
                {
                    **base_filter,
                    "provenance.affected_paths": {"$exists": True, "$ne": []},
                    "verification_status": {"$in": ["verified", "partially_verified"]},
                    "superseded_by": None,
                }
            ):
                doc_affected = doc.get("provenance", {}).get("affected_paths", [])
                if not doc_affected:
                    continue
                overlap = any(p in changed_set for p in doc_affected)
                if overlap:
                    await db.operational_memory.update_one(
                        {"_id": doc["_id"]},
                        {
                            "$set": {
                                "verification_status": "needs_revalidation",
                                "staleness_status": "stale",
                            }
                        },
                    )
                    newly_needs_revalidation += 1

            # Legacy entries with no affected_paths: fall back to SHA equality.
            stale_result = await db.operational_memory.update_many(
                {
                    **base_filter,
                    "provenance.affected_paths": {"$in": [None, []]},
                    "provenance.source_commit_sha": {"$ne": current_commit_sha},
                    "staleness_status": "current",
                    "superseded_by": None,
                },
                {
                    "$set": {
                        "staleness_status": "stale",
                        "verification_status": "needs_revalidation",
                    }
                },
            )
            newly_stale = stale_result.modified_count

        else:
            # No changed-path information: use SHA equality as a conservative proxy.
            # We only stale entries where source_commit_sha is known AND differs.
            stale_result = await db.operational_memory.update_many(
                {
                    **base_filter,
                    "provenance.source_commit_sha": {"$nin": [current_commit_sha, None]},
                    "staleness_status": "current",
                    "superseded_by": None,
                },
                {
                    "$set": {
                        "staleness_status": "stale",
                        "verification_status": "needs_revalidation",
                    }
                },
            )
            newly_stale = stale_result.modified_count

        # Mark entries with no source_commit_sha as unverifiable.
        await db.operational_memory.update_many(
            {**base_filter, "provenance.source_commit_sha": None},
            {"$set": {"staleness_status": "unverifiable", "verification_status": "needs_revalidation"}},
        )

        # Refresh entries whose source SHA exactly matches current HEAD back to current.
        await db.operational_memory.update_many(
            {
                **base_filter,
                "provenance.source_commit_sha": current_commit_sha,
                "staleness_status": {"$ne": "current"},
                "superseded_by": None,
                "verification_status": {"$nin": ["invalidated", "expired", "stale"]},
            },
            {"$set": {"staleness_status": "current"}},
        )

        current_count = await db.operational_memory.count_documents({**base_filter, "staleness_status": "current"})
        stale_count = await db.operational_memory.count_documents({**base_filter, "staleness_status": "stale"})
        unverifiable_count = await db.operational_memory.count_documents(
            {**base_filter, "provenance.source_commit_sha": None}
        )
        needs_rev_count = await db.operational_memory.count_documents(
            {**base_filter, "verification_status": "needs_revalidation"}
        )

        return StalenessReport(
            repository_id=repository_id,
            branch=branch,
            checked_commit_sha=current_commit_sha,
            total_entries=total,
            current_count=current_count,
            stale_count=stale_count,
            needs_revalidation_count=needs_rev_count,
            unverifiable_count=unverifiable_count,
            newly_stale_count=newly_stale,
            newly_needs_revalidation_count=newly_needs_revalidation,
        )

    # ------------------------------------------------------------------
    # Revalidation
    # ------------------------------------------------------------------

    async def revalidate_entry(
        self,
        entry_id: str,
        *,
        user_id: str,
        new_verification_status: VerificationStatus,
        confirming_evidence_ids: list[str] | None = None,
    ) -> bool:
        """Update the verification_status of an entry after current-run revalidation.

        Only the owning user may revalidate their own entries (tenant isolation).
        Returns True if the entry was found and updated.
        """
        db = get_db()
        update_doc: dict[str, Any] = {
            "verification_status": new_verification_status,
        }
        if new_verification_status in ("verified", "partially_verified"):
            update_doc["staleness_status"] = "current"
            update_doc["last_confirmed_at"] = utc_now()
        elif new_verification_status in ("stale", "invalidated"):
            update_doc["staleness_status"] = "stale"

        set_payload: dict[str, Any] = update_doc
        inc_payload: dict[str, Any] = {}
        addtoset_payload: dict[str, Any] = {}

        if new_verification_status in ("verified", "partially_verified"):
            inc_payload["confirmed_count"] = 1
        if confirming_evidence_ids:
            addtoset_payload["provenance.evidence_ids"] = {"$each": confirming_evidence_ids}

        mongo_update: dict[str, Any] = {"$set": set_payload}
        if inc_payload:
            mongo_update["$inc"] = inc_payload
        if addtoset_payload:
            mongo_update["$addToSet"] = addtoset_payload

        result = await db.operational_memory.update_one(
            {"id": entry_id, "user_id": user_id, "deleted_at": None},
            mongo_update,
        )
        return result.modified_count > 0

    # ------------------------------------------------------------------
    # Read path — current (fresh) entries
    # ------------------------------------------------------------------

    async def recall_for_context(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        candidate_paths: list[str],
        kinds: list[OperationalMemoryKind] | None = None,
        tags: list[str] | None = None,
    ) -> list[MemorySummary]:
        """Recall fresh, path-applicable memory for primary context injection.

        Only entries with staleness_status=="current" and verification_status
        in {"verified", "partially_verified"} are returned here.

        Ranking factors (higher = surfaced first):
          1. Same affected_paths overlap with candidate_paths
          2. verification_status == "verified" over "partially_verified"
          3. confirmed_count (higher = more corroborated)
          4. last_confirmed_at (more recent first)

        Returns at most ``max_context_entries`` (default 5) summaries.
        Injected into context_pack["operational_memory_summary"].
        Callers must NOT merge these IDs with current evidence IDs.
        """
        db = get_db()
        query: dict[str, Any] = {
            "user_id": user_id,
            "repository_id": repository_id,
            "branch": branch,
            "staleness_status": "current",
            "verification_status": {"$in": ["verified", "partially_verified"]},
            "superseded_by": None,
            "deleted_at": None,
            "confidence": {"$gte": self.min_confidence},
        }

        if kinds:
            query["kind"] = {"$in": [str(k) for k in kinds]}
        if tags:
            query["tags"] = {"$elemMatch": {"$in": tags}}

        # Fetch a wider pool for Python-side ranking/filtering.
        pool_size = self.max_context_entries * 6
        cursor = db.operational_memory.find(query).sort("confirmed_count", -1).limit(pool_size)
        docs = await cursor.to_list(pool_size)

        # Apply applicability and affected_paths filters + ranking.
        scored: list[tuple[int, dict[str, Any]]] = []
        for doc in docs:
            app = MemoryApplicability.model_validate(doc.get("applicability", {}))
            if not _applicability_matches(app, candidate_paths):
                continue
            # Rank: path overlap gives +2, verified gives +1.
            doc_affected = doc.get("provenance", {}).get("affected_paths", [])
            path_overlap = _affected_paths_match(doc_affected, candidate_paths)
            score = (2 if path_overlap else 0) + (1 if doc.get("verification_status") == "verified" else 0)
            scored.append((score, doc))

        # Sort by (score DESC, confirmed_count DESC).
        scored.sort(key=lambda x: (x[0], x[1].get("confirmed_count", 1)), reverse=True)
        top_docs = [d for _, d in scored[: self.max_context_entries]]
        return [_to_summary(d) for d in top_docs]

    # ------------------------------------------------------------------
    # Read path — historical (stale / needs-revalidation) entries
    # ------------------------------------------------------------------

    async def recall_historical(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str,
        candidate_paths: list[str],
        kinds: list[OperationalMemoryKind] | None = None,
    ) -> list[MemoryMatch]:
        """Recall stale/needs-revalidation entries as historical context.

        These MUST be labelled [HISTORICAL — REQUIRES CURRENT VERIFICATION]
        by all callers.  They must never be used as current evidence.

        Returns at most ``max_historical_entries`` matches,
        ranked by recency (last_confirmed_at DESC).
        """
        db = get_db()
        query: dict[str, Any] = {
            "user_id": user_id,
            "repository_id": repository_id,
            "branch": branch,
            "staleness_status": "stale",
            "deleted_at": None,
            "superseded_by": None,
        }
        if kinds:
            query["kind"] = {"$in": [str(k) for k in kinds]}

        pool_size = self.max_historical_entries * 4
        cursor = db.operational_memory.find(query).sort("last_confirmed_at", -1).limit(pool_size)
        docs = await cursor.to_list(pool_size)

        matches: list[MemoryMatch] = []
        for doc in docs:
            app = MemoryApplicability.model_validate(doc.get("applicability", {}))
            if not _applicability_matches(app, candidate_paths):
                continue
            reasons: list[str] = ["[HISTORICAL — REQUIRES CURRENT VERIFICATION]"]
            doc_affected = doc.get("provenance", {}).get("affected_paths", [])
            if _affected_paths_match(doc_affected, candidate_paths):
                reasons.append("Relevant paths overlap with current investigation")
            matches.append(_to_match(doc, reasons))
            if len(matches) >= self.max_historical_entries:
                break

        return matches

    # ------------------------------------------------------------------
    # Operator promotion
    # ------------------------------------------------------------------

    async def promote_entry(
        self,
        *,
        user_id: str,
        operator_user_id: str,
        repository_id: str,
        branch: str,
        commit_sha: str,
        run_id: str,
        verified_evidence_ids: set[str],
        candidate: MemoryEntryCandidate,
    ) -> str | None:
        """Operator-explicit entry promotion with elevated confidence."""
        if candidate.confidence < 0.85:
            candidate = candidate.model_copy(update={"confidence": 0.85})

        valid_eids = [e for e in candidate.evidence_ids if e in verified_evidence_ids]
        allowed, reason = self.policy.check(
            candidate,
            verified_evidence_ids,
            min_confidence=0.85,
        )
        if not allowed:
            logger.warning("memory: promote_entry rejected kind=%s key=%s — %s", candidate.kind, candidate.key, reason)
            return None

        return await self._upsert_candidate(
            user_id=user_id,
            repository_id=repository_id,
            branch=branch,
            commit_sha=commit_sha,
            run_id=run_id,
            source_type="operator_promotion",
            candidate=candidate,
            valid_eids=valid_eids,
            initial_verification_status="verified",
        )

    # ------------------------------------------------------------------
    # DELIVERED_FIX → MERGED_FIX promotion
    # ------------------------------------------------------------------

    async def promote_to_merged_fix(
        self,
        *,
        entry_id: str,
        user_id: str,
        repository_id: str,
        pr_number: int | None = None,
        user_id_for_github: str | None = None,
    ) -> dict[str, Any]:
        """Attempt to promote a DELIVERED_FIX entry to MERGED_FIX.

        Flow
        ----
        1. Load the entry (tenant-scoped).
        2. Verify it is a DELIVERED_FIX not yet superseded/deleted.
        3. Look up PR status via PullRequestStatusProvider.
           - merged=True  → create a linked MERGED_FIX entry; update delivery_outcome.
           - closed, merged=False → update delivery_outcome="closed_unmerged";
             do NOT create MERGED_FIX (closed != rejected).
           - open / draft  → update delivery_outcome="open"; leave as DELIVERED_FIX.
           - GitHub unavailable → leave current state unchanged.

        Returns a result dict describing what happened.
        """
        from app.git.pr_status import PullRequestStatusProvider

        db = get_db()
        doc = await db.operational_memory.find_one(
            {"id": entry_id, "user_id": user_id, "deleted_at": None}
        )
        if not doc:
            return {"promoted": False, "reason": "entry not found or deleted"}

        if doc.get("kind") != str(OperationalMemoryKind.DELIVERED_FIX):
            return {
                "promoted": False,
                "reason": f"entry kind={doc.get('kind')} is not DELIVERED_FIX",
            }

        if doc.get("superseded_by"):
            return {"promoted": False, "reason": "entry is already superseded"}

        # Resolve PR number: from argument, from provenance, or from metadata.
        prov = doc.get("provenance") or {}
        resolved_pr = pr_number or prov.get("pr_number") or doc.get("metadata", {}).get("pr_number")

        if not resolved_pr:
            return {
                "promoted": False,
                "reason": "No PR number available for status lookup",
            }

        # Resolve which user_id to use for GitHub auth.
        github_uid = user_id_for_github or user_id

        provider = PullRequestStatusProvider()
        status = await provider.get(
            user_id=github_uid,
            repository_id=repository_id,
            pr_number=int(resolved_pr),
        )

        if status is None:
            # GitHub unavailable — leave unchanged, record attempt.
            logger.info(
                "memory: promote_to_merged_fix: GitHub unavailable for entry %s PR#%s",
                entry_id,
                resolved_pr,
            )
            return {
                "promoted": False,
                "reason": "GitHub unavailable; current delivery_outcome unchanged.",
                "pr_number": resolved_pr,
            }

        now = utc_now()

        if status.merged:
            # --- Create linked MERGED_FIX entry ---
            merged_key = f"merged_fix_{entry_id}"
            existing_merged = await db.operational_memory.find_one(
                {
                    "user_id": user_id,
                    "repository_id": doc.get("repository_id", repository_id),
                    "kind": str(OperationalMemoryKind.MERGED_FIX),
                    "key": merged_key,
                    "deleted_at": None,
                }
            )
            merged_id: str | None = None
            if existing_merged:
                merged_id = existing_merged["id"]
            else:
                new_entry = OperationalMemoryEntry(
                    id=new_id("mem_"),
                    user_id=user_id,
                    repository_id=doc.get("repository_id", repository_id),
                    branch=doc.get("branch", "main"),
                    kind=OperationalMemoryKind.MERGED_FIX,
                    key=merged_key,
                    value=doc.get("value", ""),
                    tags=doc.get("tags", []),
                    confidence=doc.get("confidence", 0.8),
                    provenance=MemoryProvenance(
                        source_type="merged_fix",
                        evidence_ids=prov.get("evidence_ids", []),
                        run_id=prov.get("run_id", ""),
                        delivery_result_id=prov.get("delivery_result_id"),
                        source_commit_sha=prov.get("source_commit_sha"),
                        affected_paths=prov.get("affected_paths", []),
                        extracted_at=now,
                        # Carry forward delivery-chain provenance
                        pr_number=int(resolved_pr),
                        pr_url=status.html_url,
                        delivered_commit_sha=prov.get("delivered_commit_sha"),
                        merge_commit_sha=status.merge_commit_sha,
                        merged_at=status.merged_at,
                    ),
                    applicability=MemoryApplicability.model_validate(
                        doc.get("applicability", {})
                    ),
                    created_at=now,
                    last_confirmed_at=now,
                    confirmed_count=1,
                    verification_status="verified",
                    staleness_status="current",
                    delivery_outcome="merged",
                    metadata={
                        "source_memory_id": entry_id,
                        "merge_commit_sha": status.merge_commit_sha,
                        "merged_at": status.merged_at.isoformat() if status.merged_at else None,
                    },
                )
                await db.operational_memory.insert_one(_to_document(new_entry))
                merged_id = new_entry.id
                logger.info(
                    "memory: created MERGED_FIX %s from DELIVERED_FIX %s (PR#%s)",
                    merged_id,
                    entry_id,
                    resolved_pr,
                )

            # Update DELIVERED_FIX delivery_outcome
            await db.operational_memory.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "delivery_outcome": "merged",
                        "provenance.pr_number": int(resolved_pr),
                        "provenance.pr_url": status.html_url,
                        "provenance.merge_commit_sha": status.merge_commit_sha,
                        "provenance.merged_at": (
                            status.merged_at.isoformat() if status.merged_at else None
                        ),
                    }
                },
            )
            return {
                "promoted": True,
                "merged_fix_id": merged_id,
                "pr_number": resolved_pr,
                "merge_commit_sha": status.merge_commit_sha,
                "merged_at": status.merged_at.isoformat() if status.merged_at else None,
            }

        elif status.state == "closed" and not status.merged:
            # Closed without merge — NOT a REJECTED_FIX (many possible reasons).
            await db.operational_memory.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "delivery_outcome": "closed_unmerged",
                        "provenance.pr_number": int(resolved_pr),
                        "provenance.pr_url": status.html_url,
                    }
                },
            )
            return {
                "promoted": False,
                "reason": "PR is closed but not merged; delivery_outcome=closed_unmerged",
                "pr_number": resolved_pr,
            }

        else:
            # Open / draft — update delivery_outcome
            outcome = "open"
            await db.operational_memory.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "delivery_outcome": outcome,
                        "provenance.pr_number": int(resolved_pr),
                        "provenance.pr_url": status.html_url,
                    }
                },
            )
            return {
                "promoted": False,
                "reason": f"PR is still {outcome}",
                "pr_number": resolved_pr,
            }

    # ------------------------------------------------------------------
    # Per-entry freshness refresh
    # ------------------------------------------------------------------

    async def refresh_entry_freshness(
        self,
        *,
        entry_id: str,
        user_id: str,
        current_sha: str,
        repo_path: Path | None = None,
        repository_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate and apply freshness for a single entry on demand.

        Returns a dict describing the freshness result.
        This is used by the /refresh API endpoint.
        """
        from app.db.mongo import get_db as _get_db
        from app.memory.freshness import MemoryFreshnessService

        db = _get_db()
        doc = await db.operational_memory.find_one(
            {"id": entry_id, "user_id": user_id, "deleted_at": None}
        )
        if not doc:
            return {"refreshed": False, "reason": "entry not found"}

        repo_id = repository_id or doc.get("repository_id", "")
        svc = MemoryFreshnessService()
        result = await svc.evaluate(
            memory_id=entry_id,
            kind=OperationalMemoryKind(doc.get("kind", "repository_fact")),
            source_commit_sha=(doc.get("provenance") or {}).get("source_commit_sha"),
            affected_paths=(doc.get("provenance") or {}).get("affected_paths") or [],
            current_sha=current_sha,
            repo_path=repo_path,
            user_id=user_id,
            repository_id=repo_id,
        )
        updated = await svc.apply_to_db(result, user_id=user_id, current_sha=current_sha)
        return {
            "refreshed": updated,
            "freshness": result.freshness,
            "verification_status": result.recommended_verification_status,
            "reason": result.reason,
            "source": result.source,
            "changed_relevant_paths": result.changed_relevant_paths,
        }

    # ------------------------------------------------------------------
    # Admin / GC
    # ------------------------------------------------------------------

    async def list_entries(
        self,
        *,
        user_id: str,
        repository_id: str,
        branch: str | None = None,
        include_stale: bool = False,
        limit: int = 50,
    ) -> list[OperationalMemoryEntry]:
        """List entries for a repository.  Always scoped to user_id."""
        db = get_db()
        query: dict[str, Any] = {
            "user_id": user_id,
            "repository_id": repository_id,
            "deleted_at": None,
        }
        if branch:
            query["branch"] = branch
        if not include_stale:
            query["staleness_status"] = "current"

        cursor = db.operational_memory.find(query).sort("last_confirmed_at", -1).limit(limit)
        docs = await cursor.to_list(limit)
        return [OperationalMemoryEntry.model_validate(d) for d in docs]

    async def delete_entry(self, entry_id: str, *, user_id: str) -> bool:
        """Soft-delete a single entry.  Only the owning user may delete."""
        db = get_db()
        result = await db.operational_memory.update_one(
            {"id": entry_id, "user_id": user_id, "deleted_at": None},
            {"$set": {"deleted_at": utc_now()}},
        )
        return result.modified_count > 0

    async def expire_stale(
        self,
        *,
        user_id: str,
        repository_id: str,
        stale_ttl_days: int = 90,
    ) -> int:
        """Soft-delete stale entries older than stale_ttl_days.

        GC ONLY — must never be called during an active graph run.
        """
        from datetime import timedelta

        db = get_db()
        cutoff = utc_now() - timedelta(days=stale_ttl_days)
        result = await db.operational_memory.update_many(
            {
                "user_id": user_id,
                "repository_id": repository_id,
                "staleness_status": "stale",
                "last_confirmed_at": {"$lt": cutoff},
                "deleted_at": None,
            },
            {
                "$set": {
                    "deleted_at": utc_now(),
                    "verification_status": "expired",
                }
            },
        )
        return result.modified_count
