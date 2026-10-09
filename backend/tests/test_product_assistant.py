import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.assistant import AssistantChatRequest
from app.services import product_assistant_service
from app.services.product_assistant_service import ProductAssistantService


def test_offline_guide_explains_github_setup() -> None:
    answer = ProductAssistantService()._offline_answer("How do I connect my GitHub repository?")

    assert "Connect GitHub" in answer
    assert "GITHUB_CLIENT_ID" in answer


def test_offline_guide_explains_approval_boundary() -> None:
    answer = ProductAssistantService()._offline_answer("Which actions need approval?")

    assert "Read-only diagnostics" in answer
    assert "approval" in answer.lower()


def test_offline_guide_explains_lexical_rag_baseline() -> None:
    answer = ProductAssistantService()._offline_answer("How does RAG indexing work?")

    assert "Lexical retrieval" in answer
    assert "MongoDB connectivity" in answer


def test_default_guide_answers_indexing_without_model() -> None:
    answer = ProductAssistantService._default_answer("How do I index a repository before asking?")

    assert answer is not None
    assert "do not need to send a chat message first" in answer
    assert "Production queues" in answer


def test_default_guide_uses_strict_readiness_for_deployment() -> None:
    answer = ProductAssistantService._default_answer("How do I deploy Base64 Ops?")

    assert answer is not None
    assert "/ready" in answer
    assert "/health" not in answer


async def test_product_guide_redacts_secrets_before_history_persistence(monkeypatch) -> None:
    persisted: list[dict] = []

    class Cursor:
        def sort(self, *_args):
            return self

        def limit(self, _limit: int):
            return self

        async def to_list(self, _limit: int) -> list[dict]:
            return []

    class Messages:
        def find(self, _query: dict) -> Cursor:
            return Cursor()

        async def insert_many(self, values: list[dict]) -> None:
            persisted.extend(values)

    class Conversations:
        async def update_one(self, *_args, **_kwargs) -> None:
            return None

    class Database:
        product_assistant_messages = Messages()
        product_assistant_conversations = Conversations()

    monkeypatch.setattr(product_assistant_service, "get_db", lambda: Database())
    result = await ProductAssistantService().answer(
        "user_a",
        "How do I index a repo? OPENAI_API_KEY=sk-secret-value-123456789",
        "guide_a",
    )

    assert "sk-secret-value-123456789" not in result["answer"]
    assert persisted
    assert all("sk-secret-value-123456789" not in item["content"] for item in persisted)
    assert "[REDACTED]" in persisted[0]["content"]


def test_default_guide_preserves_approval_boundary() -> None:
    answer = ProductAssistantService._default_answer("Can it push directly to main?")

    assert answer is not None
    assert "does not push directly to main" in answer


def test_guide_keeps_known_questions_deterministic_even_when_a_model_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        product_assistant_service,
        "get_settings",
        lambda: Settings(openai_api_key="configured"),
    )

    assert not ProductAssistantService().requires_model("How do I index a repository?")
    assert ProductAssistantService().requires_model("Explain a feature I invented")


def test_guide_history_budget_retains_most_recent_conversation_context() -> None:
    history = [
        {"role": "assistant", "content": "newest" * 2_000},
        {"role": "user", "content": "older" * 2_000},
        {"role": "assistant", "content": "oldest" * 2_000},
    ]

    bounded = ProductAssistantService._bounded_history(history)

    assert bounded
    assert bounded[-1]["content"].startswith("newest")
    assert sum(len(item["content"]) for item in bounded) <= 12_000


def test_guide_rejects_oversized_new_messages_before_model_or_persistence() -> None:
    with pytest.raises(ValidationError):
        AssistantChatRequest(message="x" * 4_001)


def test_model_limits_have_bounded_defaults() -> None:
    settings = Settings(_env_file=None)

    assert 5 <= settings.llm_timeout_seconds <= 120
    assert 0 <= settings.llm_max_retries <= 2
    assert settings.support_model == "gpt-5.6-luna"
