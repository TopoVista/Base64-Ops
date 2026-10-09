from app import main


async def test_mongo_recovery_reinstalls_indexes_before_reporting_success(monkeypatch) -> None:
    events: list[str] = []

    async def no_sleep(_seconds: int) -> None:
        events.append("sleep")

    async def reconnect() -> None:
        events.append("connect")

    async def ensure_indexes() -> None:
        events.append("indexes")

    monkeypatch.setattr(main, "mongo_is_connected", lambda: False)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(main, "connect_mongo", reconnect)
    monkeypatch.setattr(main, "_ensure_database_indexes", ensure_indexes)

    await main._retry_mongo_connection()

    assert events == ["sleep", "connect", "indexes"]
