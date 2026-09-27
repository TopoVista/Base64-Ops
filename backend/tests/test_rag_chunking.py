import asyncio

from langchain_core.documents import Document

from app.services import rag_service
from app.services.rag_service import RagService
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


def test_index_workspace_persists_lexical_chunks_without_vector_search(tmp_path) -> None:
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
    try:
        result = asyncio.run(
            RagService().index_workspace({"_id": "ses_1", "repoName": "fixture"}, tmp_path)
        )
    finally:
        rag_service.get_db = original_get_db

    assert result["indexed"] == 1
    assert result["storage"] == "lexical"
    assert Database.rag_chunks.rows[0]["source"] == "backend/main.py"
