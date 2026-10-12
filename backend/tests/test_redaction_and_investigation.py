import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

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


def test_redaction_removes_cloud_tokens_and_non_mongo_connection_strings() -> None:
    # Construct a deliberately fake token at runtime so the fixture exercises the
    # Slack redactor without committing a credential-shaped static value.
    slack_fixture = "-".join(("xoxb", "fixturetoken1234"))
    value = (
        "AWS=AKIAIOSFODNN7EXAMPLE "
        f"slack={slack_fixture} "
        "postgresql://ops:database-password@db.example/app "
        "redis://:cache-password@cache.example:6379/0"
    )
    redacted = RedactionService().redact(value)

    for secret in ("AKIAIOSFODNN7EXAMPLE", slack_fixture, "database-password", "cache-password"):
        assert secret not in redacted
    assert "[REDACTED_AWS_ACCESS_KEY]" in redacted
    assert "[REDACTED_SLACK_TOKEN]" in redacted
    assert redacted.count("[REDACTED_CONNECTION_STRING]") == 2


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


def test_session_hydration_returns_only_the_users_latest_command_center(monkeypatch) -> None:
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
        sessions = Collection([
            {
                "_id": "ses_a",
                "userId": "user_a",
                "slugId": "diagnose",
                "repoUrl": "https://github.com/example/repo.git",
            }
        ])
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

    refresh_calls: list[tuple[str, str]] = []

    async def no_automatic_refresh(user_id: str, slug_id: str) -> None:
        refresh_calls.append((user_id, slug_id))

    monkeypatch.setattr(session_service, "get_db", lambda: Database())
    monkeypatch.setattr(
        session_service, "get_settings", lambda: SimpleNamespace(ci_auto_refresh_seconds=0)
    )
    monkeypatch.setattr(session_service, "refresh_latest_ci", no_automatic_refresh)
    result = asyncio.run(session_service.get_session_by_slug("user_a", "diagnose"))

    assert result["commandCenter"]["runId"] == "run_latest"
    assert result["commandCenter"]["repository"]["commit_sha"] == "abc123"
    assert result["commandCenter"]["evidence"] == [{"id": "evd_1"}]
    assert result["commandCenter"]["toolResult"]["output"] == "On branch main"
    assert refresh_calls == []


def test_session_hydration_uses_the_canonical_pending_delivery_approval(monkeypatch) -> None:
    """A stale nested snapshot must not hide the current safe push action."""

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
            return Cursor(
                [
                    document
                    for document in self.documents
                    if all(document.get(key) == value for key, value in query.items())
                ]
            )

    class Database:
        sessions = Collection(
            [{"_id": "ses_a", "userId": "user_a", "slugId": "diagnose", "repoUrl": "https://example/repo"}]
        )
        messages = Collection([])
        investigations = Collection(
            [
                {
                    "userId": "user_a",
                    "sessionId": "ses_a",
                    "runId": "run_latest",
                    "deliveryPlan": {"id": "dlp_current", "title": "Fix CI"},
                    "approvals": [{"id": "apr_old", "status": "invalidated", "deliveryPlanId": "dlp_current"}],
                }
            ]
        )
        delivery_results = Collection([])
        approvals = Collection(
            [
                {
                    "id": "apr_current",
                    "userId": "user_a",
                    "sessionId": "ses_a",
                    "deliveryPlanId": "dlp_current",
                    "status": "pending",
                }
            ]
        )

    monkeypatch.setattr(session_service, "get_db", lambda: Database())
    monkeypatch.setattr(
        session_service, "get_settings", lambda: SimpleNamespace(ci_auto_refresh_seconds=0)
    )

    result = asyncio.run(session_service.get_session_by_slug("user_a", "diagnose"))

    assert result["commandCenter"]["approvals"] == [
        {
            "id": "apr_current",
            "userId": "user_a",
            "sessionId": "ses_a",
            "deliveryPlanId": "dlp_current",
            "status": "pending",
        }
    ]


def test_request_delivery_approval_reuses_a_plan_bound_pending_record(monkeypatch) -> None:
    """The recovery action must never create duplicate authorization records."""

    class Collection:
        def __init__(self, documents: list[dict]) -> None:
            self.documents = documents

        async def find_one(self, query: dict, **_kwargs):
            for document in self.documents:
                if all(document.get(key) == value for key, value in query.items()):
                    return document
            return None

    class Database:
        sessions = Collection(
            [
                {
                    "_id": "ses_a",
                    "userId": "user_a",
                    "slugId": "diagnose",
                    "repoUrl": "https://example/repo",
                    "defaultBranch": "main",
                }
            ]
        )
        delivery_plans = Collection(
            [{"id": "dlp_current", "userId": "user_a", "sessionId": "ses_a"}]
        )
        approvals = Collection(
            [
                {
                    "id": "apr_current",
                    "userId": "user_a",
                    "sessionId": "ses_a",
                    "deliveryPlanId": "dlp_current",
                    "status": "pending",
                }
            ]
        )

    monkeypatch.setattr(session_service, "get_db", lambda: Database())
    result = asyncio.run(
        session_service.request_delivery_approval("user_a", "diagnose", "dlp_current")
    )

    assert result["reused"] is True
    assert result["approval"]["id"] == "apr_current"


def test_safe_tool_result_is_bounded_and_redacted() -> None:
    safe = session_service.safe_tool_result(
        {"success": True, "output": "Authorization: Bearer secret-value\n" + "x" * 5_000}
    )
    assert safe is not None
    assert safe["success"] is True
    assert "secret-value" not in safe["output"]
    assert len(safe["output"]) == 4_000


def test_session_code_read_is_tenant_scoped_bounded_and_redacted(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "app.py"
    source.write_text("token = 'sk-secret-value-123456789'\n" + "x" * 65_000, encoding="utf-8")

    class Sessions:
        async def find_one(self, query: dict) -> dict | None:
            if query == {"userId": "user_a", "slugId": "session_a"}:
                return {
                    "userId": "user_a",
                    "slugId": "session_a",
                    "repoUrl": "https://example/repo",
                    "defaultBranch": "main",
                }
            return None

    class Database:
        sessions = Sessions()

    class Workspace:
        async def ensure_workspace(self, *_args):
            return tmp_path, "repo"

        def read_file(self, _repo_path: Path, path: str) -> str:
            return (tmp_path / path).read_text(encoding="utf-8")

        def head_commit(self, _repo_path: Path) -> str:
            return "abc123"

        def iter_indexable_files(self, _repo_path: Path) -> list[Path]:
            return [source]

    monkeypatch.setattr(session_service, "get_db", lambda: Database())
    monkeypatch.setattr(session_service, "WorkspaceService", Workspace)

    payload = asyncio.run(session_service.read_session_code_file("user_a", "session_a", "app.py"))
    tree = asyncio.run(session_service.list_session_code_files("user_a", "session_a"))

    assert payload["truncated"] is True
    assert "sk-secret-value-123456789" not in payload["content"]
    assert payload["headSha"] == "abc123"
    assert tree["files"] == [{"path": "app.py", "bytes": source.stat().st_size}]


def test_index_failure_reason_distinguishes_mongo_from_repository_failures() -> None:
    from pymongo.errors import AutoReconnect

    assert "MongoDB" in session_service.index_failure_reason(AutoReconnect("TLS failed"))
    assert "direct read-only inspection" in session_service.index_failure_reason(RuntimeError("unexpected"))
