"""Public orchestration boundary for diagnostic dashboard integrations.

The implementation remains in ``app.services.diagnostic_stream_service`` so
the dashboard shares the production redaction, evidence, and approval systems.
"""

from collections.abc import AsyncIterator

from app.schemas.session import DiagnosticStreamRequest
from app.services.diagnostic_stream_service import stream_diagnostic


class DiagnosticAgent:
    """Typed façade for consumers that need the dashboard SSE sequence."""

    async def stream(self, *, user_id: str, request: DiagnosticStreamRequest) -> AsyncIterator[str]:
        async for event in stream_diagnostic(user_id, request):
            yield event
