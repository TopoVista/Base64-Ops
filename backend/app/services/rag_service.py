import asyncio
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
        # Reading/splitting a repository is synchronous filesystem/CPU work.
        # Keep it off the worker event loop so the durable job's lease can
        # continue heartbeating while a large (but bounded) corpus is built.
        documents, index_stats = await asyncio.to_thread(
            self._build_repository_documents,
            session,
            repo_path,
            max_files=settings.rag_index_max_files,
            max_total_bytes=settings.rag_index_max_total_bytes,
            max_chunks=settings.rag_index_max_chunks,
        )
        if not documents:
            return {"indexed": 0, "storage": "lexical", **index_stats}

        # Persist usable lexical chunks before optional external embedding work.
        # This prevents an unavailable Atlas Search index, embeddings outage, or
        # unsupported Mongo deployment from turning a successful repository scan
        # into an empty RAG index.
        # A compound multikey index keeps lexical retrieval bounded even for
        # repositories with far more chunks than a process should scan.
        await db.rag_chunks.create_index([("sessionId", 1), ("searchTerms", 1)])
        await db.rag_chunks.delete_many({"sessionId": session["_id"], "kind": "repo"})
        await db.rag_chunks.insert_many(self._document_rows(documents))
        result: dict[str, Any] = {
            "indexed": len(documents),
            "storage": "lexical",
            "vectorIndexed": False,
            **index_stats,
        }
        if settings.rag_vector_enabled and settings.openai_api_key:
            try:
                await self._index_with_vector_store(documents)
                result["vectorIndexed"] = True
            except Exception:
                # The canonical lexical corpus is already safely persisted.
                result["vectorWarning"] = "Atlas vector indexing is unavailable; lexical retrieval remains active."
        return result

    def _build_repository_documents(
        self,
        session: dict[str, Any],
        repo_path: Path,
        *,
        max_files: int,
        max_total_bytes: int,
        max_chunks: int,
    ) -> tuple[list[Document], dict[str, int | bool]]:
        """Create a bounded lexical corpus without executing repository code.

        ``iter_indexable_files`` already filters extensions and individual
        files. This second limit is aggregate: many individually valid files
        must not exhaust a small worker or create an unexpectedly large Atlas
        write. Files are selected deterministically in workspace traversal
        order so an incomplete index is honest and repeatable.
        """

        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=1600, chunk_overlap=180)
        documents: list[Document] = []
        selected_files = 0
        selected_bytes = 0
        skipped_files = 0
        truncated = False
        for file_path in self.workspace.iter_indexable_files(repo_path):
            try:
                file_bytes = file_path.stat().st_size
            except OSError:
                skipped_files += 1
                continue
            if selected_files >= max_files or selected_bytes + file_bytes > max_total_bytes:
                skipped_files += 1
                truncated = True
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped_files += 1
                continue
            selected_files += 1
            selected_bytes += file_bytes
            relative = str(file_path.relative_to(repo_path)).replace("\\", "/")
            for chunk_index, chunk in enumerate(splitter.split_text(text)):
                if len(documents) >= max_chunks:
                    truncated = True
                    break
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
            if truncated and len(documents) >= max_chunks:
                break
        return documents, {
            "filesIndexed": selected_files,
            "indexedBytes": selected_bytes,
            "skippedFiles": skipped_files,
            "truncated": truncated,
        }

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
        terms = self._lexical_terms(query)
        candidate_limit = max(100, min(500, limit * 25))
        db = get_db()
        if terms:
            # `searchTerms` is generated server-side during indexing. It
            # narrows the candidate set before scoring full text and avoids a
            # first-N-chunks bias for large repositories.
            docs = await (
                db.rag_chunks.find({"sessionId": session_id, "searchTerms": {"$in": terms}})
                .limit(candidate_limit)
                .to_list(candidate_limit)
            )
        else:
            docs = []

        # Older indexes predate `searchTerms`; a bounded deterministic
        # fallback keeps them usable until the next reindex.
        if not docs:
            docs = await (
                db.rag_chunks.find({"sessionId": session_id})
                .sort("source", 1)
                .limit(candidate_limit)
                .to_list(candidate_limit)
            )
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
                "searchTerms": RagService._search_terms(
                    f"{doc.metadata.get('source', '')}\n{doc.page_content}"
                ),
            }
            for doc in documents
        ]

    @staticmethod
    def _search_terms(value: str) -> list[str]:
        """Return compact, deterministic terms for a Mongo multikey index."""
        terms = re.findall(r"[a-zA-Z0-9_.-]{2,}", value.lower())
        return list(dict.fromkeys(terms))[:256]

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
