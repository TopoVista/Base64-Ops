from app.services.ci_request_intent import extract_ci_request_intent


def test_ci_failure_request_is_detected_with_explicit_run():
    intent = extract_ci_request_intent("Why did workflow run 842 fail?")
    assert intent.is_ci_investigation
    assert intent.explicit_run_id == 842


def test_workflow_explanation_is_not_a_live_ci_request():
    assert not extract_ci_request_intent("Explain .github/workflows/ci.yml.").is_ci_investigation
