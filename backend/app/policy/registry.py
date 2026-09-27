from app.policy.models import ToolPolicy, ToolRisk

POLICIES: dict[str, ToolPolicy] = {
    "repo.read_file": ToolPolicy(tool_name="repo.read_file", risk_level=ToolRisk.READ, requires_approval=False),
    "repo.search": ToolPolicy(tool_name="repo.search", risk_level=ToolRisk.READ, requires_approval=False),
    "repo.list_tree": ToolPolicy(tool_name="repo.list_tree", risk_level=ToolRisk.READ, requires_approval=False),
    "git.status": ToolPolicy(tool_name="git.status", risk_level=ToolRisk.READ, requires_approval=False),
    "git.diff": ToolPolicy(tool_name="git.diff", risk_level=ToolRisk.READ, requires_approval=False),
    "docker.compose_config": ToolPolicy(
        tool_name="docker.compose_config", risk_level=ToolRisk.READ, requires_approval=False
    ),
    "patch.generate": ToolPolicy(tool_name="patch.generate", risk_level=ToolRisk.PROPOSAL, requires_approval=False),
    "patch.apply": ToolPolicy(
        tool_name="patch.apply", risk_level=ToolRisk.WRITE, requires_approval=True, requires_validation=True
    ),
    "git.create_branch": ToolPolicy(tool_name="git.create_branch", risk_level=ToolRisk.WRITE, requires_approval=True),
    "git.commit": ToolPolicy(tool_name="git.commit", risk_level=ToolRisk.WRITE, requires_approval=True),
    "git.push": ToolPolicy(tool_name="git.push", risk_level=ToolRisk.WRITE, requires_approval=True),
    "github.create_pull_request": ToolPolicy(
        tool_name="github.create_pull_request", risk_level=ToolRisk.WRITE, requires_approval=True
    ),
    "deploy": ToolPolicy(tool_name="deploy", risk_level=ToolRisk.PRIVILEGED, requires_approval=True),
}


def get_policy(tool_name: str) -> ToolPolicy:
    try:
        return POLICIES[tool_name]
    except KeyError as exc:
        raise ValueError(f"Tool is not registered: {tool_name}") from exc
