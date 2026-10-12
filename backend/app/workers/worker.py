import argparse
import asyncio
import os
import signal
from datetime import timedelta

from app.core.config import get_settings
from app.db.mongo import close_mongo, connect_mongo, get_db
from app.git.actions import GitHubActionsAdapter
from app.intelligence.graph.service import EngineeringGraphService
from app.intelligence.graph.store import EngineeringGraphStore
from app.services.background_job_service import BackgroundJobService
from app.services.delivery_ci_service import evaluate_delivery_ci
from app.services.rag_service import RagService
from app.services.redaction_service import RedactionService
from app.services.workspace_service import WorkspaceService
from app.utils.datetime import utc_now

redactor = RedactionService()


class DeferredJob(Exception):
    """A safe polling result; it is not a worker error or a CI verdict."""

    def __init__(self, delay_seconds: int) -> None:
        self.delay_seconds = delay_seconds


async def renew_job_lease(jobs: BackgroundJobService, job_id: str, lease_token: str, stopped: asyncio.Event) -> None:
    """Keep a durable claim alive without allowing the job to execute more work."""
    lease_seconds = get_settings().background_job_lease_seconds
    interval = max(5, min(60, lease_seconds // 3))
    while True:
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval)
            return
        except TimeoutError:
            if not await jobs.renew_lease(job_id, lease_token):
                return


async def run_job(job: dict) -> None:
    """Run only allowlisted internal maintenance tasks, never arbitrary commands."""
    if job.get("type") == "purge_expired_guide_history":
        await get_db().product_assistant_messages.delete_many({"createdAt": {"$lt": job["payload"]["before"]}})
        return
    if job.get("type") == "reindex_repository":
        session_id = str((job.get("payload") or {}).get("sessionId", ""))
        session = await get_db().sessions.find_one({"_id": session_id, "userId": job.get("userId")})
        if not session:
            # The session may have been deleted while this job waited. It is
            # not an error and must not make a worker inspect another tenant.
            return
        workspace = WorkspaceService()
        repo_path, _ = await workspace.ensure_workspace(
            str(job["userId"]), session["slugId"], session["repoUrl"], session.get("defaultBranch") or "main"
        )
        result = await RagService().index_workspace(session, repo_path)
        index_status = "ready" if result.get("indexed") else "empty"
        index_error = None if result.get("indexed") else "Repository contains no supported indexable files."
        graph_status = "not_built"
        graph_error = None
        try:
            snapshot = await EngineeringGraphService(workspace=workspace).build_and_store(
                session=session, repo_path=repo_path
            )
            graph_status = "ready"
        except Exception as exc:
            # Indexing and the graph are complementary evidence products. A
            # parser/storage failure must not discard an otherwise usable RAG
            # index or expose an implementation detail to the browser.
            snapshot = None
            graph_status = "failed"
            graph_error = redactor.redact(f"{type(exc).__name__}: {exc}")[:300]
        await get_db().sessions.update_one(
            {"_id": session_id, "userId": job["userId"]},
            {
                "$set": {
                    "indexStatus": index_status,
                    "indexError": index_error,
                    # A bounded corpus remains retrievable, but callers must
                    # not mistake it for a complete repository index.
                    "indexTruncated": bool(result.get("truncated")),
                    "indexedAt": utc_now(),
                    "indexJobId": job["_id"],
                    "graphStatus": graph_status,
                    "graphError": graph_error,
                    "graphSnapshotId": snapshot.id if snapshot else None,
                }
            },
        )
        return
    if job.get("type") == "verify_delivery_ci":
        result_id = str((job.get("payload") or {}).get("deliveryResultId", ""))
        delivery = await get_db().delivery_results.find_one({"_id": result_id, "user_id": job.get("userId")})
        if not delivery:
            return
        runs = await GitHubActionsAdapter().list_workflow_runs(
            user_id=str(job["userId"]),
            repository_id=delivery["repository_id"],
            branch=delivery.get("branch_name"),
        )
        verdict = evaluate_delivery_ci(commit_sha=delivery.get("commit_sha"), runs=runs)
        attempts = int(job.get("attempts", 1))
        if verdict.status in {"pending", "running"} and attempts >= get_settings().ci_verification_max_polls:
            verdict = verdict.__class__(
                "unknown",
                verdict.run_ids,
                "CI did not reach a terminal state before the verification window expired.",
            )
        await get_db().delivery_results.update_one(
            {"_id": result_id, "user_id": job["userId"]},
            {
                "$set": {
                    "ciVerification": {
                        "status": verdict.status,
                        "runIds": verdict.run_ids,
                        "message": verdict.message,
                        "checkedAt": utc_now(),
                    }
                }
            },
        )
        if verdict.status in {"pending", "running"}:
            raise DeferredJob(get_settings().ci_verification_poll_seconds)
        return
    raise ValueError(f"Unsupported background job type: {job.get('type')}")


async def run(role: str, once: bool, stopped: asyncio.Event | None = None) -> None:
    """Consume server-created jobs until asked to drain.

    A shutdown signal stops *new* claims. A job already under a fencing lease
    is allowed to finish its normal state transition when possible, so an
    autoscale/deploy event does not create duplicate delivery or index work.
    If a platform eventually force-kills the process, the short renewable
    lease makes the job reclaimable by another worker.
    """

    stopped = stopped or asyncio.Event()
    await connect_mongo()
    try:
        jobs = BackgroundJobService()
        await jobs.ensure_indexes()
        # Workers can be deployed before an API replica during a Render
        # rollout, so establish the same immutable graph-snapshot indexes at
        # their own durable write boundary.
        await EngineeringGraphStore().ensure_indexes()
        while not stopped.is_set():
            job = await jobs.claim(role)
            if not job:
                if once:
                    return
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=2)
                except TimeoutError:
                    pass
                continue
            lease_token = str(job.get("leaseToken", ""))
            if not lease_token:
                # Every current claim creates a fencing token. Refuse to run
                # an unexpected legacy document without one rather than let
                # it race another worker's lifecycle update.
                await get_db().background_jobs.update_one(
                    {"_id": job["_id"], "status": "running"},
                    {
                        "$set": {"status": "failed", "error": "Missing job lease token", "completedAt": utc_now()},
                        "$unset": {"leaseExpiresAt": "", "activeDedupeKey": ""},
                    },
                )
                continue
            lease_stopped = asyncio.Event()
            lease_heartbeat = asyncio.create_task(renew_job_lease(jobs, job["_id"], lease_token, lease_stopped))
            try:
                await run_job(job)
                await get_db().background_jobs.update_one(
                    {"_id": job["_id"], "status": "running", "leaseToken": lease_token},
                    {
                        "$set": {"status": "completed", "completedAt": utc_now(), "updatedAt": utc_now()},
                        "$unset": {"leaseExpiresAt": "", "leaseToken": "", "activeDedupeKey": ""},
                    },
                )
            except DeferredJob as exc:
                await get_db().background_jobs.update_one(
                    {"_id": job["_id"], "status": "running", "leaseToken": lease_token},
                    {
                        "$set": {
                            "status": "queued",
                            "availableAt": utc_now() + timedelta(seconds=exc.delay_seconds),
                            "updatedAt": utc_now(),
                        },
                        "$unset": {"leaseExpiresAt": "", "leaseToken": ""},
                    },
                )
            except Exception as exc:
                attempts = int(job.get("attempts", 1))
                retry = attempts < get_settings().background_job_max_attempts
                safe_error = redactor.redact(f"{type(exc).__name__}: {exc}")[:500]
                await get_db().background_jobs.update_one(
                    {"_id": job["_id"], "status": "running", "leaseToken": lease_token},
                    {
                        "$set": (
                            {
                                "status": "queued",
                                "availableAt": utc_now() + timedelta(seconds=30 * attempts),
                                "lastError": safe_error,
                                "updatedAt": utc_now(),
                            }
                            if retry
                            else {"status": "failed", "error": safe_error, "completedAt": utc_now()}
                        ),
                        "$unset": (
                            {"leaseExpiresAt": "", "leaseToken": ""}
                            if retry
                            else {"leaseExpiresAt": "", "leaseToken": "", "activeDedupeKey": ""}
                        ),
                    },
                )
            finally:
                lease_stopped.set()
                await lease_heartbeat
    finally:
        await close_mongo()


def main() -> None:
    parser = argparse.ArgumentParser(description="Base64 Ops internal worker")
    parser.add_argument("--once", action="store_true", help="Process available work once and exit")
    args = parser.parse_args()

    async def run_with_signal_drain() -> None:
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        # Render sends SIGTERM during deploys/scaling. Windows does not expose
        # all asyncio signal handlers, so retain normal local interruption.
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stopped.set)
            except (NotImplementedError, RuntimeError):
                pass
        await run(os.getenv("SERVICE_ROLE", "maintenance"), args.once, stopped)

    asyncio.run(run_with_signal_drain())


if __name__ == "__main__":
    main()
