from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.services import background_job_service, session_service
from app.services.background_job_service import BackgroundJobService


class FakeJobsCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def find_one(self, query, projection=None):  # noqa: ANN001, ANN201
        for row in self.rows:
            if row.get("dedupeKey") == query.get("dedupeKey") and row.get("status") in query["status"]["$in"]:
                return row
        return None

    async def insert_one(self, row):  # noqa: ANN001, ANN201
        self.rows.append(row)
        return SimpleNamespace(inserted_id=row["_id"])


async def test_reindex_jobs_are_deduplicated_and_contain_no_repository_input(monkeypatch) -> None:
    fake_db = SimpleNamespace(background_jobs=FakeJobsCollection())
    monkeypatch.setattr("app.services.background_job_service.get_db", lambda: fake_db)
    jobs = BackgroundJobService()

    first = await jobs.enqueue_reindex(user_id="user-a", session_id="session-a")
    second = await jobs.enqueue_reindex(user_id="user-a", session_id="session-a")

    assert not first["deduplicated"]
    assert second["deduplicated"]
    assert fake_db.background_jobs.rows[0]["payload"] == {"sessionId": "session-a"}
    assert "repoUrl" not in fake_db.background_jobs.rows[0]["payload"]


class FakeSessionsCollection:
    def __init__(self) -> None:
        self.session = {
            "_id": "session-a",
            "userId": "user-a",
            "slugId": "slug-a",
            "repoUrl": "https://github.com/example/repository.git",
            "defaultBranch": "main",
        }
        self.updates: list[dict] = []

    async def find_one(self, query):  # noqa: ANN001, ANN201
        if query.get("userId") == self.session["userId"] and query.get("slugId") == self.session["slugId"]:
            return self.session
        return None

    async def update_one(self, query, update):  # noqa: ANN001, ANN201
        self.updates.append({"query": query, "update": update})
        self.session.update(update.get("$set", {}))


async def test_queued_reindex_returns_immediately_without_cloning_in_api_process(monkeypatch) -> None:
    sessions = FakeSessionsCollection()
    jobs = FakeJobsCollection()
    fake_db = SimpleNamespace(sessions=sessions, background_jobs=jobs)
    monkeypatch.setattr(session_service, "get_db", lambda: fake_db)
    monkeypatch.setattr(session_service, "get_settings", lambda: Settings(indexing_mode="queued"))
    monkeypatch.setattr("app.services.background_job_service.get_db", lambda: fake_db)

    result = await session_service.reindex_session("user-a", "slug-a")

    assert result["success"]
    assert result["status"] == "queued"
    assert jobs.rows[0]["role"] == "indexer"
    assert sessions.session["indexStatus"] == "queued"


class FakeClaimCollection:
    def __init__(self) -> None:
        self.claim_query: dict | None = None
        self.claim_update: dict | None = None
        self.renew_query: dict | None = None

    async def find_one_and_update(self, query, update, **_kwargs):  # noqa: ANN001, ANN201
        self.claim_query = query
        self.claim_update = update
        return {"_id": "job-a", "status": "running"}

    async def update_one(self, query, _update):  # noqa: ANN001, ANN201
        self.renew_query = query
        return SimpleNamespace(modified_count=1)


async def test_background_job_claim_uses_a_reclaimable_lease(monkeypatch) -> None:
    collection = FakeClaimCollection()
    monkeypatch.setattr(
        background_job_service,
        "get_db",
        lambda: SimpleNamespace(background_jobs=collection),
    )
    monkeypatch.setattr(
        background_job_service,
        "get_settings",
        lambda: Settings(background_job_lease_seconds=120),
    )

    job = await BackgroundJobService().claim("indexer")
    lease_token = collection.claim_update["$set"]["leaseToken"]
    renewed = await BackgroundJobService().renew_lease("job-a", lease_token)

    assert job and job["_id"] == "job-a"
    assert renewed
    claimed_at = collection.claim_update["$set"]["claimedAt"]
    expires_at = collection.claim_update["$set"]["leaseExpiresAt"]
    assert {"status": "running", "leaseExpiresAt": {"$lte": claimed_at}} in collection.claim_query["$or"]
    assert expires_at > claimed_at
    assert lease_token.startswith("lease_")
    assert collection.renew_query == {"_id": "job-a", "status": "running", "leaseToken": lease_token}


async def test_stale_worker_cannot_renew_a_reclaimed_job(monkeypatch) -> None:
    class Collection:
        async def update_one(self, query, _update):  # noqa: ANN001, ANN201
            return SimpleNamespace(modified_count=int(query["leaseToken"] == "current-lease"))

    monkeypatch.setattr(background_job_service, "get_db", lambda: SimpleNamespace(background_jobs=Collection()))

    jobs = BackgroundJobService()
    assert not await jobs.renew_lease("job-a", "stale-lease")
    assert await jobs.renew_lease("job-a", "current-lease")


async def test_enqueue_race_returns_the_existing_active_job(monkeypatch) -> None:
    class RaceCollection:
        async def find_one(self, query, projection=None):  # noqa: ANN001, ANN201
            if "activeDedupeKey" in query:
                return {"_id": "job-existing", "status": "running"}
            return None

        async def insert_one(self, _row):  # noqa: ANN001, ANN201
            raise DuplicateKeyError("background_jobs_one_active_dedupe_key")

    monkeypatch.setattr(
        background_job_service,
        "get_db",
        lambda: SimpleNamespace(background_jobs=RaceCollection()),
    )

    result = await BackgroundJobService().enqueue_reindex(user_id="user-a", session_id="session-a")

    assert result == {"id": "job-existing", "status": "running", "deduplicated": True}
