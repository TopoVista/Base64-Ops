"""Safe, ephemeral SSE diagnostics for pasted CI output.

This is deliberately a thin diagnostic surface, not a second agent or mutation
pipeline. CI output and diffs are untrusted input, are redacted before every
event, and an executable fix can only be created by the normal evidence-backed
graph and its exact approval workflow.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.db.mongo import get_db
from app.git.actions import CIFailureCategory, WorkflowJobSummary, extract_failure_excerpts
from app.schemas.session import DiagnosticStreamRequest
from app.services.redaction_service import RedactionService
from app.utils.sse import sse_event

_MAX_STREAM_LOG_CHARS = 120_000
_MAX_STREAM_DIFF_CHARS = 120_000


def _analysis(excerpts: list[str], categories: list[CIFailureCategory]) -> str:
    category_text = ", ".join(category.replace("_", " ") for category in categories) or "unknown failure"
    if not excerpts:
        return (
            "## Diagnostic result\n\n"
            "No deterministic failure marker was found in the supplied bounded log. "
            "Review the failed step and run the normal repository investigation to collect evidence."
        )
    return (
        "## Diagnostic result\n\n"
        f"The log contains signals consistent with **{category_text}**. "
        "These signals are evidence, not proof of root cause.\n\n"
        "### Redacted failure window\n\n"
        "```text\n"
        f"{excerpts[0]}\n"
        "```\n\n"
        "Use the session investigation to correlate this output with the workflow at the failed SHA, "
        "changed files, and repository configuration before approving a remediation."
    )


async def stream_diagnostic(user_id: str, payload: DiagnosticStreamRequest) -> AsyncIterator[str]:
    """Emit a bounded diagnostic sequence without persisting raw logs or invoking mutations."""
    if payload.sessionSlugId:
        session = await get_db().sessions.find_one({"userId": user_id, "slugId": payload.sessionSlugId})
        if not session:
            yield sse_event("error", {"message": "Session not found."})
            return

    redactor = RedactionService()
    safe_log = redactor.redact(payload.failedLog[:_MAX_STREAM_LOG_CHARS])
    safe_diff = redactor.redact(payload.gitDiff[:_MAX_STREAM_DIFF_CHARS])
    input_truncated = len(payload.failedLog) > _MAX_STREAM_LOG_CHARS or len(payload.gitDiff) > _MAX_STREAM_DIFF_CHARS

    yield sse_event("state", {"label": "Analyzing log frames..."})
    job = WorkflowJobSummary(id=0, run_id=0, name="Pasted CI output")
    excerpts = extract_failure_excerpts(text=safe_log, run_id=0, job=job, truncated=input_truncated)
    categories = sorted({category for excerpt in excerpts for category in excerpt.categories}, key=str)
    markdown = _analysis([item.excerpt for item in excerpts], categories)
    for offset in range(0, len(markdown), 160):
        yield sse_event("log_analysis", {"delta": markdown[offset : offset + 160]})

    yield sse_event("state", {"label": "Synthesizing minimal code fix..."})
    # Do not manufacture an unvalidated write from arbitrary pasted output. The
    # existing graph may later produce a DeliveryPlan from repository evidence.
    yield sse_event(
        "code_fix",
        {
            "patch": None,
            "input_diff_present": bool(safe_diff.strip()),
            "message": (
                "No executable patch was generated from pasted CI output. "
                "Request a repository-backed fix to produce an exact, validated DeliveryPlan."
            ),
        },
    )
    yield sse_event(
        "complete",
        {
            "status": "diagnosed",
            "categories": [str(category) for category in categories],
            "excerpt_count": len(excerpts),
            "input_truncated": input_truncated,
            "requires_approval": True,
            "mutation_performed": False,
        },
    )
