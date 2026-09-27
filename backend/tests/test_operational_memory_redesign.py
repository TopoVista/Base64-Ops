"""Tests for the redesigned operational memory subsystem.

Covers requirements from the Operational Memory redesign pass:

1.  Write-policy gate — per-kind rules (DELIVERED_FIX, MERGED_FIX, etc.)
2.  Semantic kind lifecycle — OperationalMemoryKind enum values
3.  delivered != merged (DELIVERED_FIX does not imply MERGED_FIX)
4.  Draft PR / push do NOT produce MERGED_FIX
5.  INCIDENT_OUTCOME does not require delivery
6.  REJECTED_FIX records the rejection, not "this is wrong"
7.  USER_CORRECTION requires explicit statement
8.  OPERATIONAL_PREFERENCE requires explicit preference (not inferred)
9.  Path-aware staleness — only affected paths trigger needs_revalidation
10. Stale history remains visible via recall_historical
11. Stale history is labelled, never used as current evidence
12. Current evidence precedence over memory (architectural enforcement)
13. Memory cannot authorize mutation (write-path gates)
14. Cross-user memory access denied (tenant isolation)
15. Secrets are not persisted (redaction gate)
16. Learning disabled (memory_enabled=False) prevents writes
17. Deduplication uses semantic identity
18. Recurrence: historical cause no longer applies
19. Recurrence: historical cause does apply → still requires fresh evidence
20. Tags are stored and recalled correctly
"""

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.memory_service import MemoryService, MemoryWritePolicy
from app.memory.models import (
    MemoryEntryCandidate,
    MemoryProvenance,
    OperationalMemoryEntry,
    OperationalMemoryKind,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _candidate(
    kind: OperationalMemoryKind = OperationalMemoryKind.REPOSITORY_FACT,
    key: str = "k",
    value: str = "v",
    confidence: float = 0.75,
    evidence_ids: list[str] | None = None,
    tags: list[str] | None = None,
    affected_paths: list[str] | None = None,
) -> MemoryEntryCandidate:
    # Use sentinel to distinguish "not provided" from explicit empty list
    eids = ["evd_001"] if evidence_ids is None else evidence_ids
    return MemoryEntryCandidate(
        kind=kind,
        key=key,
        value=value,
        confidence=confidence,
        evidence_ids=eids,
        tags=tags or [],
        affected_paths=affected_paths or [],
    )


def _make_doc(
    entry_id: str = "mem_001",
    kind: str = "repository_fact",
    key: str = "k",
    value: str = "v",
    commit_sha: str = "abc",
    staleness_status: str = "stale",
    verification_status: str = "needs_revalidation",
    affected_paths: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    now = _now()
    return {
        "_id": entry_id,
        "id": entry_id,
        "user_id": "user_1",
        "repository_id": "https://github.com/test/repo",
        "branch": "main",
        "commit_sha": commit_sha,
        "kind": kind,
        "key": key,
        "value": value,
        "tags": tags or [],
        "confidence": 0.75,
        "provenance": {
            "source_type": "delivery_result",
            "evidence_ids": ["evd_001"],
            "run_id": "run_001",
            "delivery_result_id": "dlr_001",
            "source_commit_sha": commit_sha,
            "affected_paths": affected_paths or [],
            "operator_user_id": None,
            "extracted_at": now.isoformat(),
        },
        "applicability": {
            "path_prefix": None,
            "glob_patterns": [],
            "language": None,
            "framework": None,
        },
        "created_at": now.isoformat(),
        "last_confirmed_at": now.isoformat(),
        "confirmed_count": 2,
        "verification_status": verification_status,
        "staleness_status": staleness_status,
        "superseded_by": None,
        "deleted_at": None,
        "metadata": {},
    }


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


def _cursor_from(docs):
    class _C:
        def sort(self, *a, **kw):
            return self

        def limit(self, n):
            self._n = n
            return self

        async def to_list(self, n):
            return docs[:n]

        def __aiter__(self):
            return _AsyncIter(list(docs))

    return _C()


# ---------------------------------------------------------------------------
# 1. Write-policy gate — per-kind rules
# ---------------------------------------------------------------------------


class TestWritePolicyGate:
    def test_delivered_fix_requires_delivery_result_id(self):
        c = _candidate(kind=OperationalMemoryKind.DELIVERED_FIX)
        ok, reason = MemoryWritePolicy.check(c, {"evd_001"}, delivery_result_id=None)
        assert not ok
        assert "delivery_result_id" in reason.lower() or "DELIVERED_FIX" in reason

    def test_delivered_fix_allowed_with_delivery_result_id(self):
        c = _candidate(kind=OperationalMemoryKind.DELIVERED_FIX)
        ok, _ = MemoryWritePolicy.check(c, {"evd_001"}, delivery_result_id="dlr_001")
        assert ok

    def test_merged_fix_requires_github_confirmation(self):
        c = _candidate(kind=OperationalMemoryKind.MERGED_FIX)
        ok, reason = MemoryWritePolicy.check(c, {"evd_001"}, github_merge_confirmed=False)
        assert not ok
        assert "merge" in reason.lower() or "MERGED_FIX" in reason

    def test_merged_fix_draft_pr_not_sufficient(self):
        """Draft PR creation must NOT qualify as github_merge_confirmed."""
        c = _candidate(kind=OperationalMemoryKind.MERGED_FIX)
        # Simulate: push succeeded, draft PR created — but no merge confirmation
        ok, _ = MemoryWritePolicy.check(c, {"evd_001"}, github_merge_confirmed=False)
        assert not ok

    def test_merged_fix_allowed_with_github_confirmation(self):
        c = _candidate(kind=OperationalMemoryKind.MERGED_FIX)
        ok, _ = MemoryWritePolicy.check(c, {"evd_001"}, github_merge_confirmed=True)
        assert ok

    def test_rejected_fix_requires_rejection_confirmed(self):
        c = _candidate(kind=OperationalMemoryKind.REJECTED_FIX)
        ok, reason = MemoryWritePolicy.check(c, {"evd_001"}, rejection_confirmed=False)
        assert not ok
        assert "rejection" in reason.lower() or "REJECTED_FIX" in reason

    def test_rejected_fix_allowed_with_rejection_confirmed(self):
        c = _candidate(kind=OperationalMemoryKind.REJECTED_FIX)
        ok, _ = MemoryWritePolicy.check(c, {"evd_001"}, rejection_confirmed=True)
        assert ok

    def test_user_correction_requires_explicit_statement(self):
        c = _candidate(kind=OperationalMemoryKind.USER_CORRECTION, evidence_ids=[])
        ok, reason = MemoryWritePolicy.check(c, set(), is_explicit_user_statement=False)
        assert not ok

    def test_user_correction_allowed_with_explicit_statement(self):
        c = _candidate(kind=OperationalMemoryKind.USER_CORRECTION, evidence_ids=[])
        ok, _ = MemoryWritePolicy.check(c, set(), is_explicit_user_statement=True)
        assert ok

    def test_operational_preference_requires_explicit_preference(self):
        c = _candidate(kind=OperationalMemoryKind.OPERATIONAL_PREFERENCE, evidence_ids=[])
        ok, reason = MemoryWritePolicy.check(c, set(), is_explicit_preference=False)
        assert not ok
        assert "preference" in reason.lower() or "OPERATIONAL_PREFERENCE" in reason

    def test_operational_preference_allowed_with_explicit_preference(self):
        c = _candidate(kind=OperationalMemoryKind.OPERATIONAL_PREFERENCE, evidence_ids=[])
        ok, _ = MemoryWritePolicy.check(c, set(), is_explicit_preference=True)
        assert ok

    def test_repository_fact_requires_evidence(self):
        c = _candidate(kind=OperationalMemoryKind.REPOSITORY_FACT, evidence_ids=[])
        ok, reason = MemoryWritePolicy.check(c, {"evd_001"})
        assert not ok
        assert "evidence" in reason.lower()

    def test_low_confidence_rejected_regardless_of_kind(self):
        for kind in OperationalMemoryKind:
            c = _candidate(kind=kind, confidence=0.3, evidence_ids=["evd_001"])
            ok, reason = MemoryWritePolicy.check(
                c,
                {"evd_001"},
                delivery_result_id="dlr_001",
                github_merge_confirmed=True,
                rejection_confirmed=True,
                is_explicit_user_statement=True,
                is_explicit_preference=True,
                min_confidence=0.6,
            )
            assert not ok, f"Expected rejection for kind={kind} with low confidence"
            assert "confidence" in reason.lower()


# ---------------------------------------------------------------------------
# 2. Delivered != Merged
# ---------------------------------------------------------------------------


class TestDeliveredVsMerged:
    @pytest.mark.asyncio
    async def test_delivered_fix_stored_for_successful_delivery(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(kind=OperationalMemoryKind.DELIVERED_FIX, key="fix_binding")
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_from_delivery(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
            )
        assert len(ids) == 1
        assert inserted[0]["kind"] == "delivered_fix"

    @pytest.mark.asyncio
    async def test_merged_fix_not_stored_without_github_confirmation(self):
        svc = MemoryService()
        mock_db = AsyncMock()
        mock_db.operational_memory.insert_one = AsyncMock()

        c = _candidate(kind=OperationalMemoryKind.MERGED_FIX, key="fix_merged")
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_merged_fix(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
                github_merge_confirmed=False,
            )
        assert ids == []
        mock_db.operational_memory.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_merged_fix_stored_with_github_confirmation(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(kind=OperationalMemoryKind.MERGED_FIX, key="fix_merged")
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_merged_fix(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
                github_merge_confirmed=True,
            )
        assert len(ids) == 1
        assert inserted[0]["kind"] == "merged_fix"


# ---------------------------------------------------------------------------
# 3. INCIDENT_OUTCOME — no delivery required
# ---------------------------------------------------------------------------


class TestIncidentOutcome:
    @pytest.mark.asyncio
    async def test_incident_outcome_stored_without_delivery(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(
            kind=OperationalMemoryKind.INCIDENT_OUTCOME,
            key="502_caused_by_binding",
            value="FastAPI bound to 127.0.0.1 instead of 0.0.0.0",
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_incident_outcome(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
            )
        assert len(ids) == 1
        assert inserted[0]["kind"] == "incident_outcome"

    @pytest.mark.asyncio
    async def test_incident_outcome_without_evidence_rejected(self):
        svc = MemoryService()
        mock_db = AsyncMock()
        mock_db.operational_memory.insert_one = AsyncMock()

        c = _candidate(
            kind=OperationalMemoryKind.INCIDENT_OUTCOME,
            key="unverified_guess",
            evidence_ids=[],  # No evidence
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_incident_outcome(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
            )
        assert ids == []


# ---------------------------------------------------------------------------
# 4. REJECTED_FIX — records rejection, not "fix is wrong"
# ---------------------------------------------------------------------------


class TestRejectedFix:
    @pytest.mark.asyncio
    async def test_rejected_fix_stored_on_rejection(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(
            kind=OperationalMemoryKind.REJECTED_FIX,
            key="rejected_binding_fix",
            value="Proposed change to main.py rejected by user",
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_rejected_fix(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
            )
        assert len(ids) == 1
        assert inserted[0]["kind"] == "rejected_fix"


# ---------------------------------------------------------------------------
# 5. USER_CORRECTION and OPERATIONAL_PREFERENCE
# ---------------------------------------------------------------------------


class TestUserCorrectionAndPreference:
    @pytest.mark.asyncio
    async def test_user_correction_stored_with_explicit_statement(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(
            kind=OperationalMemoryKind.USER_CORRECTION,
            key="compose_location",
            value="docker-compose.yml is in infra/ not root",
            evidence_ids=[],
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_user_correction(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                candidates=[c],
            )
        assert len(ids) == 1
        assert inserted[0]["kind"] == "user_correction"

    @pytest.mark.asyncio
    async def test_operational_preference_stored_with_explicit_preference(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(
            kind=OperationalMemoryKind.OPERATIONAL_PREFERENCE,
            key="always_draft_pr",
            value="Always create draft PRs, never merge directly",
            evidence_ids=[],
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_operational_preference(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                candidates=[c],
            )
        assert len(ids) == 1
        assert inserted[0]["kind"] == "operational_preference"


# ---------------------------------------------------------------------------
# 6. Path-aware staleness
# ---------------------------------------------------------------------------


class TestPathAwareStaleness:
    @pytest.mark.asyncio
    async def test_unaffected_path_not_staled_when_changed_paths_provided(self):
        """Entry with affected_paths=['frontend/'] must NOT be staled when
        only backend/ changed."""
        svc = MemoryService()

        # Entry only affects frontend/
        _make_doc(
            entry_id="mem_front",
            staleness_status="current",
            verification_status="verified",
            affected_paths=["frontend/src/api.ts"],
        )

        update_one_calls: list[tuple] = []

        async def cap_update_one(flt, upd):
            update_one_calls.append((flt, upd))

        async def cap_update_many(flt, upd):
            r = MagicMock()
            r.modified_count = 0
            return r

        mock_db = AsyncMock()
        mock_db.operational_memory.count_documents = AsyncMock(
            side_effect=[
                1,
                1,
                0,
                0,
                0,
            ]
        )
        mock_db.operational_memory.update_one = AsyncMock(side_effect=cap_update_one)
        mock_db.operational_memory.update_many = AsyncMock(side_effect=cap_update_many)
        mock_db.operational_memory.find = MagicMock(
            # No overlap entries — the single entry doesn't match changed paths
            return_value=_cursor_from([])
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.check_staleness(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                current_commit_sha="new_sha",
                # Only backend changed
                changed_paths_since_source=["backend/app/main.py"],
            )

        # update_one must not have been called to stale the frontend entry
        stale_one_calls = [(f, u) for f, u in update_one_calls if u.get("$set", {}).get("staleness_status") == "stale"]
        assert len(stale_one_calls) == 0, "Frontend entry should NOT have been staled when only backend paths changed"

    @pytest.mark.asyncio
    async def test_affected_path_staled_when_changed(self):
        """Entry with affected_paths=['backend/app/main.py'] MUST be staled
        when backend/app/main.py is in the changed set."""
        svc = MemoryService()

        entry_doc = _make_doc(
            entry_id="mem_backend",
            staleness_status="current",
            verification_status="verified",
            affected_paths=["backend/app/main.py"],
        )

        update_one_calls: list[tuple] = []

        async def cap_update_one(flt, upd):
            update_one_calls.append((flt, upd))

        async def cap_update_many(flt, upd):
            r = MagicMock()
            r.modified_count = 0
            return r

        mock_db = AsyncMock()
        mock_db.operational_memory.count_documents = AsyncMock(
            side_effect=[
                1,
                1,
                0,
                0,
                0,
            ]
        )
        mock_db.operational_memory.update_one = AsyncMock(side_effect=cap_update_one)
        mock_db.operational_memory.update_many = AsyncMock(side_effect=cap_update_many)
        # Return the entry so the async-for loop processes it
        mock_db.operational_memory.find = MagicMock(return_value=_cursor_from([entry_doc]))

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.check_staleness(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                current_commit_sha="new_sha",
                changed_paths_since_source=["backend/app/main.py"],
            )

        stale_one_calls = [(f, u) for f, u in update_one_calls if u.get("$set", {}).get("staleness_status") == "stale"]
        assert len(stale_one_calls) == 1, "Backend entry SHOULD have been staled"
        assert stale_one_calls[0][1]["$set"]["verification_status"] == "needs_revalidation"


# ---------------------------------------------------------------------------
# 7. Stale history remains visible
# ---------------------------------------------------------------------------


class TestHistoricalRecall:
    @pytest.mark.asyncio
    async def test_recall_historical_returns_stale_entries(self):
        svc = MemoryService()
        stale_doc = _make_doc(
            staleness_status="stale",
            verification_status="needs_revalidation",
        )

        mock_db = MagicMock()
        mock_db.operational_memory.find = MagicMock(return_value=_cursor_from([stale_doc]))

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            matches = await svc.recall_historical(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                candidate_paths=[],
            )

        assert len(matches) == 1
        assert matches[0].freshness in ("stale", "needs_revalidation")
        assert any("[HISTORICAL" in r for r in matches[0].relevance_reasons)

    @pytest.mark.asyncio
    async def test_recall_for_context_excludes_stale(self):
        """recall_for_context must only return current entries."""
        svc = MemoryService()

        class _EmptyCursor:
            def sort(self, *a, **kw):
                return self

            def limit(self, n):
                return self

            async def to_list(self, n):
                return []

        captured: dict = {}

        def cap_find(q):
            captured.update(q)
            return _EmptyCursor()

        mock_db = MagicMock()
        mock_db.operational_memory.find = cap_find

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.recall_for_context(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc",
                candidate_paths=[],
            )

        assert captured.get("staleness_status") == "current"
        assert captured.get("superseded_by") is None


# ---------------------------------------------------------------------------
# 8. Memory cannot authorize mutation
# ---------------------------------------------------------------------------


class TestMemoryCannotAuthorizeMutation:
    """Memory entries must never be sufficient to authorize any mutation.

    These tests verify that:
    - The write-path methods don't expose approval APIs.
    - record_from_delivery, record_merged_fix etc. all require gated evidence.
    - The MemoryService does not expose .approve(), .authorize(), or
      .create_approval() methods.
    """

    def test_memory_service_has_no_approve_method(self):
        svc = MemoryService()
        assert not hasattr(svc, "approve"), "MemoryService must not expose an approve() method"
        assert not hasattr(svc, "authorize"), "MemoryService must not expose an authorize() method"
        assert not hasattr(svc, "create_approval"), "MemoryService must not expose create_approval()"

    def test_memory_entry_has_no_approval_weight_field(self):
        now = _now()
        entry = OperationalMemoryEntry(
            id="mem_001",
            user_id="u1",
            repository_id="repo1",
            branch="main",
            kind=OperationalMemoryKind.DELIVERED_FIX,
            key="some_fix",
            value="value",
            provenance=MemoryProvenance(
                source_type="delivery_result",
                evidence_ids=["evd_001"],
                run_id="run_001",
                extracted_at=now,
            ),
            created_at=now,
            last_confirmed_at=now,
        )
        # Entry must have no field that could represent approval weight
        dump = entry.model_dump()
        for field in ("approval_weight", "authorized", "can_deploy", "bypass_approval"):
            assert field not in dump, f"Unexpected field {field!r} found in OperationalMemoryEntry"

    @pytest.mark.asyncio
    async def test_merged_fix_does_not_bypass_delivery_flow(self):
        """Even a confirmed MERGED_FIX cannot bypass the delivery/approval flow.
        The service stores facts, it never returns an approval record."""
        svc = MemoryService()

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock()

        c = _candidate(kind=OperationalMemoryKind.MERGED_FIX, key="prev_fix")
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            result = await svc.record_merged_fix(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
                github_merge_confirmed=True,
            )

        # Returns a list of string IDs, not an ApprovalRecord
        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], str)
            assert not result[0].startswith("apr_"), "record_merged_fix must not return an ApprovalRecord ID"


# ---------------------------------------------------------------------------
# 9. Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_recall_is_scoped_to_user_id(self):
        """recall_for_context must always include user_id in the query."""
        svc = MemoryService()
        captured: dict = {}

        class _C:
            def sort(self, *a, **kw):
                return self

            def limit(self, n):
                return self

            async def to_list(self, n):
                return []

        def cap_find(q):
            captured.update(q)
            return _C()

        mock_db = MagicMock()
        mock_db.operational_memory.find = cap_find

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.recall_for_context(
                user_id="user_alice",
                repository_id="repo1",
                branch="main",
                commit_sha="abc",
                candidate_paths=[],
            )

        assert captured.get("user_id") == "user_alice"

    @pytest.mark.asyncio
    async def test_recall_historical_is_scoped_to_user_id(self):
        svc = MemoryService()
        captured: dict = {}

        mock_db = MagicMock()
        mock_db.operational_memory.find = MagicMock(return_value=_cursor_from([]))

        # Patch find to capture query
        original_find = mock_db.operational_memory.find

        def cap_find(q):
            captured.update(q)
            return original_find(q)

        mock_db.operational_memory.find = cap_find

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.recall_historical(
                user_id="user_bob",
                repository_id="repo1",
                branch="main",
                candidate_paths=[],
            )

        assert captured.get("user_id") == "user_bob"

    @pytest.mark.asyncio
    async def test_delete_entry_scoped_to_user_id(self):
        """delete_entry must include user_id in the filter so cross-user deletion is impossible."""
        svc = MemoryService()
        update_filters: list[dict] = []

        async def cap_update_one(flt, upd):
            update_filters.append(flt)
            r = MagicMock()
            r.modified_count = 1
            return r

        mock_db = AsyncMock()
        mock_db.operational_memory.update_one = AsyncMock(side_effect=cap_update_one)

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.delete_entry("mem_001", user_id="user_alice")

        assert update_filters[0].get("user_id") == "user_alice"


# ---------------------------------------------------------------------------
# 10. Secrets not persisted
# ---------------------------------------------------------------------------


class TestSecretsNotPersisted:
    @pytest.mark.asyncio
    async def test_secret_value_redacted_before_storage(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        # Use a value pattern that RedactionService recognises (password= prefix)
        c = _candidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="env_val",
            value="password=super_secret_value_12345",
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.record_from_delivery(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
            )

        assert len(inserted) == 1
        stored_value = inserted[0].get("value", "")
        # The redaction service replaces the secret portion with [REDACTED]
        assert "super_secret_value_12345" not in stored_value, "Secret value must be redacted before storage"
        assert "REDACTED" in stored_value


# ---------------------------------------------------------------------------
# 11. Tags stored and recalled
# ---------------------------------------------------------------------------


class TestTags:
    @pytest.mark.asyncio
    async def test_tags_persisted_in_document(self):
        svc = MemoryService()
        inserted: list[dict] = []

        async def cap_insert(doc):
            inserted.append(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=cap_insert)

        c = _candidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="exposed_port_8001",
            value="8001",
            tags=["port", "docker", "runtime"],
        )
        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.record_from_delivery(
                user_id="u1",
                repository_id="repo1",
                branch="main",
                commit_sha="sha1",
                run_id="run1",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_001"},
                candidates=[c],
            )

        assert "port" in inserted[0]["tags"]
        assert "docker" in inserted[0]["tags"]
        assert "runtime" in inserted[0]["tags"]


# ---------------------------------------------------------------------------
# 12. Recurrence test A — historical cause no longer applies
# ---------------------------------------------------------------------------


class TestRecurrenceHistoricalCauseNoLongerApplies:
    @pytest.mark.asyncio
    async def test_stale_historical_entry_does_not_auto_apply(self):
        """Historical memory:  502 caused by 127.0.0.1 binding.
        Current source already has 0.0.0.0 binding.
        Expected: historical entry is returned as stale/historical context,
        but current evidence takes precedence and must be used for diagnosis."""
        svc = MemoryService()

        # Historical entry says binding was 127.0.0.1
        historical_doc = _make_doc(
            entry_id="mem_old_binding",
            kind="incident_outcome",
            key="502_cause",
            value="FastAPI bound to 127.0.0.1",
            staleness_status="stale",
            verification_status="needs_revalidation",
        )

        mock_db = MagicMock()
        mock_db.operational_memory.find = MagicMock(return_value=_cursor_from([historical_doc]))

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            matches = await svc.recall_historical(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                candidate_paths=["backend/app/main.py"],
            )

        # Historical entry IS returned (it's visible, not hidden)
        assert len(matches) == 1

        # But it is clearly marked as historical / needing revalidation
        assert matches[0].freshness in ("stale", "needs_revalidation")
        assert matches[0].verification_status == "needs_revalidation"
        assert any("[HISTORICAL" in r for r in matches[0].relevance_reasons)

        # It is NOT in recall_for_context (current evidence pool)
        captured_current: dict = {}

        class _EmptyCursor:
            def sort(self, *a, **kw):
                return self

            def limit(self, n):
                return self

            async def to_list(self, n):
                return []

        def cap_find_current(q):
            captured_current.update(q)
            return _EmptyCursor()

        mock_db_current = MagicMock()
        mock_db_current.operational_memory.find = cap_find_current

        with patch("app.memory.memory_service.get_db", return_value=mock_db_current):
            current_matches = await svc.recall_for_context(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="current_sha",
                candidate_paths=["backend/app/main.py"],
            )

        # Current recall must query for staleness_status == "current"
        assert captured_current.get("staleness_status") == "current"
        assert current_matches == []  # Empty because cursor is empty


# ---------------------------------------------------------------------------
# 13. Recurrence test B — historical cause does apply (same typo again)
# ---------------------------------------------------------------------------


class TestRecurrenceHistoricalCauseStillApplies:
    @pytest.mark.asyncio
    async def test_same_failure_pattern_requires_fresh_evidence(self):
        """Previous incident: Compose service typo.
        Current repository has the same typo again.
        Expected: historical memory is retrieved AND labelled, but the
        fix still requires new current evidence + new PatchProposal."""
        svc = MemoryService()

        # Historical entry: previous typo fix was delivered
        historical_doc = _make_doc(
            entry_id="mem_typo_fix",
            kind="delivered_fix",
            key="compose_typo_db_service",
            value="db_seryice → db_service",
            staleness_status="stale",
            verification_status="needs_revalidation",
            affected_paths=["docker-compose.yml"],
        )

        mock_db = MagicMock()
        mock_db.operational_memory.find = MagicMock(return_value=_cursor_from([historical_doc]))

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            matches = await svc.recall_historical(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                candidate_paths=["docker-compose.yml"],
            )

        # Historical entry is visible as context
        assert len(matches) == 1
        assert matches[0].key == "compose_typo_db_service"
        assert matches[0].freshness in ("stale", "needs_revalidation")

        # The match carries relevance reasons indicating current verification is required
        assert any("[HISTORICAL" in r for r in matches[0].relevance_reasons)
        # But it does NOT grant any approval capability
        assert not hasattr(matches[0], "approval_hash")
        assert not hasattr(matches[0], "diff_hash")


# ---------------------------------------------------------------------------
# 14. OperationalMemoryKind enum completeness
# ---------------------------------------------------------------------------


class TestKindEnum:
    def test_all_expected_kinds_present(self):
        expected = {
            "repository_fact",
            "incident_outcome",
            "delivered_fix",
            "merged_fix",
            "rejected_fix",
            "user_correction",
            "operational_preference",
        }
        actual = {k.value for k in OperationalMemoryKind}
        assert actual == expected

    def test_kind_is_str_enum(self):
        import enum

        assert issubclass(OperationalMemoryKind, str)
        assert issubclass(OperationalMemoryKind, enum.Enum)

    def test_old_technical_kinds_not_present(self):
        """Implementation-detail kinds must NOT exist as enum members."""
        old_kinds = {"command_fact", "port_fact", "env_fact", "ci_fact", "dependency_fact", "path_fact", "repo_fact"}
        actual = {k.value for k in OperationalMemoryKind}
        overlap = old_kinds & actual
        assert not overlap, f"Old implementation-detail kinds found: {overlap}"
