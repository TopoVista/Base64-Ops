from app import main


async def test_lifespan_keeps_api_available_when_mongo_is_unavailable(monkeypatch) -> None:
    calls: list[str] = []

    async def unavailable_mongo() -> None:
        raise ConnectionError("database endpoint unavailable")

    async def close_database() -> None:
        calls.append("closed")

    monkeypatch.setattr(main, "connect_mongo", unavailable_mongo)
    monkeypatch.setattr(main, "close_mongo", close_database)

    async with main.lifespan(main.app):
        calls.append("started")

    assert calls == ["started", "closed"]


async def test_unhandled_error_response_does_not_expose_internal_details() -> None:
    response = await main.app_exception_handler(None, RuntimeError("mongodb://user:password@host"))

    assert response.status_code == 500
    assert b"password" not in response.body
    assert b"server could not complete" in response.body


async def test_health_reports_a_degraded_database_dependency(monkeypatch) -> None:
    monkeypatch.setattr(main, "mongo_is_connected", lambda: False)

    payload = await main.health()

    assert payload["status"] == "degraded"
    assert payload["database"] == "unavailable"
