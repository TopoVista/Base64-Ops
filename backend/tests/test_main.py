from datetime import UTC, datetime, timedelta

from app import main
from app.db.mongo import MongoUnavailableError
from app.services.session_service import ci_refresh_due


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


async def test_lifespan_fails_closed_if_required_database_indexes_cannot_be_created(monkeypatch) -> None:
    calls: list[str] = []
    state = {"connected": False}

    async def connected_mongo() -> None:
        state["connected"] = True
        calls.append("connected")

    async def unavailable_indexes() -> None:
        calls.append("indexes")
        raise RuntimeError("unique index migration failed")

    async def close_database() -> None:
        state["connected"] = False
        calls.append("closed")

    monkeypatch.setattr(main, "connect_mongo", connected_mongo)
    monkeypatch.setattr(main, "_ensure_database_indexes", unavailable_indexes)
    monkeypatch.setattr(main, "close_mongo", close_database)
    monkeypatch.setattr(main, "mongo_is_connected", lambda: state["connected"])

    async with main.lifespan(main.app):
        calls.append("started")

    assert calls[:4] == ["connected", "indexes", "closed", "started"]


async def test_unhandled_error_response_does_not_expose_internal_details() -> None:
    response = await main.app_exception_handler(None, RuntimeError("mongodb://user:password@host"))

    assert response.status_code == 500
    assert b"password" not in response.body
    assert b"server could not complete" in response.body


async def test_database_outage_returns_a_safe_retryable_response() -> None:
    response = await main.app_exception_handler(None, MongoUnavailableError())

    assert response.status_code == 503
    assert b"saved sessions are safe" in response.body
    assert b"MONGO_URI" not in response.body


async def test_health_reports_a_degraded_database_dependency(monkeypatch) -> None:
    monkeypatch.setattr(main, "mongo_is_connected", lambda: False)

    payload = await main.health()

    assert payload["status"] == "degraded"
    assert payload["database"] == "unavailable"


async def test_readiness_refuses_customer_traffic_when_database_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "mongo_is_connected", lambda: False)

    response = await main.readiness()

    assert response.status_code == 503
    assert b'"status":"not_ready"' in response.body
    assert response.headers["cache-control"] == "no-store"


async def test_readiness_accepts_traffic_only_after_database_connection(monkeypatch) -> None:
    monkeypatch.setattr(main, "mongo_is_connected", lambda: True)

    response = await main.readiness()

    assert response.status_code == 200
    assert b'"status":"ready"' in response.body


async def test_readiness_refuses_customer_traffic_when_fail_closed_rate_limits_are_down(monkeypatch) -> None:
    class UnavailableRateLimitStore:
        async def is_available(self) -> bool:
            return False

    monkeypatch.setattr(main, "mongo_is_connected", lambda: True)
    monkeypatch.setattr(main, "rate_limit_store", UnavailableRateLimitStore())
    monkeypatch.setattr(main, "settings", main.settings.model_copy(update={"rate_limit_fail_closed": True}))

    response = await main.readiness()

    assert response.status_code == 503
    assert b'"rateLimit":"unavailable"' in response.body


def test_ci_auto_refresh_interval_is_deterministic() -> None:
    assert ci_refresh_due(None)
    assert not ci_refresh_due(datetime.now(UTC))
    assert ci_refresh_due(datetime.now(UTC) - timedelta(minutes=2))
    assert not ci_refresh_due(
        datetime.now(UTC) - timedelta(seconds=10), interval=timedelta(seconds=60)
    )
    assert ci_refresh_due(
        datetime.now(UTC) - timedelta(seconds=61), interval=timedelta(seconds=60)
    )


def test_cors_does_not_allow_ambient_browser_credentials() -> None:
    cors = next(item for item in main.app.user_middleware if item.cls.__name__ == "CORSMiddleware")

    assert cors.kwargs["allow_credentials"] is False
