from pathlib import Path
from types import SimpleNamespace

from app.agent import graph as graph_module
from app.agent.graph import AgentGraph
from app.core.config import Settings


async def test_queued_indexing_never_runs_inside_the_agent_api_path(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    class RagChunks:
        async def find_one(self, _query, projection=None):  # noqa: ANN001, ANN201
            return None

    class Sessions:
        updates: list[dict] = []

        async def update_one(self, _query, update):  # noqa: ANN001, ANN201
            self.updates.append(update)

    class Telemetry:
        def __init__(self) -> None:
            self.documents: list[dict] = []

        async def insert_one(self, _document):  # noqa: ANN001, ANN201
            self.documents.append(_document)
            return None

    sessions = Sessions()
    telemetry = Telemetry()
    db = SimpleNamespace(rag_chunks=RagChunks(), sessions=sessions, retrieval_telemetry=telemetry)
    monkeypatch.setattr(graph_module, "get_db", lambda: db)
    monkeypatch.setattr(
        graph_module,
        "get_settings",
        lambda: Settings(indexing_mode="queued", memory_enabled=False, rag_retrieval_limit=6),
    )

    class Jobs:
        async def enqueue_reindex(self, *, user_id: str, session_id: str):
            calls.append((user_id, session_id))
            return {"id": "job_index", "status": "queued"}

    monkeypatch.setattr(graph_module, "BackgroundJobService", Jobs)
    graph = AgentGraph()

    async def should_not_index(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("queued indexing must not execute in the API graph")

    async def empty_retrieval(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return []

    graph.rag.index_workspace = should_not_index
    graph.rag.retrieve = empty_retrieval

    async def record_sources(**_kwargs):
        return []

    graph.evidence.record_sources = record_sources
    result = await graph._retrieve(
        {
            "user_id": "user-a",
            "session_id": "session-a",
            "repo_path": str(tmp_path),
            "repo_url": "https://github.com/acme/repository.git",
            "repo_name": "repository",
            "default_branch": "main",
            "prompt": "What is this repository? OPENAI_API_KEY=sk-secret-value-123456789",
            "run_id": "run-a",
            "repository_map": {},
            "timeline": [],
            "context_pack": {},
        }
    )

    assert calls == [("user-a", "session-a")]
    assert sessions.updates[0]["$set"]["indexStatus"] == "queued"
    assert any(item["label"] == "Repository index queued" for item in result["timeline"])
    assert "sk-secret-value-123456789" not in telemetry.documents[0]["query"]
    assert "[REDACTED]" in telemetry.documents[0]["query"]
