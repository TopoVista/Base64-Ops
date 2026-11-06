import asyncio
import hashlib
import re
from collections import defaultdict
from datetime import datetime
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

    async def index_workspace(
        self, session: dict[str, Any], repo_path: Path, *, revision: str | None = None
    ) -> dict[str, Any]:
        db = get_db()
        settings = get_settings()
        # Reading/splitting a repository is synchronous filesystem/CPU work.
        # Keep it off the worker event loop so the durable job's lease can
        # continue heartbeating while a large (but bounded) corpus is built.
        documents, index_stats = await asyncio.to_thread(
            self._build_repository_documents,
            session,
            repo_path,
            revision=revision,
            max_files=settings.rag_index_max_files,
            max_total_bytes=settings.rag_index_max_total_bytes,
            max_chunks=settings.rag_index_max_chunks,
        )
        # Persist usable lexical chunks before optional external embedding work.
        # This prevents an unavailable Atlas Search index, embeddings outage, or
        # unsupported Mongo deployment from turning a successful repository scan
        # into an empty RAG index.
        # A compound multikey index keeps lexical retrieval bounded even for
        # repositories with far more chunks than a process should scan.
        await db.rag_chunks.create_index([("sessionId", 1), ("searchTerms", 1)])
        await db.rag_chunks.create_index([("sessionId", 1), ("kind", 1), ("source", 1), ("contentHash", 1)])
        await db.rag_chunks.create_index(
            [("repositoryId", 1), ("kind", 1), ("validAt", 1), ("knownAt", 1), ("searchTerms", 1)],
            name="rag_temporal_lexical",
        )
        rows = self._document_rows(documents)
        vector_requested = bool(settings.rag_vector_enabled and settings.openai_api_key)
        if revision:
            persistence = await self._persist_revision_rows(
                collection=db.rag_chunks,
                session_id=str(session["_id"]),
                revision=revision,
                rows=rows,
            )
            await self._record_snapshot(session=session, revision=revision, rows=rows, index_stats=index_stats)
        elif vector_requested:
            # The external vector collection cannot safely prove that a
            # document deletion/replacement succeeded through the lexical
            # collection alone. Preserve the prior full-replace behavior when
            # the optional accelerator is enabled rather than leaving stale
            # embeddings behind.
            await db.rag_chunks.delete_many({"sessionId": session["_id"], "kind": "repo"})
            if rows:
                await db.rag_chunks.insert_many(rows)
            persistence = {
                "indexMode": "full_replace_vector_enabled",
                "chunksInserted": len(rows),
                "chunksReused": 0,
                "chunksRemoved": 0,
            }
        else:
            persistence = await self._persist_incremental_lexical_rows(
                collection=db.rag_chunks,
                session_id=str(session["_id"]),
                rows=rows,
                max_chunks=settings.rag_index_max_chunks,
            )
        result: dict[str, Any] = {
            "indexed": len(documents),
            "storage": "lexical",
            "vectorIndexed": False,
            **index_stats,
            **persistence,
        }
        if revision:
            result["revision"] = revision
            result["freshness"] = "exact_revision"
            result["snapshotStatus"] = "ready" if rows else "empty"
        if vector_requested and documents:
            try:
                await self._index_with_vector_store(documents)
                result["vectorIndexed"] = True
            except Exception:
                # The canonical lexical corpus is already safely persisted.
                result["vectorWarning"] = "Atlas vector indexing is unavailable; lexical retrieval remains active."
        return result

    async def _persist_revision_rows(
        self,
        *,
        collection: Any,
        session_id: str,
        revision: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, int | str]:
        """Replace only one immutable commit snapshot, never the latest corpus."""

        query = {"sessionId": session_id, "kind": "repo", "validAt": revision}
        existing = await collection.find(query, {"_id": 1}).to_list(20_001)
        await collection.delete_many(query)
        if rows:
            await collection.insert_many(rows)
        return {
            "indexMode": "commit_snapshot_replace",
            "chunksInserted": len(rows),
            "chunksReused": 0,
            "chunksRemoved": len(existing),
        }

    async def _record_snapshot(
        self,
        *,
        session: dict[str, Any],
        revision: str,
        rows: list[dict[str, Any]],
        index_stats: dict[str, int | bool],
    ) -> None:
        """Publish the temporal snapshot only after its chunk set is durable."""

        known_at = utc_now()
        corpus_hash = hashlib.sha256(
            "\n".join(f"{row['source']}:{row['contentHash']}" for row in rows).encode("utf-8")
        ).hexdigest()
        session_id = str(session["_id"])
        repository_id = str(session.get("repositoryId") or session.get("repoUrl") or session_id)
        await get_db().rag_snapshots.update_one(
            {"sessionId": session_id, "repositoryId": repository_id, "commitSha": revision},
            {
                "$set": {
                    "sessionId": session_id,
                    "userId": session.get("userId"),
                    "repositoryId": repository_id,
                    "commitSha": revision,
                    "validAt": revision,
                    "knownAt": known_at,
                    "status": "ready" if rows else "empty",
                    "chunkCount": len(rows),
                    "corpusHash": corpus_hash,
                    "truncated": bool(index_stats.get("truncated")),
                    "updatedAt": known_at,
                },
                "$setOnInsert": {"_id": new_id("rgs_"), "createdAt": known_at},
            },
            upsert=True,
        )

    async def snapshot_freshness(
        self,
        *,
        session_id: str,
        repository_id: str,
        valid_at: str,
        known_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return one exact revision snapshot, never a nearest/latest substitute."""

        query: dict[str, Any] = {
            "sessionId": session_id,
            "repositoryId": repository_id,
            "commitSha": valid_at,
        }
        if known_at is not None:
            query["knownAt"] = {"$lte": known_at}
        return await get_db().rag_snapshots.find_one(query)

    async def _persist_incremental_lexical_rows(
        self,
        *,
        collection: Any,
        session_id: str,
        rows: list[dict[str, Any]],
        max_chunks: int,
    ) -> dict[str, int | str]:
        """Reconcile a bounded lexical corpus by source content hash.

        A source is reused only when its persisted chunk count and exact file
        content hash match the freshly built deterministic chunks. If an older
        corpus exceeds the configured bound or lacks trustworthy source/hash
        fields, the method fails closed to a lexical full replacement instead
        of silently retaining stale context.
        """

        query = {"sessionId": session_id, "kind": "repo"}
        cursor = collection.find(query, {"source": 1, "contentHash": 1, "metadata.contentHash": 1})
        existing_rows = await cursor.limit(max_chunks + 1).to_list(max_chunks + 1)
        has_untrusted_existing_row = any(
            not isinstance(row.get("source"), str)
            or not row.get("source")
            or not isinstance(
                row.get("contentHash")
                or (row.get("metadata", {}).get("contentHash") if isinstance(row.get("metadata"), dict) else None),
                str,
            )
            for row in existing_rows
        )
        if len(existing_rows) > max_chunks or has_untrusted_existing_row:
            await collection.delete_many(query)
            if rows:
                await collection.insert_many(rows)
            return {
                "indexMode": (
                    "full_replace_existing_corpus_exceeded_bound"
                    if len(existing_rows) > max_chunks
                    else "full_replace_untrusted_existing_corpus"
                ),
                "chunksInserted": len(rows),
                "chunksReused": 0,
                "chunksRemoved": len(existing_rows),
            }

        existing_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in existing_rows:
            source = row.get("source")
            if isinstance(source, str) and source:
                existing_by_source[source].append(row)

        fresh_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            fresh_by_source[row["source"]].append(row)

        unchanged_sources: set[str] = set()
        for source, fresh_rows in fresh_by_source.items():
            existing_for_source = existing_by_source.get(source, [])
            fresh_hashes = {str(row.get("contentHash") or "") for row in fresh_rows}
            existing_hashes = {
                str(row.get("contentHash") or row.get("metadata", {}).get("contentHash") or "")
                for row in existing_for_source
            }
            if (
                len(fresh_hashes) == 1
                and fresh_hashes == existing_hashes
                and "" not in fresh_hashes
                and len(fresh_rows) == len(existing_for_source)
            ):
                unchanged_sources.add(source)

        changed_sources = set(fresh_by_source) - unchanged_sources
        stale_sources = set(existing_by_source) - set(fresh_by_source)
        chunks_removed = 0
        for source in sorted(changed_sources | stale_sources):
            chunks_removed += len(existing_by_source.get(source, []))
            await collection.delete_many({**query, "source": source})

        rows_to_insert = [
            row for source in sorted(changed_sources) for row in fresh_by_source[source]
        ]
        if rows_to_insert:
            await collection.insert_many(rows_to_insert)
        return {
            "indexMode": "incremental_lexical",
            "chunksInserted": len(rows_to_insert),
            "chunksReused": sum(len(fresh_by_source[source]) for source in unchanged_sources),
            "chunksRemoved": chunks_removed,
        }

    def _build_repository_documents(
        self,
        session: dict[str, Any],
        repo_path: Path,
        *,
        revision: str | None = None,
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
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
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
                            "contentHash": content_hash,
                            **(
                                {"validAt": revision, "commitSha": revision, "knownAt": utc_now()}
                                if revision
                                else {}
                            ),
                            "repositoryId": session.get("repositoryId") or session.get("repoUrl"),
                            "parserVersion": "base64-rag-parser-v1",
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

    async def retrieve(
        self,
        session_id: str,
        query: str,
        limit: int = 6,
        *,
        valid_at: str | None = None,
        known_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if settings.rag_vector_enabled and settings.openai_api_key:
            vector_client = None
            try:
                vector_store, vector_client = self._vector_store()
                pre_filter: dict[str, Any] = {"sessionId": {"$eq": session_id}}
                if valid_at:
                    pre_filter["validAt"] = {"$eq": valid_at}
                if known_at:
                    pre_filter["knownAt"] = {"$lte": known_at}
                # The vector adapter is synchronous. Keep its Atlas work off
                # the API event loop and close the short-lived client after
                # each bounded query so optional vector search cannot leak
                # pools during a transient Atlas outage.
                results = await asyncio.to_thread(
                    vector_store.similarity_search,
                    query,
                    k=limit,
                    pre_filter=pre_filter,
                )
                return [
                    {
                        "source": doc.metadata.get("source", "unknown"),
                        "kind": doc.metadata.get("kind", "repo"),
                        "excerpt": doc.page_content[:700],
                        "lineStart": doc.metadata.get("lineStart"),
                        "lineEnd": doc.metadata.get("lineEnd"),
                        "validAt": doc.metadata.get("validAt"),
                        "knownAt": doc.metadata.get("knownAt"),
                        "freshness": "valid_at" if valid_at else "current",
                    }
                    for doc in results
                ]
            except Exception:
                pass
            finally:
                if vector_client is not None:
                    vector_client.close()
        # Natural-language prompts almost never equal a source line verbatim.
        # Score meaningful query terms locally so lexical retrieval remains
        # useful without Atlas Search, then use a conservative repository-file
        # fallback when the prompt contains no literal source token.
        terms = self._lexical_terms(query)
        candidate_limit = max(100, min(500, limit * 25))
        db = get_db()
        base_query: dict[str, Any] = {"sessionId": session_id}
        if valid_at:
            # Historical retrieval must not silently fall back to a newer
            # corpus.  A missing exact snapshot is an honest empty result.
            base_query["validAt"] = valid_at
        if known_at:
            base_query["knownAt"] = {"$lte": known_at}
        if terms:
            # `searchTerms` is generated server-side during indexing. It
            # narrows the candidate set before scoring full text and avoids a
            # first-N-chunks bias for large repositories.
            docs = await (
                db.rag_chunks.find({**base_query, "searchTerms": {"$in": terms}})
                .limit(candidate_limit)
                .to_list(candidate_limit)
            )
        else:
            docs = []

        # Older indexes predate `searchTerms`; a bounded deterministic
        # fallback keeps them usable until the next reindex.
        if not docs:
            docs = await (
                db.rag_chunks.find(base_query)
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
                "validAt": doc.get("validAt") or doc.get("metadata", {}).get("validAt"),
                "knownAt": doc.get("knownAt") or doc.get("metadata", {}).get("knownAt"),
                "freshness": "valid_at" if valid_at else "current",
            }
            for doc in docs
        ]

    @staticmethod
    def _document_rows(documents: list[Document]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for doc in documents:
            content_hash = doc.metadata.get("contentHash")
            if not isinstance(content_hash, str) or not content_hash:
                content_hash = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()
            metadata = {**doc.metadata, "contentHash": content_hash}
            rows.append(
                {
                    "_id": new_id("rag_"),
                    "text": doc.page_content,
                    "metadata": metadata,
                    "sessionId": metadata["sessionId"],
                    "kind": metadata["kind"],
                    "source": metadata["source"],
                    "contentHash": content_hash,
                    "validAt": metadata.get("validAt"),
                    "knownAt": metadata.get("knownAt"),
                    "commitSha": metadata.get("commitSha"),
                    "repositoryId": metadata.get("repositoryId"),
                    "parserVersion": metadata.get("parserVersion"),
                    "lineStart": metadata.get("lineStart"),
                    "lineEnd": metadata.get("lineEnd"),
                    "createdAt": utc_now(),
                    "searchTerms": RagService._search_terms(
                        f"{metadata.get('source', '')}\n{doc.page_content}"
                    ),
                }
            )
        return rows

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
        vector_store, vector_client = self._vector_store()
        try:
            await asyncio.to_thread(vector_store.add_documents, documents)
        finally:
            vector_client.close()

    def _vector_store(self):
        from langchain_mongodb import MongoDBAtlasVectorSearch
        from langchain_openai import OpenAIEmbeddings
        from pymongo import MongoClient

        settings = get_settings()
        client = MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=settings.mongo_connect_timeout_ms,
            connectTimeoutMS=settings.mongo_connect_timeout_ms,
            socketTimeoutMS=settings.mongo_connect_timeout_ms,
            maxPoolSize=min(settings.mongo_max_pool_size, 5),
            waitQueueTimeoutMS=settings.mongo_wait_queue_timeout_ms,
            maxIdleTimeMS=60_000,
            retryReads=True,
            retryWrites=True,
            appname="base64-ops-vector",
        )
        collection = client[settings.mongo_db_name]["rag_chunks"]
        embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
        return (
            MongoDBAtlasVectorSearch(
                collection=collection,
                embedding=embeddings,
                index_name=settings.vector_index_name,
                text_key="text",
                relevance_score_fn="cosine",
            ),
            client,
        )
