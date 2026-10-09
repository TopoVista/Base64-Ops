"""Tenant-scoped, durable jobs for bounded internal background work."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.db.mongo import get_db
from app.utils.datetime import utc_now
from app.utils.ids import new_id


class BackgroundJobService:
    """A deliberately narrow Mongo-backed queue.

    Payloads contain server IDs only; workers load tenant/repository data from
    persistence. This prevents a browser or repository text from smuggling a
    command, URL, or filesystem path into a worker.
    """

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.background_jobs.create_index([("role", 1), ("status", 1), ("availableAt", 1)])
        await db.background_jobs.create_index([("role", 1), ("status", 1), ("leaseExpiresAt", 1)])
        await db.background_jobs.create_index([("dedupeKey", 1), ("status", 1)])
        # An ordinary lookup followed by insert races across API replicas.
        # This sparse active key makes that invariant database-enforced while
        # allowing a new job after a prior terminal result.
        await db.background_jobs.create_index(
            "activeDedupeKey",
            unique=True,
            sparse=True,
            name="background_jobs_one_active_dedupe_key",
        )
        await db.background_jobs.create_index("completedAt", expireAfterSeconds=60 * 60 * 24 * 30)

    async def enqueue_reindex(self, *, user_id: str, session_id: str) -> dict[str, Any]:
        db = get_db()
        dedupe_key = f"reindex:{user_id}:{session_id}"
        existing = await db.background_jobs.find_one(
            {"dedupeKey": dedupe_key, "status": {"$in": ["queued", "running"]}},
            projection={"_id": 1, "status": 1},
        )
        if existing:
            return {"id": existing["_id"], "status": existing["status"], "deduplicated": True}
        now = utc_now()
        job = {
            "_id": new_id("job_"),
            "role": "indexer",
            "type": "reindex_repository",
            "status": "queued",
            "dedupeKey": dedupe_key,
            "userId": user_id,
            "payload": {"sessionId": session_id},
            "attempts": 0,
            "availableAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        job["activeDedupeKey"] = dedupe_key
        try:
            await db.background_jobs.insert_one(job)
        except DuplicateKeyError:
            existing = await db.background_jobs.find_one(
                {"activeDedupeKey": dedupe_key}, projection={"_id": 1, "status": 1}
            )
            if existing:
                return {"id": existing["_id"], "status": existing["status"], "deduplicated": True}
            raise
        return {"id": job["_id"], "status": job["status"], "deduplicated": False}

    async def enqueue_delivery_ci_verification(self, *, user_id: str, delivery_result_id: str) -> dict[str, Any]:
        db = get_db()
        dedupe_key = f"delivery-ci:{user_id}:{delivery_result_id}"
        existing = await db.background_jobs.find_one(
            {"dedupeKey": dedupe_key, "status": {"$in": ["queued", "running"]}}, projection={"_id": 1, "status": 1}
        )
        if existing:
            return {"id": existing["_id"], "status": existing["status"], "deduplicated": True}
        now = utc_now()
        job = {
            "_id": new_id("job_"),
            "role": "ci_observer",
            "type": "verify_delivery_ci",
            "status": "queued",
            "dedupeKey": dedupe_key,
            "userId": user_id,
            "payload": {"deliveryResultId": delivery_result_id},
            "attempts": 0,
            "availableAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
        job["activeDedupeKey"] = dedupe_key
        try:
            await db.background_jobs.insert_one(job)
        except DuplicateKeyError:
            existing = await db.background_jobs.find_one(
                {"activeDedupeKey": dedupe_key}, projection={"_id": 1, "status": 1}
            )
            if existing:
                return {"id": existing["_id"], "status": existing["status"], "deduplicated": True}
            raise
        return {"id": job["_id"], "status": job["status"], "deduplicated": False}

    async def claim(self, role: str) -> dict[str, Any] | None:
        """Atomically claim one allowlisted job and give it a renewable lease.

        A crashed worker cannot hold a job indefinitely. The worker renews the
        lease while it is alive; an expired lease is eligible for reclamation.
        Legacy running documents without a lease are treated as stale only
        after the same conservative interval.
        """
        now = utc_now()
        lease_seconds = get_settings().background_job_lease_seconds
        stale_before = now - timedelta(seconds=lease_seconds)
        lease_token = new_id("lease_")
        return await get_db().background_jobs.find_one_and_update(
            {
                "role": role,
                "$or": [
                    {"status": "queued", "availableAt": {"$lte": now}},
                    {"status": "running", "leaseExpiresAt": {"$lte": now}},
                    {
                        "status": "running",
                        "leaseExpiresAt": {"$exists": False},
                        "startedAt": {"$lte": stale_before},
                    },
                ],
            },
            {
                "$set": {
                    "status": "running",
                    "startedAt": now,
                    "claimedAt": now,
                    "leaseExpiresAt": now + timedelta(seconds=lease_seconds),
                    "leaseToken": lease_token,
                    "updatedAt": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("availableAt", 1), ("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def renew_lease(self, job_id: str, lease_token: str) -> bool:
        """Extend a claimed job only while its owner still marks it running."""
        now = utc_now()
        result = await get_db().background_jobs.update_one(
            {"_id": job_id, "status": "running", "leaseToken": lease_token},
            {
                "$set": {
                    "leaseExpiresAt": now + timedelta(seconds=get_settings().background_job_lease_seconds),
                    "updatedAt": now,
                }
            },
        )
        return result.modified_count == 1
