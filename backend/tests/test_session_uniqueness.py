from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

from app.schemas.session import SessionChatRequest
from app.services import session_service


async def test_concurrent_session_insert_returns_existing_tenant_session(monkeypatch) -> None:
    existing = {"_id": "ses_existing", "userId": "user-a", "slugId": "slug-a", "repoUrl": "https://github.com/acme/a.git"}

    class Sessions:
        def __init__(self) -> None:
            self.calls = 0

        async def find_one(self, _query):  # noqa: ANN001, ANN201
            self.calls += 1
            return None if self.calls == 1 else existing

        async def insert_one(self, _session):  # noqa: ANN001, ANN201
            raise DuplicateKeyError("sessions_one_slug_per_tenant")

    sessions = Sessions()
    monkeypatch.setattr(session_service, "get_db", lambda: SimpleNamespace(sessions=sessions))

    async def authorized(_user_id: str, _url: str) -> dict[str, str]:
        return {"cloneUrl": "https://github.com/acme/a.git", "defaultBranch": "main"}

    monkeypatch.setattr(session_service, "resolve_authorized_repository", authorized)
    payload = SessionChatRequest(slugId="slug-a", repoUrl="https://github.com/acme/a")

    result = await session_service.get_or_create_session("user-a", payload, "Investigate the repository")

    assert result is existing


async def test_session_index_setup_contains_tenant_unique_constraint(monkeypatch) -> None:
    class Collection:
        def __init__(self) -> None:
            self.indexes: list[tuple] = []

        async def create_index(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            self.indexes.append((args, kwargs))

    sessions = Collection()
    database = SimpleNamespace(sessions=sessions, messages=Collection(), investigations=Collection())
    monkeypatch.setattr(session_service, "get_db", lambda: database)

    await session_service.ensure_session_indexes()

    tenant_index = (
        ([("userId", 1), ("slugId", 1)],),
        {"unique": True, "name": "sessions_one_slug_per_tenant"},
    )
    assert tenant_index in sessions.indexes
