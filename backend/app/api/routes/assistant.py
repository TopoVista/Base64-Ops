from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse
from app.services.product_assistant_service import SUGGESTED_PROMPTS, ProductAssistantService

router = APIRouter()
service = ProductAssistantService()


@router.get("/starter-prompts")
async def starter_prompts(user: dict = Depends(get_current_user)) -> dict:
    return {"prompts": SUGGESTED_PROMPTS}


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(
    payload: AssistantChatRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return await service.answer(user["_id"], payload.message, payload.conversation_id)
