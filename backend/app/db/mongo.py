import asyncio

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.core.config import get_settings

_client: AsyncIOMotorClient | None = None


class MongoUnavailableError(RuntimeError):
    """Raised when a database-backed request arrives during an Atlas outage."""


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
            maxPoolSize=settings.mongo_max_pool_size,
            waitQueueTimeoutMS=settings.mongo_wait_queue_timeout_ms,
            maxIdleTimeMS=60_000,
            retryReads=True,
            retryWrites=True,
            appname="base64-ops",
        )
        try:
            await client.admin.command("ping")
            _client = client
            return
        except PyMongoError as exc:
            last_error = exc
            client.close()
            if attempt < settings.mongo_connect_max_retries:
                # Short exponential backoff prevents a DNS/TLS outage from
                # creating a tight connection loop, while still recovering
                # quickly enough for a stateless API replica.
                await asyncio.sleep(min(5.0, 0.5 * (2**attempt)))
    _client = None
    if last_error:
        raise last_error


async def close_mongo() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None


async def ping_mongo() -> bool:
    """Probe the current client and discard it if Atlas has dropped it.

    ``mongo_is_connected`` reports whether a client has been established, not
    whether its underlying sockets are still usable. The application lifecycle
    calls this bounded ping on an interval so an outage after startup enters
    the same safe reconnect path as a startup outage.
    """

    global _client
    client = _client
    if client is None:
        return False
    try:
        await client.admin.command("ping")
        return True
    except PyMongoError:
        # Only clear the exact client we probed. A newer client may have been
        # installed by a concurrent recovery attempt.
        if _client is client:
            client.close()
            _client = None
        return False


def get_db() -> AsyncIOMotorDatabase:
    settings = get_settings()
    if _client is None:
        # The API may intentionally stay up while the background reconnect
        # loop recovers Atlas.  Do not imply that tenant data was lost or that
        # callers should change credentials; the frontend can retry safely.
        raise MongoUnavailableError("MongoDB is temporarily unavailable")
    return _client[settings.mongo_db_name]


def mongo_is_connected() -> bool:
    """Return whether database-backed routes can currently use MongoDB."""
    return _client is not None
