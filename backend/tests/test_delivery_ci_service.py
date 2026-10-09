from app.git.actions import WorkflowRunSummary
from app.services.delivery_ci_service import evaluate_delivery_ci


def run(*, run_id: int, sha: str, status: str, conclusion: str | None) -> WorkflowRunSummary:
    return WorkflowRunSummary(
        id=run_id,
        repository_id="https://github.com/example/repo.git",
        head_sha=sha,
        workflow_name="CI",
        status=status,
        conclusion=conclusion,
    )


def test_delivery_ci_requires_exact_commit_sha_before_reporting_pass() -> None:
    verdict = evaluate_delivery_ci(
        commit_sha="delivered-sha",
        runs=[run(run_id=1, sha="other-sha", status="completed", conclusion="success")],
    )

    assert verdict.status == "pending"
    assert not verdict.run_ids


def test_delivery_ci_reports_running_and_failed_without_claiming_success() -> None:
    running = evaluate_delivery_ci(
        commit_sha="delivered-sha", runs=[run(run_id=1, sha="delivered-sha", status="in_progress", conclusion=None)]
    )
    failed = evaluate_delivery_ci(
        commit_sha="delivered-sha", runs=[run(run_id=1, sha="delivered-sha", status="completed", conclusion="failure")]
    )

    assert running.status == "running"
    assert failed.status == "failed"


def test_delivery_ci_reports_pass_only_for_completed_successful_matching_runs() -> None:
    verdict = evaluate_delivery_ci(
        commit_sha="delivered-sha",
        runs=[
            run(run_id=1, sha="delivered-sha", status="completed", conclusion="success"),
            run(run_id=2, sha="delivered-sha", status="completed", conclusion="neutral"),
        ],
    )

    assert verdict.status == "passed"
    assert verdict.run_ids == [1, 2]
