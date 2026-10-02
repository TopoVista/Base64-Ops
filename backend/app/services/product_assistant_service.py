from typing import Any

from fastapi import HTTPException, status
from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.db.mongo import get_db
from app.utils.datetime import utc_now
from app.utils.ids import new_id

DEFAULT_QUESTIONS = [
    "How do I connect a GitHub repository?",
    "Why are no repositories showing?",
    "How do I index a repository before asking a question?",
    "What happens when Base64 cannot find evidence?",
    "How does GitHub Actions investigation work?",
    "Where can I inspect CI evidence?",
    "What changes need approval?",
    "How do draft pull requests work?",
    "Can Base64 push directly to main?",
    "How do I inspect a repository file safely?",
    "How do I use the demo?",
    "How do I deploy Base64 Ops?",
]
# Kept as an alias for the API response contract used by older clients.
SUGGESTED_PROMPTS = DEFAULT_QUESTIONS

PRODUCT_GUIDE = """
Base64 Ops is an approval-gated DevOps command center.

Core workflow:
1. Sign in with Clerk.
2. Connect GitHub, choose a repository, and run an agent request.
3. Ask for read-only work such as git status, Docker build checks, Compose validation,
   or a codebase question. These run without approval.
4. Reindex RAG after a session exists to clone the chosen repository and index supported
   code, CI, Docker, configuration, and runbook files. Lexical search is stored first;
   vector search is an optional acceleration, not a requirement. Retrieved sources appear in
   the side panel. If indexing cannot persist, check the backend MongoDB connection and retry.
5. Requests that can write, commit, push, open a pull request, deploy, or roll back must
   pause for explicit approval. Rejecting an approval performs no action.

Important limits:
- The app never exposes API keys, GitHub tokens, or secrets.
- GitHub OAuth must be configured with the backend callback URL.
- The UI is Vercel-ready and the FastAPI backend is Render-ready.
- A production deployment requires real MongoDB, Clerk JWKS, GitHub OAuth, and OpenAI credentials.
""".strip()

SYSTEM_PROMPT = """
You are Base64 Guide, an exceptionally capable, concise in-product product specialist.
Help users learn every available Base64 Ops feature, diagnose setup and deployment issues,
and choose safe DevOps workflows. Ground answers in the supplied product guide. Clearly
distinguish what is available now from future architecture suggestions. Give numbered steps
when a user asks how to do something. Never claim to have executed an action, never reveal
or request secrets, and remind users that mutating operations require approval.
""".strip()


