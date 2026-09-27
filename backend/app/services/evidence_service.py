import hashlib
from typing import Any

from app.db.mongo import get_db
from app.schemas.investigation import EvidenceItem
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now
from app.utils.ids import new_id


class EvidenceService:
    def __init__(self) -> None:
        self.redactor = RedactionService()

    async def record_sources(
        self,
        *,
        user_id: str,
        session: dict[str, Any],
        run_id: str,
        sources: list[dict[str, Any]],
        commit_sha: str | None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source in sources:
            excerpt = self.redactor.redact(str(source.get("excerpt", "")))[:2000]
            path = str(source.get("source") or source.get("path") or "unknown")
            metadata = source.get("metadata", {})
            source_type = (
                "ci_log"
                if source.get("kind") == "ci_log"
                else "runbook"
                if source.get("kind") == "runbook"
                else "repo_file"
            )
            provenance_state = str(metadata.get("state", ""))
            digest = hashlib.sha256(
                f"{source_type}:{path}:{commit_sha or ''}:{provenance_state}:{excerpt}".encode()
            ).hexdigest()
            existing = await get_db().evidence.find_one(
                {"userId": user_id, "sessionId": session["_id"], "runId": run_id, "contentHash": digest}
            )
            if existing:
                records.append(existing)
                continue
            item = EvidenceItem(
                id=new_id("evd_"),
                user_id=user_id,
                session_id=session["_id"],
                run_id=run_id,
                source_type=source_type,
                repository_id=session.get("repoUrl"),
                branch=session.get("defaultBranch"),
                commit_sha=commit_sha,
                path=path,
                line_start=source.get("lineStart"),
                line_end=source.get("lineEnd"),
                title=path,
                excerpt=excerpt,
                source_timestamp=source.get("sourceTimestamp"),
                retrieved_at=utc_now(),
                retrieval_score=source.get("score"),
                content_hash=digest,
                metadata={"kind": source.get("kind", "repo"), **source.get("metadata", {})},
            ).model_dump(by_alias=False)
            document = {
                "_id": item["id"],
                "id": item["id"],
                "userId": item.pop("user_id"),
                "sessionId": item.pop("session_id"),
                "runId": item.pop("run_id"),
                "sourceType": item.pop("source_type"),
                "repositoryId": item.pop("repository_id"),
                "commitSha": item.pop("commit_sha"),
                "lineStart": item.pop("line_start"),
                "lineEnd": item.pop("line_end"),
                "sourceTimestamp": item.pop("source_timestamp"),
                "retrievedAt": item.pop("retrieved_at"),
                "retrievalScore": item.pop("retrieval_score"),
                "contentHash": item.pop("content_hash"),
                **item,
            }
            await get_db().evidence.insert_one(document)
            records.append(document)
        return records

    async def list_for_session(self, user_id: str, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return await (
            get_db()
            .evidence.find({"userId": user_id, "sessionId": session_id})
            .sort("retrievedAt", -1)
            .limit(limit)
            .to_list(limit)
        )
