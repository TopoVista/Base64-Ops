import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import assistant, auth, dev, github, memory, session, system
from app.core.config import get_settings
from app.db.mongo import close_mongo, connect_mongo, mongo_is_connected

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        await connect_mongo()
    except Exception as exc:  # Database-backed routes fail closed via get_db().
        # A transient database outage must not make health checks or the API
        # process disappear. Do not include the provider message here: it may
        # contain connection metadata.
        logger.warning("MongoDB was unavailable during startup: %s", type(exc).__name__)
    yield
    await close_mongo()


settings = get_settings()

app = FastAPI(
    title="Base64 Ops API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list({settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"}),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    database_connected = mongo_is_connected()
    return {
        "message": "Server is running" if database_connected else "Server is running with MongoDB unavailable",
        "status": "healthy" if database_connected else "degraded",
        "database": "connected" if database_connected else "unavailable",
        "stack": "fastapi-langgraph-clerk",
    }


# Auth: only /me endpoint (Clerk handles sign-up/sign-in)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# GitHub OAuth (repo connection, not user auth)
app.include_router(github.router, prefix="/api/github", tags=["github"])

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


@app.exception_handler(Exception)
async def app_exception_handler(_request, exc: Exception) -> JSONResponse:
    status_code = getattr(exc, "status_code", 500)
    if status_code >= 500:
        # User-facing responses must not reveal connection strings, provider
        # messages, filesystem paths, or other operational details.
        logger.error("Unhandled API error: %s", type(exc).__name__)
        detail = "The server could not complete that request. Please try again."
    else:
        detail = getattr(exc, "detail", None) or "The request could not be completed."
    return JSONResponse(status_code=status_code, content={"message": detail})
