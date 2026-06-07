from app.agent.policy import action_requires_approval, choose_action, is_risky_prompt


def test_read_only_status_is_safe():
    action = choose_action("check git status and tell me what changed")
    assert action["name"] == "git_status"
    assert action["risk"] == "safe"
    assert not action_requires_approval(action)


def test_mutating_prompt_requires_approval():
    action = choose_action("fix the Dockerfile and push a pull request")
    assert is_risky_prompt("fix the Dockerfile and push a pull request")
    assert action["risk"] == "approval_required"
    assert action_requires_approval(action)


def test_docker_build_check_is_read_only_diagnostic():
    action = choose_action("run a docker build check")
    assert action["name"] == "docker_build_check"
    assert action["risk"] == "safe"
