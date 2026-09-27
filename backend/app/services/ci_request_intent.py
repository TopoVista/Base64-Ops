"""Deterministic recognition of requests that need a failed Actions run."""

from __future__ import annotations

import re

from pydantic import BaseModel


class CIRequestIntent(BaseModel):
    is_ci_investigation: bool = False
    explicit_run_id: int | None = None
    pr_number: int | None = None
    branch: str | None = None
    workflow_name: str | None = None
    latest_failed: bool = False


_FAILURE_WORDS = re.compile(r"\b(fail(?:ed|ing|ure)?|red|checks?)\b", re.I)
_CI_WORDS = re.compile(r"\b(ci|github actions|workflow|pr checks?|build)\b", re.I)
_RUN = re.compile(r"\b(?:workflow\s+)?run\s*#?(\d+)\b", re.I)
_PR = re.compile(r"\bpr\s*#?(\d+)\b", re.I)


def extract_ci_request_intent(prompt: str) -> CIRequestIntent:
    """Classify failure investigation only; workflow explanation stays a repo query."""
    run = _RUN.search(prompt)
    pr = _PR.search(prompt)
    failure_context = bool(_FAILURE_WORDS.search(prompt))
    ci_context = bool(_CI_WORDS.search(prompt))
    return CIRequestIntent(
        is_ci_investigation=bool(run or (failure_context and ci_context)),
        explicit_run_id=int(run.group(1)) if run else None,
        pr_number=int(pr.group(1)) if pr else None,
        latest_failed=bool(re.search(r"\b(latest|most recent)\b", prompt, re.I) and failure_context),
    )
