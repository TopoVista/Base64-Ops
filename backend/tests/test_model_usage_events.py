from types import SimpleNamespace

from app.agent.graph import _provider_usage_event


def test_provider_usage_event_keeps_only_provider_reported_counts() -> None:
    raw = SimpleNamespace(
        usage_metadata={"input_tokens": 320, "output_tokens": 41, "total_tokens": 361},
        response_metadata={},
    )

    assert _provider_usage_event(raw, model="gpt-test") == {
        "model": "gpt-test",
        "prompt_tokens": 320,
        "completion_tokens": 41,
        "total_tokens": 361,
    }


def test_provider_usage_event_does_not_invent_missing_usage() -> None:
    raw = SimpleNamespace(usage_metadata=None, response_metadata={})

    assert _provider_usage_event(raw, model="gpt-test") is None
