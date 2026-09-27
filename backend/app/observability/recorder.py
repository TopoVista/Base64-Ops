import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from app.db.mongo import get_db
from app.observability.models import RunTrace, TraceSpan
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now
from app.utils.ids import new_id


class TraceExporter(Protocol):
    async def export_trace(self, trace: dict[str, Any]) -> None: ...

    async def export_span(self, span: dict[str, Any]) -> None: ...


class LocalTraceExporter:
    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []

    async def export_trace(self, trace: dict[str, Any]) -> None:
        self.traces.append(trace)

    async def export_span(self, span: dict[str, Any]) -> None:
        self.spans.append(span)


class MongoTraceExporter:
    async def export_trace(self, trace: dict[str, Any]) -> None:
        await get_db().run_traces.update_one({"id": trace["id"]}, {"$set": trace}, upsert=True)

    async def export_span(self, span: dict[str, Any]) -> None:
        await get_db().trace_spans.update_one({"id": span["id"]}, {"$set": span}, upsert=True)


class TraceRecorder:
    def __init__(self, exporter: TraceExporter | None = None) -> None:
        self.redactor = RedactionService()
        self.exporter = exporter or LocalTraceExporter()

    async def start_run(self, user_id: str, session_id: str, run_id: str, repository_id: str | None) -> RunTrace:
        trace = RunTrace(
            id=new_id("trc_"),
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            repository_id=repository_id,
            started_at=utc_now(),
        )
        await self.exporter.export_trace(trace.model_dump())
        return trace

    async def complete_run(self, trace: RunTrace, status: str, errors: int = 0) -> RunTrace:
        trace.completed_at = utc_now()
        trace.status = status
        trace.error_count = errors
        trace.total_duration_ms = int((trace.completed_at - trace.started_at).total_seconds() * 1000)
        await self.exporter.export_trace(trace.model_dump())
        return trace

    @asynccontextmanager
    async def span(
        self,
        trace_id: str,
        name: str,
        safe_input_summary: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[TraceSpan]:
        started = time.monotonic()
        span = TraceSpan(
            id=new_id("spn_"),
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            name=name,
            started_at=utc_now(),
            safe_input_summary=self._safe(safe_input_summary or {}),
        )
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.error_type = type(exc).__name__
            span.error_message = self.redactor.redact(str(exc))[:500]
            raise
        finally:
            span.completed_at = utc_now()
            span.duration_ms = round((time.monotonic() - started) * 1000)
            span.safe_output_summary = self._safe(span.safe_output_summary)
            await self.exporter.export_span(span.model_dump())

    def _safe(self, value: dict[str, Any]) -> dict[str, Any]:
        return {key: self.redactor.redact(str(item))[:500] for key, item in value.items()}
