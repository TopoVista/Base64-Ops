from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

_client: AsyncIOMotorClient | None = None


async def connect_mongo() -> None:
    global _client
    settings = get_settings()
    if not settings.mongo_uri:
        _client = None
        return
    _client = AsyncIOMotorClient(settings.mongo_uri)
    await _client.admin.command("ping")


async def close_mongo() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None


def get_db() -> AsyncIOMotorDatabase:
    settings = get_settings()
    if _client is None:
        raise RuntimeError("MongoDB is not connected. Set MONGO_URI and restart the API.")
    return _client[settings.mongo_db_name]
