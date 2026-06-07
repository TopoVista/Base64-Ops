from typing import Any


def serialize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    serialized = dict(doc)
    if "_id" in serialized:
        serialized["_id"] = str(serialized["_id"])
        serialized.setdefault("id", serialized["_id"])
    for key in ("createdAt", "updatedAt", "repoInitializedAt", "tokenExpiresAt"):
        if key in serialized and serialized[key] is not None:
            serialized[key] = serialized[key].isoformat()
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
