from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.billing.usage_service import UsageService
from app.core.agent_run_guard import AgentRunGuard
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse
from app.services.product_assistant_service import SUGGESTED_PROMPTS, ProductAssistantService

router = APIRouter()
service = ProductAssistantService()
usage_service = UsageService()
agent_run_guard = AgentRunGuard()


@router.get("/starter-prompts")
async def starter_prompts(user: dict = Depends(get_current_user)) -> dict:
    return {"prompts": SUGGESTED_PROMPTS}


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(
    payload: AssistantChatRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    if not service.requires_model(payload.message):
        return await service.answer(user["_id"], payload.message, payload.conversation_id)

    lease = await agent_run_guard.acquire(str(user["_id"]))
    try:
        await usage_service.consume_agent_run(user, request_id=payload.client_request_id)
        response = await service.answer(user["_id"], payload.message, payload.conversation_id)
        model_usage = response.pop("_modelUsage", None)
        if model_usage:
            try:
                await usage_service.record_model_usage(str(user["_id"]), model_usage)
            except Exception:
                # Token telemetry must not hide a completed support response.
                pass
        return response
    finally:
        await agent_run_guard.release(lease)
