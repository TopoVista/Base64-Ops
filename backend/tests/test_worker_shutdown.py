import asyncio

from app.workers import worker


async def test_worker_drain_does_not_claim_a_new_job_after_shutdown(monkeypatch) -> None:
    events: list[str] = []

    async def connect() -> None:
        events.append("connect")

    async def close() -> None:
        events.append("close")

    class Jobs:
        async def ensure_indexes(self) -> None:
            events.append("indexes")

        async def claim(self, _role: str):
            events.append("claim")
            return None

    class GraphStore:
        async def ensure_indexes(self) -> None:
            events.append("graph-indexes")

    monkeypatch.setattr(worker, "connect_mongo", connect)
    monkeypatch.setattr(worker, "close_mongo", close)
    monkeypatch.setattr(worker, "BackgroundJobService", Jobs)
    monkeypatch.setattr(worker, "EngineeringGraphStore", GraphStore)
    stopped = asyncio.Event()
    stopped.set()

    await worker.run("indexer", once=False, stopped=stopped)

    assert events == ["connect", "indexes", "graph-indexes", "close"]
