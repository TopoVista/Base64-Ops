from app.api.routes import assistant
from app.core.agent_run_guard import AgentRunLease
from app.schemas.assistant import AssistantChatRequest


async def test_model_backed_guide_request_uses_the_browser_idempotency_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Guard:
        async def acquire(self, user_id):  # noqa: ANN001, ANN201
            captured["leased_user"] = user_id
            return AgentRunLease(key="lease", token="token")

        async def release(self, lease):  # noqa: ANN001, ANN201
            captured["released"] = lease

    class Usage:
        async def consume_agent_run(self, user, *, request_id=None):  # noqa: ANN001, ANN201
            captured["usage_user"] = user["_id"]
            captured["request_id"] = request_id

        async def record_model_usage(self, user_id, model_usage):  # noqa: ANN001, ANN201
            captured["metered_user"] = user_id
            captured["model_usage"] = model_usage

    class Guide:
        def requires_model(self, message):  # noqa: ANN001, ANN201
            return True

        async def answer(self, user_id, message, conversation_id):  # noqa: ANN001, ANN201
            return {
                "conversationId": conversation_id or "guide",
                "answer": "safe",
                "suggestedPrompts": [],
                "_modelUsage": {"total_tokens": 12},
            }

    monkeypatch.setattr(assistant, "agent_run_guard", Guard())
    monkeypatch.setattr(assistant, "usage_service", Usage())
    monkeypatch.setattr(assistant, "service", Guide())

    result = await assistant.chat(
        AssistantChatRequest(message="Explain an undocumented feature", clientRequestId="request-123"),
        {"_id": "user-a"},
    )

    assert result["answer"] == "safe"
    assert captured["leased_user"] == "user-a"
    assert captured["usage_user"] == "user-a"
    assert captured["request_id"] == "request-123"
    assert captured["metered_user"] == "user-a"
    assert captured["model_usage"] == {"total_tokens": 12}
    assert "_modelUsage" not in result
    assert isinstance(captured["released"], AgentRunLease)
