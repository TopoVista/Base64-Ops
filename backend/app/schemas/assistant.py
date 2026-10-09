from pydantic import BaseModel, Field


class AssistantChatRequest(BaseModel):
    # The guide keeps a deliberately small support context. Longer repository
    # content belongs in the evidence-first investigation flow, not a general
    # product-help prompt that could multiply provider spend.
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, alias="conversationId")
    # The guide can invoke a model for questions outside its deterministic
    # support catalogue. Scope retries to a server-side hashed request key so
    # a dropped browser response cannot consume a second entitlement.
    client_request_id: str | None = Field(default=None, alias="clientRequestId", max_length=128)


class AssistantChatResponse(BaseModel):
    conversation_id: str = Field(alias="conversationId")
    answer: str
    suggested_prompts: list[str] = Field(alias="suggestedPrompts")
