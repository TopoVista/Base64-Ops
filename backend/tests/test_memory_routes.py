from app.api.routes import memory
from app.core.config import Settings


def test_memory_routes_register_specific_staleness_before_greedy_repository_path() -> None:
    paths = [route.path for route in memory.router.routes]

    assert paths.index("/{repository_id:path}/staleness") < paths.index("/{repository_id:path}")


async def test_memory_list_uses_the_authenticated_internal_user_id(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def list_entries(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(memory, "get_settings", lambda: Settings(memory_enabled=True))
    monkeypatch.setattr(memory._memory, "list_entries", list_entries)

    result = await memory.list_memory_entries("acme/base64", current_user={"_id": "usr_internal"})

    assert result["count"] == 0
    assert captured["user_id"] == "usr_internal"
