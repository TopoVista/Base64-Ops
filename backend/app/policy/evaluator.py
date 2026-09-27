from app.policy.registry import get_policy


def requires_approval(tool_name: str) -> bool:
    return get_policy(tool_name).requires_approval
