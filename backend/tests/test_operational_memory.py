"""Unit tests for the repository-scoped operational memory subsystem.

Tests are grouped into:
1. Schema / serialization
2. Provenance gate (no evidence → rejected)
3. Confidence threshold filtering
4. Redaction of values before persistence
5. Deduplication and confirmed_count increment
6. Supersession (conflicting value → new entry + superseded_by linkage)
7. Staleness detection (commit_sha mismatch → stale)
8. Context recall exclusion of stale entries
9. Path applicability filtering
10. Context cap enforcement (max_context_entries)
"""

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.memory_service import MemoryService, _applicability_matches
from app.memory.models import (
    MemoryApplicability,
    MemoryEntryCandidate,
    MemoryProvenance,
    OperationalMemoryEntry,
    OperationalMemoryKind,
    StalenessReport,
)

# Keep the old MemoryKind alias so call-sites that import it still work
MemoryKind = OperationalMemoryKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _make_entry(
    *,
    entry_id: str = "mem_001",
    kind: str = "repository_fact",
    key: str = "test_command",
    value: str = "pytest",
    commit_sha: str = "abc123",
    staleness_status: str = "current",
    superseded_by: str | None = None,
    confidence: float = 0.75,
    evidence_ids: list[str] | None = None,
    path_prefix: str | None = None,
    glob_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Build a raw MongoDB document for a memory entry."""
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
        "tags": [],
        "confidence": confidence,
        "provenance": {
            "source_type": "delivery_result",
            "evidence_ids": evidence_ids or ["evd_001"],
            "run_id": "run_001",
            "delivery_result_id": "dlr_001",
            "source_commit_sha": commit_sha,
            "affected_paths": [],
            "operator_user_id": None,
            "extracted_at": now.isoformat(),
        },
        "applicability": {
            "path_prefix": path_prefix,
            "glob_patterns": glob_patterns or [],
            "language": None,
            "framework": None,
        },
        "created_at": now.isoformat(),
        "last_confirmed_at": now.isoformat(),
        "confirmed_count": 1,
        "verification_status": "verified",
        "staleness_status": staleness_status,
        "superseded_by": superseded_by,
        "deleted_at": None,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# 1. Schema / serialization
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_operational_memory_entry_round_trip(self):
        now = _now()
        entry = OperationalMemoryEntry(
            id="mem_001",
            user_id="user_1",
            repository_id="https://github.com/test/repo",
            branch="main",
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="test_command",
            value="pytest",
            confidence=0.75,
            provenance=MemoryProvenance(
                source_type="delivery_result",
                evidence_ids=["evd_001"],
                run_id="run_001",
                delivery_result_id="dlr_001",
                source_commit_sha="abc123",
                extracted_at=now,
            ),
            applicability=MemoryApplicability(glob_patterns=["*.py"]),
            created_at=now,
            last_confirmed_at=now,
            confirmed_count=1,
            verification_status="verified",
            staleness_status="current",
        )
        dumped = entry.model_dump()
        restored = OperationalMemoryEntry.model_validate(dumped)
        assert restored.id == "mem_001"
        assert restored.kind == OperationalMemoryKind.REPOSITORY_FACT
        assert restored.provenance.evidence_ids == ["evd_001"]
        assert restored.applicability.glob_patterns == ["*.py"]
        assert restored.staleness_status == "current"
        assert restored.verification_status == "verified"

    def test_memory_entry_candidate_defaults(self):
        c = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="exposed_port_8080",
            value="8080",
        )
        assert c.confidence == 0.7
        assert c.evidence_ids == []
        assert c.applicability.glob_patterns == []
        assert c.tags == []
        assert c.affected_paths == []


# ---------------------------------------------------------------------------
# 2. Provenance gate
# ---------------------------------------------------------------------------


class TestProvenanceGate:
    @pytest.mark.asyncio
    async def test_candidate_without_verified_evidence_is_dropped(self):
        """A candidate whose evidence_ids are not in verified_evidence_ids must be dropped."""
        svc = MemoryService()
        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock()

        candidate = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="test_command",
            value="pytest",
            evidence_ids=["evd_unverified"],  # Not in verified set
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_from_delivery(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc123",
                run_id="run_001",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_verified"},  # Different from candidate
                candidates=[candidate],
            )

        assert ids == []
        mock_db.operational_memory.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidate_with_empty_evidence_ids_is_dropped(self):
        svc = MemoryService()
        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock()

        candidate = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="test_command",
            value="pytest",
            evidence_ids=[],
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_from_delivery(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc123",
                run_id="run_001",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_001"},
                candidates=[candidate],
            )

        assert ids == []


# ---------------------------------------------------------------------------
# 3. Confidence threshold filtering
# ---------------------------------------------------------------------------


class TestConfidenceThreshold:
    @pytest.mark.asyncio
    async def test_low_confidence_candidate_is_dropped(self):
        svc = MemoryService(min_confidence=0.7)
        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock()

        candidate = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="env_var_port",
            value="PORT",
            confidence=0.5,  # Below threshold
            evidence_ids=["evd_001"],
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_from_delivery(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc123",
                run_id="run_001",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_001"},
                candidates=[candidate],
            )

        assert ids == []


# ---------------------------------------------------------------------------
# 4. Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    @pytest.mark.asyncio
    async def test_credential_shaped_value_is_redacted_before_persistence(self):
        """A value containing a secret-shaped pattern must be redacted."""
        svc = MemoryService()
        inserted_doc: dict = {}

        async def capture_insert(doc):
            inserted_doc.update(doc)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=None)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=capture_insert)

        # A value that looks like an API key — should be redacted
        candidate = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="env_var_openai_api_key",
            value="sk-abcdefghijklmnopqrstuvwxyz123456",
            confidence=0.75,
            evidence_ids=["evd_001"],
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.record_from_delivery(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc123",
                run_id="run_001",
                delivery_result_id="dlr_001",
                verified_evidence_ids={"evd_001"},
                candidates=[candidate],
            )

        # Value must have been redacted
        assert "sk-" not in inserted_doc.get("value", "")
        assert "REDACTED" in inserted_doc.get("value", "")


# ---------------------------------------------------------------------------
# 5. Deduplication — same value → confirmed_count increment
# ---------------------------------------------------------------------------


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_same_value_increments_confirmed_count(self):
        """Re-recording the same (kind, key, value) must increment confirmed_count."""
        svc = MemoryService()
        existing = _make_entry(value="pytest", commit_sha="old_sha")

        update_calls: list = []

        async def capture_update(filter_, update):
            update_calls.append(update)

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=existing)
        mock_db.operational_memory.update_one = AsyncMock(side_effect=capture_update)

        candidate = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="test_command",
            value="pytest",
            confidence=0.75,
            evidence_ids=["evd_001"],
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_from_delivery(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="new_sha",
                run_id="run_002",
                delivery_result_id="dlr_002",
                verified_evidence_ids={"evd_001"},
                candidates=[candidate],
            )

        assert ids == [existing["id"]]
        # Must have issued an update (not an insert)
        assert any("$inc" in call for call in update_calls)
        inc_op = next(c for c in update_calls if "$inc" in c)
        assert inc_op["$inc"]["confirmed_count"] == 1


# ---------------------------------------------------------------------------
# 6. Supersession — conflicting value
# ---------------------------------------------------------------------------


class TestSupersession:
    @pytest.mark.asyncio
    async def test_conflicting_value_creates_new_entry_and_supersedes_old(self):
        """A conflicting value must create a new entry and mark the old as superseded."""
        svc = MemoryService()
        existing = _make_entry(entry_id="mem_old", value="pytest", commit_sha="old_sha")

        inserted_docs: list = []
        updated_filters: list = []

        async def capture_insert(doc):
            inserted_docs.append(doc)

        async def capture_update(flt, upd):
            updated_filters.append((flt, upd))

        mock_db = AsyncMock()
        mock_db.operational_memory.find_one = AsyncMock(return_value=existing)
        mock_db.operational_memory.insert_one = AsyncMock(side_effect=capture_insert)
        mock_db.operational_memory.update_one = AsyncMock(side_effect=capture_update)

        candidate = MemoryEntryCandidate(
            kind=OperationalMemoryKind.REPOSITORY_FACT,
            key="test_command",
            value="python -m pytest",  # Different value
            confidence=0.80,
            evidence_ids=["evd_002"],
        )

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            ids = await svc.record_from_delivery(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="new_sha",
                run_id="run_002",
                delivery_result_id="dlr_002",
                verified_evidence_ids={"evd_002"},
                candidates=[candidate],
            )

        assert len(ids) == 1
        new_id_val = ids[0]
        assert new_id_val != "mem_old"

        # New entry was inserted
        assert len(inserted_docs) == 1
        assert inserted_docs[0]["value"] == "python -m pytest"

        # Old entry was marked superseded
        supersede_update = next(
            (upd for (flt, upd) in updated_filters if "$set" in upd and "superseded_by" in upd["$set"]),
            None,
        )
        assert supersede_update is not None
        assert supersede_update["$set"]["superseded_by"] == new_id_val


# ---------------------------------------------------------------------------
# 7. Staleness detection
# ---------------------------------------------------------------------------


class TestStalenessDetection:
    @pytest.mark.asyncio
    async def test_mismatched_commit_sha_is_flagged_stale(self):
        """Entries with commit_sha != current HEAD must be marked stale."""
        svc = MemoryService()

        update_many_calls: list = []

        async def capture_update_many(flt, upd):
            update_many_calls.append((flt, upd))
            result = MagicMock()
            result.modified_count = 1
            return result

        mock_db = AsyncMock()
        mock_db.operational_memory.count_documents = AsyncMock(
            side_effect=[
                1,  # total
                1,  # current_count
                0,  # stale_count
                0,  # unverifiable_count
                0,  # needs_revalidation_count
            ]
        )
        mock_db.operational_memory.update_many = AsyncMock(side_effect=capture_update_many)
        # path-aware branch needs to iterate — return empty cursor
        mock_db.operational_memory.find = MagicMock(return_value=_empty_cursor())

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.check_staleness(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                current_commit_sha="new_sha",
            )

        # When no changed_paths_since_source provided, SHA equality fallback must run
        # and mark entries with mismatched source_commit_sha as stale
        assert len(update_many_calls) >= 1
        # At minimum one call targets entries with mismatched SHA
        stale_calls = [(f, u) for f, u in update_many_calls if u.get("$set", {}).get("staleness_status") == "stale"]
        assert len(stale_calls) >= 1

    @pytest.mark.asyncio
    async def test_empty_repository_returns_zero_report(self):
        svc = MemoryService()
        mock_db = AsyncMock()
        mock_db.operational_memory.count_documents = AsyncMock(return_value=0)

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            report = await svc.check_staleness(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                current_commit_sha="abc123",
            )

        assert report.total_entries == 0
        assert report.stale_count == 0
        assert isinstance(report, StalenessReport)


# ---------------------------------------------------------------------------
# 8. Recall excludes stale entries
# ---------------------------------------------------------------------------


class TestRecallExcludesStale:
    @pytest.mark.asyncio
    async def test_recall_query_excludes_stale(self):
        """recall_for_context must only query entries with staleness_status == 'current'."""
        svc = MemoryService()

        captured_query: dict = {}

        class FakeCursor:
            def sort(self, *a, **kw):
                return self

            def limit(self, n):
                return self

            async def to_list(self, n):
                return []

        def capture_find(query):
            captured_query.update(query)
            return FakeCursor()

        mock_db = MagicMock()
        mock_db.operational_memory.find = capture_find

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            await svc.recall_for_context(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc123",
                candidate_paths=["app/main.py"],
            )

        assert captured_query.get("staleness_status") == "current"
        assert captured_query.get("superseded_by") is None
        assert captured_query.get("deleted_at") is None


# ---------------------------------------------------------------------------
# 9. Path applicability filtering
# ---------------------------------------------------------------------------


class TestPathApplicability:
    def test_empty_applicability_matches_all_paths(self):
        app = MemoryApplicability()
        assert _applicability_matches(app, ["any/path.py"]) is True
        assert _applicability_matches(app, []) is True

    def test_path_prefix_matches(self):
        app = MemoryApplicability(path_prefix="backend/")
        assert _applicability_matches(app, ["backend/app/main.py"]) is True
        assert _applicability_matches(app, ["client/src/index.ts"]) is False

    def test_glob_pattern_matches(self):
        app = MemoryApplicability(glob_patterns=["*.py", "tests/**"])
        assert _applicability_matches(app, ["backend/app/main.py"]) is True
        assert _applicability_matches(app, ["tests/test_foo.py"]) is True
        assert _applicability_matches(app, ["client/src/index.ts"]) is False

    def test_no_candidate_paths_always_matches(self):
        app = MemoryApplicability(path_prefix="backend/", glob_patterns=["*.py"])
        assert _applicability_matches(app, []) is True


# ---------------------------------------------------------------------------
# 10. Context cap enforcement
# ---------------------------------------------------------------------------


class TestContextCap:
    @pytest.mark.asyncio
    async def test_recall_returns_at_most_max_context_entries(self):
        """recall_for_context must return no more than max_context_entries summaries."""
        cap = 3
        svc = MemoryService(max_context_entries=cap)

        # Build 10 matching entries
        docs = [
            _make_entry(
                entry_id=f"mem_{i:03d}",
                key=f"key_{i}",
                commit_sha="abc123",
            )
            for i in range(10)
        ]

        class FakeCursor:
            def sort(self, *a, **kw):
                return self

            def limit(self, n):
                self._n = n
                return self

            async def to_list(self, n):
                return docs[:n]

        mock_db = MagicMock()
        mock_db.operational_memory.find = lambda q: FakeCursor()

        with patch("app.memory.memory_service.get_db", return_value=mock_db):
            results = await svc.recall_for_context(
                user_id="user_1",
                repository_id="https://github.com/test/repo",
                branch="main",
                commit_sha="abc123",
                candidate_paths=[],  # Empty → all entries match
            )

        assert len(results) <= cap


# ---------------------------------------------------------------------------
# Helpers for async cursor mocking
# ---------------------------------------------------------------------------


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


def _empty_cursor():
    """Return an object that acts like an empty async cursor for db.find()."""

    class _C:
        def __aiter__(self):
            return _AsyncIter([])

    return _C()
