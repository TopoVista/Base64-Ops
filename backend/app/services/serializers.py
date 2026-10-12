from datetime import date, datetime
from typing import Any


def serialize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None

    def _json_safe(value: Any) -> Any:
        """Normalize persisted Mongo values without losing the replay record shape."""

        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [_json_safe(item) for item in value]
        return value

    serialized = _json_safe(dict(doc))
    if "_id" in serialized:
        serialized["_id"] = str(serialized["_id"])
        serialized.setdefault("id", serialized["_id"])
    return serialized


def public_user(user: dict[str, Any], github_connected: bool = False) -> dict[str, Any]:
    serialized = serialize_doc(user) or {}
    return {
        "_id": serialized["_id"],
        "id": serialized["_id"],
        "name": serialized.get("name", ""),
        "email": serialized.get("email", ""),
        "avatar": serialized.get("avatar"),
        "githubConnected": github_connected,
        "createdAt": serialized.get("createdAt"),
        "updatedAt": serialized.get("updatedAt"),
    }
