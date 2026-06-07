from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from app.core.config import get_settings
from app.db.mongo import get_db
from app.services.workspace_service import WorkspaceService
from app.utils.chunking import chunk_text
from app.utils.datetime import utc_now
from app.utils.ids import new_id


class RagService:
    def __init__(self) -> None:
        self.workspace = WorkspaceService()

    async def index_workspace(self, session: dict[str, Any], repo_path: Path) -> dict[str, Any]:
        db = get_db()
        settings = get_settings()
        await db.rag_chunks.delete_many({"sessionId": session["_id"], "kind": "repo"})
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=1600, chunk_overlap=180)
        documents: list[Document] = []
        for file_path in self.workspace.iter_indexable_files(repo_path):
            relative = str(file_path.relative_to(repo_path)).replace("\\", "/")
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for chunk_index, chunk in enumerate(splitter.split_text(text)):
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "sessionId": session["_id"],
                            "repoName": session["repoName"],
                            "kind": "repo",
                            "source": relative,
                            "chunkIndex": chunk_index,
                            "createdAt": utc_now().isoformat(),
                        },
                    )
                )
        if not documents:
            return {"indexed": 0}
        if settings.openai_api_key:
            await self._index_with_vector_store(documents)
        else:
            await db.rag_chunks.insert_many(
                [
                    {
                        "_id": new_id("rag_"),
                        "text": doc.page_content,
                        "metadata": doc.metadata,
                        "sessionId": session["_id"],
                        "kind": "repo",
                        "source": doc.metadata["source"],
                        "createdAt": utc_now(),
                    }
                    for doc in documents
                ]
            )
        return {"indexed": len(documents)}

    async def add_runbook(
        self,
        user_id: str,
        title: str,
        content: str,
        tags: list[str],
    ) -> dict[str, Any]:
        db = get_db()
        runbook = {
            "_id": new_id("run_"),
            "userId": user_id,
            "title": title,
            "content": content,
            "tags": tags,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        await db.runbooks.insert_one(runbook)
        await self._index_runbook(runbook)
        return runbook

    async def list_runbooks(self, user_id: str) -> list[dict[str, Any]]:
        return await get_db().runbooks.find({"userId": user_id}).sort("createdAt", -1).to_list(100)

    async def retrieve(self, session_id: str, query: str, limit: int = 6) -> list[dict[str, Any]]:
        settings = get_settings()
        if settings.openai_api_key:
            try:
                vector_store = self._vector_store()
                results = vector_store.similarity_search(
                    query,
                    k=limit,
                    pre_filter={"sessionId": {"$eq": session_id}},
                )
                return [
                    {
                        "source": doc.metadata.get("source", "unknown"),
                        "kind": doc.metadata.get("kind", "repo"),
                        "excerpt": doc.page_content[:700],
                    }
                    for doc in results
                ]
            except Exception:
                pass
        cursor = get_db().rag_chunks.find(
            {
                "sessionId": session_id,
                "$or": [
                    {"text": {"$regex": query[:80], "$options": "i"}},
                    {"source": {"$regex": query[:80], "$options": "i"}},
                ],
            }
        ).limit(limit)
        docs = await cursor.to_list(limit)
        return [
            {
                "source": doc.get("source") or doc.get("metadata", {}).get("source", "unknown"),
                "kind": doc.get("kind") or doc.get("metadata", {}).get("kind", "repo"),
                "excerpt": doc.get("text", "")[:700],
            }
            for doc in docs
        ]

    async def _index_runbook(self, runbook: dict[str, Any]) -> None:
        settings = get_settings()
        documents = [
            Document(
                page_content=chunk,
                metadata={
                    "sessionId": "global",
                    "userId": runbook["userId"],
                    "kind": "runbook",
                    "source": runbook["title"],
                    "runbookId": runbook["_id"],
                },
            )
            for chunk in chunk_text(runbook["content"])
        ]
        if not documents:
            return
        if settings.openai_api_key:
            await self._index_with_vector_store(documents)
        else:
            await get_db().rag_chunks.insert_many(
                [
                    {
                        "_id": new_id("rag_"),
                        "text": doc.page_content,
                        "metadata": doc.metadata,
                        "sessionId": "global",
                        "kind": "runbook",
                        "source": runbook["title"],
                        "createdAt": utc_now(),
                    }
                    for doc in documents
                ]
            )

    async def _index_with_vector_store(self, documents: list[Document]) -> None:
        vector_store = self._vector_store()
        vector_store.add_documents(documents)

    def _vector_store(self):
        from langchain_mongodb import MongoDBAtlasVectorSearch
        from langchain_openai import OpenAIEmbeddings
        from pymongo import MongoClient

        settings = get_settings()
        client = MongoClient(settings.mongo_uri)
        collection = client[settings.mongo_db_name]["rag_chunks"]
        embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
        return MongoDBAtlasVectorSearch(
            collection=collection,
            embedding=embeddings,
            index_name=settings.vector_index_name,
            text_key="text",
            relevance_score_fn="cosine",
        )
