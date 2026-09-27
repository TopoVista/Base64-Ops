import re
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
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=1600, chunk_overlap=180)
        documents: list[Document] = []
        for file_path in self.workspace.iter_indexable_files(repo_path):
            relative = str(file_path.relative_to(repo_path)).replace("\\", "/")
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for chunk_index, chunk in enumerate(splitter.split_text(text)):
                start_offset = text.find(chunk)
                line_start = text[:start_offset].count("\n") + 1 if start_offset >= 0 else None
                line_end = line_start + chunk.count("\n") if line_start else None
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "sessionId": session["_id"],
                            "repoName": session["repoName"],
                            "kind": "repo",
                            "source": relative,
                            "chunkIndex": chunk_index,
                            "lineStart": line_start,
                            "lineEnd": line_end,
                            "createdAt": utc_now().isoformat(),
                        },
                    )
                )
        if not documents:
            return {"indexed": 0, "storage": "lexical"}

        # Persist usable lexical chunks before optional external embedding work.
        # This prevents an unavailable Atlas Search index, embeddings outage, or
        # unsupported Mongo deployment from turning a successful repository scan
        # into an empty RAG index.
        await db.rag_chunks.delete_many({"sessionId": session["_id"], "kind": "repo"})
        await db.rag_chunks.insert_many(self._document_rows(documents))
        result: dict[str, Any] = {"indexed": len(documents), "storage": "lexical", "vectorIndexed": False}
        if settings.rag_vector_enabled and settings.openai_api_key:
            try:
                await self._index_with_vector_store(documents)
                result["vectorIndexed"] = True
            except Exception:
                # The canonical lexical corpus is already safely persisted.
                result["vectorWarning"] = "Atlas vector indexing is unavailable; lexical retrieval remains active."
        return result

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
        if settings.rag_vector_enabled and settings.openai_api_key:
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
                        "lineStart": doc.metadata.get("lineStart"),
                        "lineEnd": doc.metadata.get("lineEnd"),
                    }
                    for doc in results
                ]
            except Exception:
                pass
        # Natural-language prompts almost never equal a source line verbatim.
        # Score meaningful query terms locally so lexical retrieval remains
        # useful without Atlas Search, then use a conservative repository-file
        # fallback when the prompt contains no literal source token.
        docs = await get_db().rag_chunks.find({"sessionId": session_id}).to_list(1_000)
        terms = self._lexical_terms(query)
        scored = [
            (self._lexical_score(document, terms), index, document)
            for index, document in enumerate(docs)
        ]
        matching = [item for item in scored if item[0] > 0]
        if matching:
            matching.sort(key=lambda item: (-item[0], item[1]))
            docs = [item[2] for item in matching[:limit]]
        else:
            docs = self._fallback_lexical_documents(docs, limit)
        return [
            {
                "source": doc.get("source") or doc.get("metadata", {}).get("source", "unknown"),
                "kind": doc.get("kind") or doc.get("metadata", {}).get("kind", "repo"),
                "excerpt": doc.get("text", "")[:700],
                "lineStart": doc.get("lineStart") or doc.get("metadata", {}).get("lineStart"),
                "lineEnd": doc.get("lineEnd") or doc.get("metadata", {}).get("lineEnd"),
            }
            for doc in docs
        ]

    @staticmethod
    def _document_rows(documents: list[Document]) -> list[dict[str, Any]]:
        return [
            {
                "_id": new_id("rag_"),
                "text": doc.page_content,
                "metadata": doc.metadata,
                "sessionId": doc.metadata["sessionId"],
                "kind": doc.metadata["kind"],
                "source": doc.metadata["source"],
                "lineStart": doc.metadata.get("lineStart"),
                "lineEnd": doc.metadata.get("lineEnd"),
                "createdAt": utc_now(),
            }
            for doc in documents
        ]

    @staticmethod
    def _lexical_terms(query: str) -> list[str]:
        ignored = {
            "about", "after", "agent", "anything", "check", "could", "does", "failure", "from", "have",
            "into", "latest", "please", "repository", "should", "that", "the", "this", "what", "when",
            "where", "which", "with", "would", "your",
        }
        return [
            term for term in re.findall(r"[a-zA-Z0-9_.-]{3,}", query.lower())
            if term not in ignored
        ][:24]

    @staticmethod
    def _lexical_score(document: dict[str, Any], terms: list[str]) -> int:
        text = str(document.get("text", "")).lower()
        source = str(document.get("source") or document.get("metadata", {}).get("source", "")).lower()
        return sum(text.count(term) + source.count(term) * 3 for term in terms)

    @staticmethod
    def _fallback_lexical_documents(documents: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        preferred = ("readme", "package.json", "pyproject.toml", "requirements", "docker", "compose", ".github/")
        return sorted(
            documents,
            key=lambda document: (
                not any(marker in str(document.get("source", "")).lower() for marker in preferred),
                str(document.get("source", "")),
            ),
        )[:limit]

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
        await get_db().rag_chunks.insert_many(self._document_rows(documents))
        if settings.rag_vector_enabled and settings.openai_api_key:
            try:
                await self._index_with_vector_store(documents)
            except Exception:
                # Runbooks remain available through lexical retrieval.
                pass

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
