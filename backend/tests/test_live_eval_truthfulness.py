from evals.models import LiveEvalReport
from evals.runner import run_live_github_test


def test_allowlisted_delivery_scaffold_is_not_reported_as_live_success() -> None:
    result = run_live_github_test("https://github.com/acme/disposable", ["https://github.com/acme/disposable"])

    assert result.status == "not_exercised"
    assert not result.diff_verified
    assert not result.cleaned_up
    assert "No live GitHub delivery call" in result.details["reason"]


def test_non_allowlisted_delivery_target_is_aborted() -> None:
    result = run_live_github_test("https://github.com/acme/customer", ["https://github.com/acme/disposable"])

    assert result.status == "aborted"
    assert not result.diff_verified


def test_live_report_defaults_to_no_external_checks() -> None:
    report = LiveEvalReport(deterministic_passed=3)

    assert report.deterministic_passed == 3
    assert report.live_passed == 0
    assert report.external_checks_exercised == 0
