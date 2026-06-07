from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, github, session
from app.core.config import get_settings
from app.db.mongo import close_mongo, connect_mongo


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await connect_mongo()
    yield
    await close_mongo()


settings = get_settings()

app = FastAPI(
    title="LangGraph DevOps Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"message": "Server is running", "status": "healthy", "stack": "fastapi-langgraph"}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(github.router, prefix="/api/github", tags=["github"])
app.include_router(session.router, prefix="/api/session", tags=["session"])


@app.exception_handler(Exception)
async def app_exception_handler(_request, exc: Exception) -> JSONResponse:
    status_code = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", None) or str(exc) or "Something went wrong"
    return JSONResponse(status_code=status_code, content={"message": detail})
