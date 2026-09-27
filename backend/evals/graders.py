from app.schemas.investigation import Hypothesis


def evidence_recall(retrieved: list[str], expected: list[str]) -> float | None:
    if not expected:
        return None
    return len(set(retrieved) & set(expected)) / len(set(expected))


def citation_precision(cited: list[str], expected: list[str]) -> float | None:
    if not cited:
        return 1.0 if not expected else 0.0
    return len(set(cited) & set(expected)) / len(set(cited))


def unsupported_claim_count(hypotheses: list[Hypothesis]) -> int:
    return sum(1 for hypothesis in hypotheses if not hypothesis.evidence_ids)


def tool_selection(expected: list[str], actual: list[str], prohibited: list[str]) -> tuple[bool, list[str], list[str]]:
    prohibited_calls = sorted(set(actual) & set(prohibited))
    missing = sorted(set(expected) - set(actual))
    return not prohibited_calls and not missing, missing, prohibited_calls
