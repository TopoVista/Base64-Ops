import asyncio

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.core.config import get_settings

_client: AsyncIOMotorClient | None = None


async def connect_mongo() -> None:
    global _client
    settings = get_settings()
    if not settings.mongo_uri:
        _client = None
        return
    last_error: PyMongoError | None = None
    for attempt in range(settings.mongo_connect_max_retries + 1):
        client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=settings.mongo_connect_timeout_ms,
            connectTimeoutMS=settings.mongo_connect_timeout_ms,
            socketTimeoutMS=settings.mongo_connect_timeout_ms,
        )
        try:
            await client.admin.command("ping")
            _client = client
            return
        except PyMongoError as exc:
            last_error = exc
            client.close()
            if attempt < settings.mongo_connect_max_retries:
                await asyncio.sleep(0.25 * (2**attempt))
    _client = None
    if last_error:
        raise last_error


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


def mongo_is_connected() -> bool:
    """Return whether database-backed routes can currently use MongoDB."""
    return _client is not None
