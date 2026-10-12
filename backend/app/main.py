import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.deps import ensure_user_indexes
from app.api.middleware import RateLimitMiddleware, RequestBodyLimitMiddleware, SecurityHeadersMiddleware
from app.api.routes import assistant, auth, billing, dev, diagnostic, github, memory, session, system
from app.billing.billing_service import BillingService
from app.billing.usage_service import UsageService
from app.core.access_logging import configure_access_log_redaction
from app.core.config import get_settings
from app.core.rate_limit import InMemoryRateLimitStore, RedisRateLimitStore
from app.db.mongo import MongoUnavailableError, close_mongo, connect_mongo, mongo_is_connected
from app.intelligence.graph.store import EngineeringGraphStore
from app.intelligence.impact.store import ChangeImpactStore
from app.services.background_job_service import BackgroundJobService
from app.services.github_service import ensure_github_indexes
from app.services.session_service import ensure_session_indexes

logger = logging.getLogger(__name__)

# Uvicorn normally records the complete query string in access logs. OAuth
# callbacks contain one-time authorization codes and signed state there, so
# install redaction before requests are served.
configure_access_log_redaction()


async def _ensure_database_indexes() -> None:
    """Create correctness-critical indexes after every successful connection.

    Mongo can be temporarily unavailable during API startup. Reconnection must
    establish the same idempotency, quota, job, billing, and cache invariants
    as the initial path before database-backed traffic resumes.
    """

    await UsageService().ensure_indexes()
    await BillingService().ensure_indexes()
    await BackgroundJobService().ensure_indexes()
    await ensure_github_indexes()
    await ensure_session_indexes()
    await EngineeringGraphStore().ensure_indexes()
    await ChangeImpactStore().ensure_indexes()
    await ensure_user_indexes()


async def _retry_mongo_connection() -> None:
    """Recover from a transient Atlas/DNS/TLS startup failure without restart."""
    interval = max(5, get_settings().mongo_reconnect_interval_seconds)
    while not mongo_is_connected():
        await asyncio.sleep(interval)
        try:
            await connect_mongo()
            await _ensure_database_indexes()
            logger.info("MongoDB connection recovered after startup degradation")
            return
        except Exception as exc:  # Safe server log only; never expose provider details to clients.
            # A reachable database without required unique/TTL indexes is not
            # safe for quota, idempotency, or one-time OAuth guarantees.
            # Close it so database-backed routes remain fail-closed and the
            # next loop attempts both connection and index setup again.
            if mongo_is_connected():
                await close_mongo()
            logger.warning("MongoDB reconnect attempt failed: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    reconnect_task: asyncio.Task[None] | None = None
    try:
        await connect_mongo()
        await _ensure_database_indexes()
    except Exception as exc:  # Database-backed routes fail closed via get_db().
        # ``connect_mongo`` may succeed while an index setup fails (for
        # example during a migration). Do not accept tenant traffic until the
        # correctness-critical indexes are present.
        if mongo_is_connected():
            await close_mongo()
        # A transient database outage must not make health checks or the API
        # process disappear. Do not include the provider message here: it may
        # contain connection metadata.
        logger.warning("MongoDB was unavailable during startup: %s", type(exc).__name__)
        reconnect_task = asyncio.create_task(_retry_mongo_connection())
    try:
        yield
    finally:
        if reconnect_task is not None:
            reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconnect_task
        await close_mongo()
        await rate_limit_store.aclose()


settings = get_settings()
rate_limit_store = (
    RedisRateLimitStore(settings.redis_url)
    if settings.redis_url
    else InMemoryRateLimitStore(max_keys=settings.rate_limit_local_max_keys)
)
cors_origins = {settings.frontend_origin}
if not settings.is_production:
    cors_origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})

app = FastAPI(
    title="Base64 Ops API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cors_origins),
    # Base64 authenticates API requests with an explicit Clerk Bearer token;
    # it does not use browser cookies. Keeping CORS credentials disabled avoids
    # creating a cross-origin cookie/CSRF channel we do not need.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Keep edge/CDN WAF and rate limiting in front of the public endpoint. These
# local controls still protect direct-origin traffic and runaway API clients.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, settings=settings, store=rate_limit_store)
app.add_middleware(RequestBodyLimitMiddleware, settings=settings)
if settings.trusted_hosts.strip():
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()],
    )


@app.get("/api/health", include_in_schema=False)
@app.get("/health")
async def health() -> dict[str, str]:
    database_connected = mongo_is_connected()
    return {
        "message": "Server is running" if database_connected else "Server is running with MongoDB unavailable",
        "status": "healthy" if database_connected else "degraded",
        "database": "connected" if database_connected else "unavailable",
        "stack": "fastapi-langgraph-clerk",
    }


@app.get("/api/ready", include_in_schema=False)
@app.get("/ready", include_in_schema=False)
async def readiness() -> JSONResponse:
    """Readiness is strict; liveness remains available for outage diagnosis.

    A Mongo outage leaves ``/health`` reachable so the process can report its
    state and execute its bounded reconnect loop. It must not, however, make a
    stateless API replica eligible for customer traffic: all authenticated
    Base64 data and entitlement invariants require MongoDB.
    """

    connected = mongo_is_connected()
    limiter_required = settings.rate_limit_enabled and settings.rate_limit_fail_closed
    limiter_connected = True
    if limiter_required:
        try:
            limiter_connected = await rate_limit_store.is_available()
        except Exception:
            limiter_connected = False
    ready = connected and limiter_connected
    return JSONResponse(
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
        content={
            "status": "ready" if ready else "not_ready",
            "database": "connected" if connected else "unavailable",
            "rateLimit": "connected" if limiter_connected else "unavailable",
        },
    )


# Auth: only /me endpoint (Clerk handles sign-up/sign-in)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# GitHub OAuth (repo connection, not user auth)
app.include_router(github.router, prefix="/api/github", tags=["github"])

# Pasted CI output is streamed as redacted, untrusted diagnostic evidence.
app.include_router(diagnostic.router, prefix="/api", tags=["diagnostic"])

# Agent sessions
app.include_router(session.router, prefix="/api/session", tags=["session"])

# In-product concierge for feature discovery, setup, and safe workflow guidance.
app.include_router(assistant.router, prefix="/api/assistant", tags=["assistant"])

# Dev run inspector
app.include_router(dev.router, prefix="/api", tags=["dev"])

# System capabilities
app.include_router(system.router, prefix="/api", tags=["system"])

# Operational memory
app.include_router(memory.router, prefix="/api/memory", tags=["memory"])

# Subscription creation requires Clerk auth. Webhooks use an independently
# verified provider signature and deliberately do not accept a browser token.
app.include_router(billing.router, prefix="/api/billing", tags=["billing"])


@app.exception_handler(Exception)
async def app_exception_handler(_request, exc: Exception) -> JSONResponse:
    if isinstance(exc, MongoUnavailableError):
        return JSONResponse(
            status_code=503,
            content={
                "message": (
                    "Base64 Ops is temporarily unable to reach its database. "
                    "Your saved sessions are safe; retry once Atlas connectivity is restored."
                ),
                "errorCode": "DATABASE_UNAVAILABLE",
            },
        )
    status_code = getattr(exc, "status_code", 500)
    if status_code >= 500:
        # User-facing responses must not reveal connection strings, provider
        # messages, filesystem paths, or other operational details.
        logger.error("Unhandled API error: %s", type(exc).__name__)
        detail = "The server could not complete that request. Please try again."
    else:
        detail = getattr(exc, "detail", None) or "The request could not be completed."
    return JSONResponse(status_code=status_code, content={"message": detail})
