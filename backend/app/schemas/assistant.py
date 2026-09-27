from pydantic import BaseModel, Field


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    conversation_id: str | None = Field(default=None, alias="conversationId")


class AssistantChatResponse(BaseModel):
    conversation_id: str = Field(alias="conversationId")
    answer: str
    suggested_prompts: list[str] = Field(alias="suggestedPrompts")
