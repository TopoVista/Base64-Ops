import asyncio

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

        async def delete_many(self, query: dict) -> None:
            self.deleted.append(query)

        async def insert_many(self, rows: list[dict]) -> None:
            self.rows.extend(rows)

    class Database:
        rag_chunks = Chunks()

    original_get_db = rag_service.get_db
    rag_service.get_db = lambda: Database()  # type: ignore[assignment]
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
    try:
        result = asyncio.run(
            RagService().index_workspace({"_id": "ses_1", "repoName": "fixture"}, tmp_path)
        )
    finally:
        rag_service.get_db = original_get_db

    assert result["indexed"] == 1
    assert result["storage"] == "lexical"
    assert Database.rag_chunks.rows[0]["source"] == "backend/main.py"


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
