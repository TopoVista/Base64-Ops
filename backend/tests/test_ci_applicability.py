from app.services.ci_applicability import CIFailureApplicability, workflow_path_applicability


def test_historical_workflow_fix_is_not_current_defect():
    assert workflow_path_applicability(
        failed_workflow="working-directory: ./server",
        current_workflow="working-directory: ./client",
        failed_run_sha="abc123",
        current_head_sha="def456",
    ) == CIFailureApplicability.HISTORICAL_FIXED


def test_same_workflow_is_still_current():
    assert workflow_path_applicability(
        failed_workflow="working-directory: ./server",
        current_workflow="working-directory: ./server",
        failed_run_sha="abc123",
        current_head_sha="def456",
    ) == CIFailureApplicability.CURRENT


def test_unrelated_workflow_edit_does_not_mark_path_failure_fixed():
    assert workflow_path_applicability(
        failed_workflow="name: CI\ndefaults:\n  run:\n    working-directory: ./server",
        current_workflow="name: Renamed CI\ndefaults:\n  run:\n    working-directory: ./server",
        failed_run_sha="abc123",
        current_head_sha="def456",
    ) == CIFailureApplicability.CURRENT


def test_missing_current_state_fails_safe():
    assert workflow_path_applicability(
        failed_workflow="working-directory: ./server",
        current_workflow=None,
        failed_run_sha="abc123",
        current_head_sha=None,
    ) == CIFailureApplicability.HISTORICAL_NEEDS_VERIFICATION
