from app.core.config import Settings
from app.db import mongo


async def test_mongo_connection_uses_a_bounded_pool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Admin:
        async def command(self, name: str) -> None:
            assert name == "ping"

    class Client:
        admin = Admin()

        def close(self) -> None:
            captured["closed"] = True

    def create_client(_uri: str, **kwargs: object) -> Client:
        captured.update(kwargs)
        return Client()

    monkeypatch.setattr(
        mongo,
        "get_settings",
        lambda: Settings(
            mongo_uri="mongodb://example",
            mongo_connect_max_retries=0,
            mongo_max_pool_size=17,
            mongo_wait_queue_timeout_ms=4_321,
        ),
    )
    monkeypatch.setattr(mongo, "AsyncIOMotorClient", create_client)
    mongo._client = None

    await mongo.connect_mongo()
    await mongo.close_mongo()

    assert captured["maxPoolSize"] == 17
    assert captured["waitQueueTimeoutMS"] == 4_321
    assert captured["closed"] is True
