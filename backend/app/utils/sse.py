import json
from collections.abc import AsyncIterator
from typing import Any


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def heartbeat() -> AsyncIterator[str]:
    yield sse_event("heartbeat", {"ok": True})
