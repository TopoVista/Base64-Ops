from app.agent.state import AgentAction

MUTATING_KEYWORDS = {
    "write",
    "edit",
    "fix",
    "change",
    "commit",
    "push",
    "pull request",
    "pr",
    "deploy",
    "rollback",
    "rerun",
    "dispatch",
    "cancel workflow",
}


def is_risky_prompt(prompt: str) -> bool:
    normalized = prompt.lower()
    return any(keyword in normalized for keyword in MUTATING_KEYWORDS)


def choose_action(prompt: str) -> AgentAction:
    normalized = prompt.lower()
    if "docker" in normalized and ("check" in normalized or "build" in normalized):
        return {
            "name": "docker_build_check",
            "args": {},
            "risk": "safe",
            "summary": "Run a Docker build check for the selected repository.",
        }
    if "compose" in normalized:
        return {
            "name": "compose_config_check",
            "args": {},
            "risk": "safe",
            "summary": "Validate Docker Compose configuration.",
        }
    if "status" in normalized or "changed" in normalized:
        return {
            "name": "git_status",
            "args": {},
            "risk": "safe",
            "summary": "Inspect current git workspace status.",
        }
    if is_risky_prompt(prompt):
        return {
            "name": "propose_change",
            "args": {"prompt": prompt},
            "risk": "approval_required",
            "summary": "Prepare a repository-changing action that must be approved before execution.",
        }
    return {
        "name": "answer_with_rag",
        "args": {"prompt": prompt},
        "risk": "safe",
        "summary": "Answer with retrieved repository and runbook context.",
    }


def action_requires_approval(action: AgentAction | None) -> bool:
    return bool(action and action.get("risk") == "approval_required")