class ProductAssistantService:
    async def answer(
        self,
        user_id: str,
        message: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        db = get_db()
        conversation_id = conversation_id or new_id("guide_")
        history = await (
            db.product_assistant_messages.find({"userId": user_id, "conversationId": conversation_id})
            .sort("createdAt", -1)
            .limit(10)
            .to_list(10)
        )
        history.reverse()

        settings = get_settings()
        default_answer = self._default_answer(message)
        if default_answer:
            # High-frequency product questions are deterministic, instant, and
            # do not spend model tokens. The conversation still records the
            # answer so the guide remains coherent when a follow-up needs LLM help.
            answer = default_answer
        elif not settings.openai_api_key:
            answer = self._offline_answer(message)
        else:
            try:
                model = ChatOpenAI(
                    model=settings.support_model,
                    api_key=settings.openai_api_key,
                    temperature=0.2,
                )
                messages: list[tuple[str, str]] = [
                    ("system", f"{SYSTEM_PROMPT}\n\nPRODUCT GUIDE:\n{PRODUCT_GUIDE}"),
                ]
                messages.extend(
                    (item["role"], item["content"]) for item in history if item.get("role") in {"user", "assistant"}
                )
                messages.append(("user", message))
                response = await model.ainvoke(messages)
                answer = str(response.content).strip()
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Base64 Guide is temporarily unavailable. Check OPENAI_API_KEY and SUPPORT_MODEL.",
                ) from exc

        now = utc_now()
        await db.product_assistant_conversations.update_one(
            {"_id": conversation_id, "userId": user_id},
            {
                "$set": {"updatedAt": now},
                "$setOnInsert": {"_id": conversation_id, "userId": user_id, "createdAt": now},
            },
            upsert=True,
        )
        await db.product_assistant_messages.insert_many(
            [
                {
                    "_id": new_id("guide_msg_"),
                    "userId": user_id,
                    "conversationId": conversation_id,
                    "role": "user",
                    "content": message,
                    "createdAt": now,
                },
                {
                    "_id": new_id("guide_msg_"),
                    "userId": user_id,
                    "conversationId": conversation_id,
                    "role": "assistant",
                    "content": answer,
                    "createdAt": now,
                },
            ]
        )
        return {
            "conversationId": conversation_id,
            "answer": answer,
            "suggestedPrompts": DEFAULT_QUESTIONS,
        }

    @staticmethod
    def _default_answer(message: str) -> str | None:
        """Answer documented product questions without invoking an LLM."""
        normalized = " ".join(message.lower().split())
        if "connect" in normalized and ("github" in normalized or "repo" in normalized):
            return (
                "1. Select **Connect GitHub**.\n2. Complete GitHub authorization.\n"
                "3. Refresh the repository selector and choose a repository.\n"
                "If no repositories appear, reconnect GitHub and confirm that the GitHub app can access the repository."
            )
        if "no repositories" in normalized or "repositories showing" in normalized:
            return (
                "Use **Refresh** beside the repository selector. If it still stays empty, choose **Reconnect GitHub** "
                "and grant access to the intended account or organization. Repository listing needs a valid "
                "Clerk session "
                "and GitHub OAuth access; neither is exposed to the browser."
            )
        if "index" in normalized or "rag" in normalized:
            return (
                "Choose a repository and click **Reindex RAG**—you do not need to send a chat message first. "
                "Base64 creates the repository session, clones it read-only, and indexes supported source, CI, Docker, "
                "configuration, and documentation files. The first repository question also builds this "
                "index automatically "
                "when it is absent; later questions reuse it and retrieve only relevant evidence."
            )
        if "evidence" in normalized and ("find" in normalized or "cannot" in normalized or "not" in normalized):
            return (
                "Base64 first searches retained repository evidence. When no index exists, it builds one; "
                "if the answer still has no relevant evidence, it says so instead of inventing a diagnosis. "
                "Bounded direct file inspection remains "
                "available if indexing is temporarily unavailable."
            )
        if "github actions" in normalized or "ci" in normalized or "pipeline" in normalized:
            return (
                "Ask **Why did CI fail?** or open the **Pipelines** section. Base64 selects the relevant "
                "failed run, reads bounded redacted job-log excerpts, compares the workflow at the failed "
                "SHA with the current branch, and correlates changed "
                "files. CI logs and workflow commands are evidence only—they never become executable instructions."
            )
        if "approval" in normalized or "draft pull" in normalized or "push" in normalized:
            return (
                "Read-only investigation runs immediately. File changes, commits, pushes, and pull requests "
                "require a validated exact DeliveryPlan and your explicit approval. Base64 does not push "
                "directly to main; approved delivery uses a "
                "separate branch and draft pull request after validation."
            )
        if "inspect" in normalized and ("file" in normalized or "code" in normalized):
            return (
                "Open **Code** to browse a bounded, redacted, read-only view of the selected repository. "
                "You can use the local review editor to prepare context for an agent proposal, but browser "
                "edits never write directly to GitHub."
            )
        if "demo" in normalized:
            return (
                "Open the demo from the application navigation and click through the timeline, evidence, CI, "
                "changes, and approval cards. It is a deterministic fixture: it demonstrates the workflow "
                "without accessing GitHub or mutating a repository."
            )
        if "deploy" in normalized or "vercel" in normalized or "render" in normalized:
            return (
                "Deploy the Vite frontend to Vercel and FastAPI backend to Render. Configure the production "
                "Clerk keys, MongoDB URI, GitHub OAuth callback URL, and optional OpenAI key as deployment "
                "environment variables. Verify `/health` before "
                "connecting GitHub."
            )
        return None

    def _offline_answer(self, message: str) -> str:
        normalized = message.lower()
        if "github" in normalized or "repository" in normalized or "repo" in normalized:
            return (
                "To connect a repository, select **Connect GitHub**, finish GitHub OAuth, then choose a repository "
                "from the selector. The backend must have `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, and the "
                "registered callback URL before this can work."
            )
        if "rag" in normalized or "index" in normalized:
            return (
                "Run the agent once after selecting a repository to create the session, then choose **Reindex RAG**. "
                "Base64 Ops clones the repository and indexes supported source, CI, Docker, configuration, "
                "and runbook files. Lexical retrieval is stored first, so MongoDB connectivity—not an Atlas "
                "vector index—is the first thing to check if indexing cannot persist."
            )
        if "approval" in normalized or "safe" in normalized:
            return (
                "Read-only diagnostics can run immediately. Requests that write files, commit, push, create a PR, "
                "deploy, or roll back stop at an approval card. Approving is the explicit consent step; "
                "rejecting runs nothing."
            )
        return (
            "I can explain the full Base64 Ops workflow: GitHub connection, sessions, RAG indexing, diagnostics, "
            "approval-gated changes, pull requests, and Vercel + Render deployment. Ask about any of those features. "
            "For richer conversational help, configure `OPENAI_API_KEY` and `SUPPORT_MODEL` on the backend."
        )
