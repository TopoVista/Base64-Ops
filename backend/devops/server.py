"""FastAPI entry point for ``uvicorn devops.server:app`` deployments.

It intentionally reuses the primary Base64 Ops application. This avoids a
second service with divergent authentication, CORS, redaction, or delivery
controls while exposing ``/api/stream-diagnostic`` and ``/api/apply-patch``.
"""

from app.main import app

__all__ = ["app"]
