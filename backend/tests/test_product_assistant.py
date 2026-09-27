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
