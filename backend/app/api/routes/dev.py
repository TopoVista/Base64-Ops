from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.db.mongo import get_db

router = APIRouter(prefix="/dev", tags=["dev"])


@router.get("/runs/{run_id}")
async def get_dev_run_inspector(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    db = get_db()

    trace = await db.run_traces.find_one({"$or": [{"run_id": run_id}, {"id": run_id}], "user_id": user_id})
    if not trace:
        # Fallback query by session_id if run_id trace wasn't created yet
        trace = await db.run_traces.find_one({"session_id": run_id, "user_id": user_id})

    spans = []
    if trace:
        cursor = db.trace_spans.find({"trace_id": trace["id"]}).sort("started_at", 1)
        spans = [doc async for doc in cursor]

    plan = await db.delivery_plans.find_one({"$or": [{"runId": run_id}, {"run_id": run_id}], "userId": user_id})
    approval = None
    if plan:
        approval = await db.approvals.find_one({"deliveryPlanId": plan["id"], "userId": user_id})

    delivery_result = None
    if plan:
        delivery_result = await db.delivery_results.find_one({"delivery_plan_id": plan["id"]})

    evidence_records = []
    cursor_evd = db.evidence.find({"$or": [{"runId": run_id}, {"run_id": run_id}], "userId": user_id})
    evidence_records = [doc async for doc in cursor_evd]
    ci_investigation = await db.ci_investigations.find_one({"userId": user_id, "runId": run_id})

    return {
        "run_id": run_id,
        "trace": trace,
        "spans": spans,
        "delivery_plan": plan,
        "approval": approval,
        "delivery_result": delivery_result,
        "ci_investigation": ci_investigation.get("context") if ci_investigation else None,
        "evidence_count": len(evidence_records),
        "evidence": [
            {
                "id": doc.get("id"),
                "path": doc.get("path"),
                "source": doc.get("source"),
                "retrieval_method": doc.get("retrievalMethod", "rag"),
                "score": doc.get("score"),
            }
            for doc in evidence_records
        ],
    }
