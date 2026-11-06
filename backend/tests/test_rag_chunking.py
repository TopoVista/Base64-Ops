import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from langchain_core.documents import Document

from app.services import rag_service, workspace_service
from app.services.rag_service import RagService
from app.services.workspace_service import WorkspaceService
from app.utils.chunking import chunk_text


def test_chunk_text_empty_input():
    assert chunk_text("") == []


def test_chunk_text_overlaps_long_input():
    text = "a" * 2000
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    assert len(chunks) == 3
    assert len(chunks[0]) == 1000
    assert chunks[0][-100:] == chunks[1][:100]


def test_rag_rows_always_include_lexical_retrieval_fields() -> None:
    rows = RagService._document_rows(
        [
            Document(
                page_content="FastAPI binds on 0.0.0.0",
                metadata={"sessionId": "ses_1", "kind": "repo", "source": "backend/main.py"},
            )
        ]
    )
    assert rows[0]["sessionId"] == "ses_1"
    assert rows[0]["text"] == "FastAPI binds on 0.0.0.0"
    assert rows[0]["source"] == "backend/main.py"
    assert "fastapi" in rows[0]["searchTerms"]
    assert "backend" in rows[0]["searchTerms"]


def test_lexical_rag_scores_natural_language_terms_and_has_safe_fallback() -> None:
    docs = [
        {"source": "backend/main.py", "text": "uvicorn.run(app, host='0.0.0.0')"},
        {"source": ".github/workflows/ci.yml", "text": "npm run build"},
    ]
    terms = RagService._lexical_terms("Why is the backend host binding wrong?")
    assert "backend" in terms and "host" in terms
    assert RagService._lexical_score(docs[0], terms) > RagService._lexical_score(docs[1], terms)
    assert RagService._fallback_lexical_documents(docs, 1)[0]["source"] == ".github/workflows/ci.yml"


def test_index_workspace_persists_lexical_chunks_without_vector_search(tmp_path, monkeypatch) -> None:
    source = tmp_path / "backend" / "main.py"
    source.parent.mkdir()
    source.write_text("app = FastAPI()\n", encoding="utf-8")

    class Chunks:
        def __init__(self) -> None:
            self.deleted: list[dict] = []
            self.rows: list[dict] = []
            self.indexes: list[list[tuple[str, int]]] = []

        async def create_index(self, fields: list[tuple[str, int]], **_kwargs) -> None:  # noqa: ANN003
            self.indexes.append(fields)

        def find(self, query: dict, _projection: dict):
            rows = [
                row for row in self.rows
                if all(row.get(key) == value for key, value in query.items())
            ]

            class Cursor:
                def __init__(self, value: list[dict]) -> None:
                    self.value = value

                def limit(self, _value: int):
                    return self

                def sort(self, _field: str, _direction: int):  # noqa: ANN201
                    return self

                async def to_list(self, _value: int) -> list[dict]:
                    return list(self.value)

            return Cursor(rows)

        async def delete_many(self, query: dict) -> None:
            self.deleted.append(query)
            self.rows = [
                row for row in self.rows
                if not all(row.get(key) == value for key, value in query.items())
            ]

        async def insert_many(self, rows: list[dict]) -> None:
            self.rows.extend(rows)

    class Database:
        rag_chunks = Chunks()

    monkeypatch.setattr(rag_service, "get_db", lambda: Database())
    # This is a lexical-storage test. It must remain offline even when a
    # developer has valid OpenAI/Atlas settings in their local .env file.
    settings = rag_service.get_settings()
    monkeypatch.setattr(
        rag_service,
        "get_settings",
        lambda: settings.model_copy(
            update={"rag_vector_enabled": False, "openai_api_key": None}
        ),
    )
    result = asyncio.run(
        RagService().index_workspace({"_id": "ses_1", "repoName": "fixture"}, tmp_path)
    )

    assert result["indexed"] == 1
    assert result["storage"] == "lexical"
    assert Database.rag_chunks.rows[0]["source"] == "backend/main.py"
    assert Database.rag_chunks.rows[0]["contentHash"]
    assert result["indexMode"] == "incremental_lexical"
    assert result["chunksInserted"] == 1
    assert Database.rag_chunks.indexes == [
        [("sessionId", 1), ("searchTerms", 1)],
        [("sessionId", 1), ("kind", 1), ("source", 1), ("contentHash", 1)],
        [("repositoryId", 1), ("kind", 1), ("validAt", 1), ("knownAt", 1), ("searchTerms", 1)],
    ]

    unchanged = asyncio.run(
        RagService().index_workspace({"_id": "ses_1", "repoName": "fixture"}, tmp_path)
    )
    assert unchanged["chunksInserted"] == 0
    assert unchanged["chunksReused"] == 1

    source.write_text("app = FastAPI(title='changed')\n", encoding="utf-8")
    changed = asyncio.run(
        RagService().index_workspace({"_id": "ses_1", "repoName": "fixture"}, tmp_path)
    )
    assert changed["chunksInserted"] == 1
    assert changed["chunksReused"] == 0
    assert "changed" in Database.rag_chunks.rows[0]["text"]

    # Old rows without a trustworthy source hash must not be reused as though
    # their source contents were current.
    Database.rag_chunks.rows[0].pop("contentHash")
    Database.rag_chunks.rows[0]["metadata"].pop("contentHash")
    migrated = asyncio.run(
        RagService().index_workspace({"_id": "ses_1", "repoName": "fixture"}, tmp_path)
    )
    assert migrated["indexMode"] == "full_replace_untrusted_existing_corpus"
    assert migrated["chunksInserted"] == 1


