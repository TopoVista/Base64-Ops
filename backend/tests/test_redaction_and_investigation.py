import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.agent.graph import AgentGraph
from app.schemas.investigation import EvidenceItem
from app.services import session_service
from app.services.redaction_service import RedactionService


def test_redaction_removes_common_credentials_from_evidence() -> None:
    value = "Authorization: Bearer abc.def-123 and OPENAI_API_KEY=sk-secret-value-123456789"
    redacted = RedactionService().redact(value)
    assert "abc.def-123" not in redacted
    assert "sk-secret-value-123456789" not in redacted
    assert "[REDACTED]" in redacted


def test_evidence_requires_stable_provenance_fields() -> None:
    item = EvidenceItem(
        id="evd_1",
        user_id="usr_1",
        session_id="ses_1",
        run_id="run_1",
        source_type="repo_file",
        title="Dockerfile",
        excerpt="EXPOSE 8000",
        retrieved_at=datetime.now(UTC),
        content_hash="a" * 64,
    )
    assert item.source_type == "repo_file"
    assert item.content_hash == "a" * 64


def test_empty_rag_can_fall_back_to_bounded_direct_repository_reads(tmp_path: Path) -> None:
    (tmp_path / "client").mkdir()
    (tmp_path / "client" / "package.json").write_text('{"name": "fixture"}\n', encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\n", encoding="utf-8"
    )
    graph = AgentGraph()
    sources = graph._direct_repository_sources(
        {
            "repo_path": str(tmp_path),
            "prompt": "Why did CI build fail?",
            "repository_map": {
                "ci": [".github/workflows/ci.yml"],
                "manifests": ["client/package.json"],
                "services": [],
                "containers": [],
                "runbooks": [],
            },
        }
    )
    assert {source["source"] for source in sources} == {".github/workflows/ci.yml", "client/package.json"}
    assert all(source["metadata"]["retrieval"] == "direct_read_fallback" for source in sources)


def test_session_hydration_returns_only_the_users_latest_command_center() -> None:
    """The persisted run snapshot is the source of truth when reopening a session."""

    class Cursor:
        def __init__(self, rows: list[dict]) -> None:
            self.rows = rows

        def sort(self, *_args):
            return self

        async def to_list(self, _limit: int) -> list[dict]:
            return self.rows

    class Collection:
        def __init__(self, documents: list[dict]) -> None:
            self.documents = documents

        async def find_one(self, query: dict, **_kwargs):
            for document in self.documents:
                if all(document.get(key) == value for key, value in query.items()):
                    return document
            return None

        def find(self, query: dict) -> Cursor:
            return Cursor([
                document for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ])

    class Database:
        sessions = Collection([{"_id": "ses_a", "userId": "user_a", "slugId": "diagnose"}])
        messages = Collection([])
        investigations = Collection([
            {
                "userId": "user_a", "sessionId": "ses_a", "runId": "run_latest",
                "repository": {"commit_sha": "abc123"},
                "investigation": {"summary": "Evidence-backed diagnosis"},
                "timeline": [{"id": "map", "status": "completed"}],
                "evidence": [{"id": "evd_1"}],
                "toolResult": {"success": True, "output": "On branch main"},
            },
            {
                "userId": "user_b", "sessionId": "ses_a", "runId": "run_other_user",
                "repository": {"commit_sha": "secret"},
            },
        ])

    original_get_db = session_service.get_db
    session_service.get_db = lambda: Database()  # type: ignore[assignment]
    try:
        result = asyncio.run(session_service.get_session_by_slug("user_a", "diagnose"))
    finally:
        session_service.get_db = original_get_db

    assert result["commandCenter"]["runId"] == "run_latest"
    assert result["commandCenter"]["repository"]["commit_sha"] == "abc123"
    assert result["commandCenter"]["evidence"] == [{"id": "evd_1"}]
    assert result["commandCenter"]["toolResult"]["output"] == "On branch main"


def test_safe_tool_result_is_bounded_and_redacted() -> None:
    safe = session_service.safe_tool_result(
        {"success": True, "output": "Authorization: Bearer secret-value\n" + "x" * 5_000}
    )
    assert safe is not None
    assert safe["success"] is True
    assert "secret-value" not in safe["output"]
    assert len(safe["output"]) == 4_000


def test_index_failure_reason_distinguishes_mongo_from_repository_failures() -> None:
    from pymongo.errors import AutoReconnect

    assert "MongoDB" in session_service.index_failure_reason(AutoReconnect("TLS failed"))
    assert "direct read-only inspection" in session_service.index_failure_reason(RuntimeError("unexpected"))
