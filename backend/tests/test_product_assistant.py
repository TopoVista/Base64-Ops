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


def test_default_guide_preserves_approval_boundary() -> None:
    answer = ProductAssistantService._default_answer("Can it push directly to main?")

    assert answer is not None
    assert "does not push directly to main" in answer