def test_commit_scoped_rag_keeps_historical_chunks_and_retrieves_only_the_requested_sha(tmp_path, monkeypatch) -> None:
    source = tmp_path / "backend" / "main.py"
    source.parent.mkdir()
    source.write_text("FAILED_SHA_TOKEN = 'historical'\n", encoding="utf-8")
    deleted_source = tmp_path / "backend" / "removed.py"
    deleted_source.write_text("DELETED_AFTER_FAILURE_TOKEN = 'historical-only'\n", encoding="utf-8")

    class Cursor:
        def __init__(self, rows: list[dict]) -> None:
            self.rows = rows

        def limit(self, value: int):  # noqa: ANN201
            self.rows = self.rows[:value]
            return self

        def sort(self, field: str, direction: int):  # noqa: ANN201
            self.rows.sort(key=lambda row: str(row.get(field, "")), reverse=direction < 0)
            return self

        async def to_list(self, value: int) -> list[dict]:
            return self.rows[:value]

    def matches(row: dict, query: dict) -> bool:
        for key, value in query.items():
            actual = row.get(key)
            if isinstance(value, dict):
                if "$in" in value and not (
                    any(item in value["$in"] for item in actual) if isinstance(actual, list) else actual in value["$in"]
                ):
                    return False
                if "$lte" in value and (actual is None or actual > value["$lte"]):
                    return False
            elif actual != value:
                return False
        return True

    class Chunks:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        async def create_index(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            return None

        def find(self, query: dict, *_args) -> Cursor:  # noqa: ANN002
            return Cursor([row for row in self.rows if matches(row, query)])

        async def delete_many(self, query: dict) -> None:
            self.rows = [row for row in self.rows if not matches(row, query)]

        async def insert_many(self, rows: list[dict]) -> None:
            self.rows.extend(rows)

    class Snapshots:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        async def update_one(self, query: dict, update: dict, *, upsert: bool) -> None:
            existing = next((row for row in self.rows if matches(row, query)), None)
            if existing is None:
                assert upsert
                existing = {**query, **update.get("$setOnInsert", {})}
                self.rows.append(existing)
            existing.update(update["$set"])

        async def find_one(self, query: dict) -> dict | None:
            return next((row for row in self.rows if matches(row, query)), None)

    class Database:
        rag_chunks = Chunks()
        rag_snapshots = Snapshots()

    monkeypatch.setattr(rag_service, "get_db", lambda: Database())
    settings = rag_service.get_settings()
    monkeypatch.setattr(
        rag_service,
        "get_settings",
        lambda: settings.model_copy(update={"rag_vector_enabled": False, "openai_api_key": None}),
    )
    service = RagService()
    session = {"_id": "ses_1", "repoName": "fixture", "repoUrl": "https://github.com/acme/fixture.git"}
    sha_a, sha_b = "a" * 40, "b" * 40

    first = asyncio.run(service.index_workspace(session, tmp_path, revision=sha_a))
    source.write_text("CURRENT_SHA_TOKEN = 'current'\n", encoding="utf-8")
    deleted_source.unlink()
    second = asyncio.run(service.index_workspace(session, tmp_path, revision=sha_b))

    historical = asyncio.run(service.retrieve("ses_1", "FAILED_SHA_TOKEN", valid_at=sha_a))
    current = asyncio.run(service.retrieve("ses_1", "CURRENT_SHA_TOKEN", valid_at=sha_b))
    missing = asyncio.run(service.retrieve("ses_1", "CURRENT_SHA_TOKEN", valid_at="c" * 40))
    deleted_at_failure = asyncio.run(service.retrieve("ses_1", "DELETED_AFTER_FAILURE_TOKEN", valid_at=sha_a))
    deleted_at_current = asyncio.run(service.retrieve("ses_1", "DELETED_AFTER_FAILURE_TOKEN", valid_at=sha_b))
    not_yet_known = asyncio.run(
        service.retrieve("ses_1", "FAILED_SHA_TOKEN", valid_at=sha_a, known_at=datetime(2000, 1, 1, tzinfo=UTC))
    )
    snapshot = asyncio.run(
        service.snapshot_freshness(
            session_id="ses_1",
            repository_id="https://github.com/acme/fixture.git",
            valid_at=sha_a,
            known_at=datetime.now(UTC),
        )
    )

    assert first["freshness"] == second["freshness"] == "exact_revision"
    assert {row["validAt"] for row in Database.rag_chunks.rows} == {sha_a, sha_b}
    assert "historical" in historical[0]["excerpt"] and historical[0]["freshness"] == "valid_at"
    assert "current" in current[0]["excerpt"] and current[0]["validAt"] == sha_b
    assert missing == []
    assert "historical-only" in deleted_at_failure[0]["excerpt"]
    assert all("historical-only" not in item["excerpt"] for item in deleted_at_current)
    assert not_yet_known == []
    assert snapshot and snapshot["status"] == "ready" and snapshot["commitSha"] == sha_a


def test_repository_index_uses_aggregate_file_and_byte_limits(tmp_path) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x" * 200, encoding="utf-8")

    documents, stats = RagService()._build_repository_documents(
        {"_id": "ses_1", "repoName": "fixture"},
        tmp_path,
        max_files=2,
        max_total_bytes=1_000,
        max_chunks=100,
    )

    assert len(documents) == 2
    assert stats == {
        "filesIndexed": 2,
        "indexedBytes": 400,
        "skippedFiles": 1,
        "truncated": True,
    }


def test_repository_index_stops_at_the_chunk_ceiling(tmp_path) -> None:
    (tmp_path / "large.py").write_text("x" * 5_000, encoding="utf-8")

    documents, stats = RagService()._build_repository_documents(
        {"_id": "ses_1", "repoName": "fixture"},
        tmp_path,
        max_files=10,
        max_total_bytes=10_000,
        max_chunks=1,
    )

    assert len(documents) == 1
    assert stats["filesIndexed"] == 1
    assert stats["truncated"] is True


def test_dependency_graph_resolves_local_python_and_typescript_imports(tmp_path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "main.py").write_text("from backend.service import run\n", encoding="utf-8")
    (backend / "service.py").write_text("def run(): pass\n", encoding="utf-8")
    client_src = tmp_path / "client" / "src"
    client_src.mkdir(parents=True)
    (client_src / "main.ts").write_text('import { api } from "./api"\n', encoding="utf-8")
    (client_src / "api.ts").write_text("export const api = {}\n", encoding="utf-8")

    graph = WorkspaceService().dependency_graph(tmp_path)

    assert {"from": "backend/main.py", "to": "backend/service.py"} in graph["edges"]
    assert {"from": "client/src/main.ts", "to": "client/src/api.ts"} in graph["edges"]
    assert graph["mermaid"].startswith("flowchart LR")


def test_workspace_recovers_interrupted_base64_clone(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    repo = root / "user" / "session" / "fixture"
    repo.mkdir(parents=True)
    (repo / "partial-file").write_text("interrupted clone", encoding="utf-8")
    monkeypatch.setattr(workspace_service, "workspace_path", lambda *_args: root / "user" / "session")

    async def token(_user_id: str) -> str:
        return "token"

    monkeypatch.setattr(workspace_service, "get_github_access_token", token)
    service = WorkspaceService()

    def clone(args, cwd, **_kwargs):
        target = cwd / args[-1]
        target.mkdir()
        (target / ".git").mkdir()
        return {"success": True, "output": "", "exitCode": 0}

    monkeypatch.setattr(service, "_run", clone)
    repo_path, _ = asyncio.run(service.ensure_workspace("user", "session", "https://github.com/acme/fixture", "main"))

    assert repo_path == repo
    assert (repo / ".git").exists()
    assert not (repo / "partial-file").exists()


def test_workspace_includes_go_source_files_for_index_and_editor(tmp_path) -> None:
    source = tmp_path / "cmd" / "api" / "main.go"
    source.parent.mkdir(parents=True)
    source.write_text("package main\nfunc main() {}\n", encoding="utf-8")

    indexed = WorkspaceService().iter_indexable_files(tmp_path)

    assert source in indexed


def test_workspace_file_selection_is_stable_before_bounded_indexing(tmp_path) -> None:
    (tmp_path / "z").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "z" / "last.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "a" / "first.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "middle.py").write_text("pass\n", encoding="utf-8")

    indexed = WorkspaceService().iter_indexable_files(tmp_path)

    assert [path.relative_to(tmp_path).as_posix() for path in indexed] == [
        "middle.py",
        "a/first.py",
        "z/last.py",
    ]


def test_workspace_path_guard_rejects_sibling_prefix_escape(tmp_path) -> None:
    repository = tmp_path / "repo"
    sibling = tmp_path / "repo-shadow"
    repository.mkdir()
    sibling.mkdir()
    (sibling / "secret.py").write_text("not repository content", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        WorkspaceService()._safe_path(repository, "../repo-shadow/secret.py")

    assert exc.value.status_code == 400
