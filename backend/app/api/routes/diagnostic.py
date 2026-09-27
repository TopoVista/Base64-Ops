"""Authenticated streaming diagnostics and approval-bound patch execution."""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.schemas.session import ApplyApprovedPatchRequest, ApprovalDecisionRequest, DiagnosticStreamRequest
from app.services.diagnostic_stream_service import stream_diagnostic
from app.services.session_service import decide_approval

router = APIRouter()


@router.post("/stream-diagnostic")
async def stream(payload: DiagnosticStreamRequest, user: dict = Depends(get_current_user)) -> StreamingResponse:
    return StreamingResponse(
        stream_diagnostic(user["_id"], payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/apply-patch")
async def apply_approved_patch(payload: ApplyApprovedPatchRequest, user: dict = Depends(get_current_user)) -> dict:
    """Compatibility action: executes only a pre-existing exact approval record."""
    return await decide_approval(
        user["_id"],
        payload.slugId,
        payload.approvalId,
        ApprovalDecisionRequest(decision="approve", note="Approved from diagnostic dashboard"),
    )
