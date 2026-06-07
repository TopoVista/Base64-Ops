from pathlib import Path
from typing import Any

from app.services.workspace_service import WorkspaceService


class DevOpsToolbox:
    def __init__(self) -> None:
        self.workspace = WorkspaceService()

    def run_safe_action(self, action_name: str, repo_path: str, args: dict[str, Any]) -> dict[str, Any]:
        path = Path(repo_path)
        if action_name == "git_status":
            return self.workspace.git_status(path)
        if action_name == "docker_build_check":
            return self.workspace.docker_build_check(path)
        if action_name == "compose_config_check":
            return self.workspace.compose_config_check(path)
        if action_name == "answer_with_rag":
            return {"success": True, "output": "RAG context gathered.", "exitCode": 0}
        return {"success": False, "output": f"Unknown safe action: {action_name}", "exitCode": 1}

    def run_approved_action(
        self,
        action_name: str,
        repo_path: str,
        branch_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        path = Path(repo_path)
        if action_name == "propose_change":
            plan = args.get("prompt", "Requested repository change")
            return {
                "success": True,
                "output": (
                    "Approval recorded. The agent prepared the change plan, but direct file mutation "
                    f"is intentionally left to a specific approved write tool. Request: {plan}"
                ),
                "exitCode": 0,
            }
        if action_name == "commit":
            return self.workspace.commit_all(path, args.get("message", "agent: approved changes"))
        if action_name == "push":
            return self.workspace.push_branch(path, branch_name)
        return {"success": False, "output": f"Unknown approved action: {action_name}", "exitCode": 1}
