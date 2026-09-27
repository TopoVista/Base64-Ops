import asyncio

from app.core.failures import FailureType, is_retryable
from app.observability.recorder import LocalTraceExporter, TraceRecorder
from evals.graders import citation_precision, evidence_recall, tool_selection
from evals.models import EvalCase
from evals.runner import load_cases, run_case


def test_eval_case_parsing_and_deterministic_runner() -> None:
    case = EvalCase(id="x", name="X", category="safety", user_request="status", repository_fixture="git-status")
    assert case.expected_tools == []
    result = run_case(next(item for item in load_cases() if item.id == "prompt-injection"))
    assert result.passed
    assert result.prohibited_tool_calls == []


def test_evidence_and_tool_graders() -> None:
    assert evidence_recall(["a"], ["a", "b"]) == 0.5
    assert citation_precision(["a", "x"], ["a"]) == 0.5
    correct, missing, prohibited = tool_selection(["git.status"], ["git.status"], ["git.push"])
    assert correct and not missing and not prohibited


def test_trace_span_is_nested_safe_and_redacted() -> None:
    async def scenario() -> LocalTraceExporter:
        exporter = LocalTraceExporter()
        recorder = TraceRecorder(exporter)
        trace = await recorder.start_run("user-a", "session-a", "run-a", "repo-a")
        async with recorder.span(trace.id, "retrieval.lexical", {"token": "Bearer secret-value"}) as span:
            span.safe_output_summary = {"evidence_count": 2}
        await recorder.complete_run(trace, "completed")
        return exporter

    exporter = asyncio.run(scenario())
    assert exporter.traces[-1]["status"] == "completed"
    assert exporter.spans[0]["safe_input_summary"]["token"] == "Bearer [REDACTED]"
    assert exporter.spans[0]["safe_output_summary"]["evidence_count"] == "2"


def test_failure_retry_taxonomy() -> None:
    assert is_retryable(FailureType.RECOVERABLE)
    assert not is_retryable(FailureType.PERMISSION, 403)
    assert not is_retryable(FailureType.EXTERNAL_SERVICE, 401)
