import argparse
import asyncio
import os

from pymongo import ReturnDocument

from app.db.mongo import close_mongo, connect_mongo, get_db
from app.utils.datetime import utc_now


async def claim_job(role: str) -> dict | None:
    return await get_db().background_jobs.find_one_and_update(
        {"role": role, "status": "queued"},
        {"$set": {"status": "running", "startedAt": utc_now()}},
        sort=[("createdAt", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def run_job(job: dict) -> None:
    """Run only allowlisted internal maintenance tasks, never arbitrary commands."""
    if job.get("type") == "purge_expired_guide_history":
        await get_db().product_assistant_messages.delete_many({"createdAt": {"$lt": job["payload"]["before"]}})
        return
    raise ValueError(f"Unsupported background job type: {job.get('type')}")


async def run(role: str, once: bool) -> None:
    await connect_mongo()
    try:
        while True:
            job = await claim_job(role)
            if not job:
                if once:
                    return
                await asyncio.sleep(2)
                continue
            try:
                await run_job(job)
                await get_db().background_jobs.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "completed", "completedAt": utc_now()}},
                )
            except Exception as exc:
                await get_db().background_jobs.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "failed", "error": str(exc), "completedAt": utc_now()}},
                )
    finally:
        await close_mongo()


def main() -> None:
    parser = argparse.ArgumentParser(description="Base64 Ops internal worker")
    parser.add_argument("--once", action="store_true", help="Process available work once and exit")
    args = parser.parse_args()
    asyncio.run(run(os.getenv("SERVICE_ROLE", "maintenance"), args.once))


if __name__ == "__main__":
    main()
