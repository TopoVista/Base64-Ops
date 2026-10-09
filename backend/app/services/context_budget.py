"""Deterministic LLM-context packing that preserves evidence provenance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextBudget:
    prompt: str
    source_count: int
    kept_source_count: int
    discarded_source_count: int
    context_chars: int
    estimated_input_tokens: int
    truncated: bool


@dataclass(frozen=True)
class PatchCandidateBudget:
    candidates: list[dict[str, Any]]
    context_chars: int
    discarded_candidate_count: int
    truncated: bool


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[: max(0, limit - 28)] + "\n[excerpt truncated by context budget]", True


def build_bounded_answer_context(
    *,
    user_request: str,
    sources: list[dict[str, Any]],
    tool_result: dict[str, Any] | None,
    investigation: dict[str, Any] | None,
    max_context_chars: int,
    max_source_chars: int,
) -> ContextBudget:
    """Build a bounded prompt from already selected evidence, in rank order.

    This deliberately never removes evidence records or citations from the
    investigation. It only controls what reaches the model. Sources are
    expected in retrieval-rank order; no opaque model scoring is introduced.
    """

    prefix = f"User request:\n{user_request}\n\nRetrieved context:\n"
    suffix = (
        "\n\nStructured investigation (factual scaffold, not hidden reasoning):\n"
        + json.dumps(investigation or {}, default=str, separators=(",", ":"))
        + "\n\nTool result:\n"
        + json.dumps(tool_result or {}, default=str, separators=(",", ":"))
        + "\n\nWrite a concise answer. State what was inspected, what was found, the safest next action, "
        "and cite only supplied evidence IDs in square brackets."
    )
    # Reserve stable space for the structured conclusion/tool result. If a
    # malformed tool result is enormous, truncate it before source allocation.
    suffix, suffix_truncated = _truncate(suffix, max(256, max_context_chars // 3))
    available = max(0, max_context_chars - len(prefix) - len(suffix))
    chunks: list[str] = []
    kept = 0
    truncated = suffix_truncated
    for index, source in enumerate(sources):
        evidence_id = source.get("evidenceId", index + 1)
        # Delimit every retrieved item at the model boundary. Repository
        # content, CI logs, runbooks, and tool output may contain adversarial
        # instruction-shaped text; provenance is evidence, never authority.
        header = (
            f"[{evidence_id}] UNTRUSTED EVIDENCE "
            f"{source.get('kind', 'repo')}:{source.get('source', 'unknown')}\n"
        )
        excerpt, item_truncated = _truncate(str(source.get("excerpt", "")), max_source_chars)
        chunk = header + excerpt + "\n\n"
        if len(chunk) > available:
            # Keep a final provenance-bearing partial excerpt when useful,
            # rather than silently dropping it or overflowing the budget.
            if available > len(header) + 64:
                partial, _ = _truncate(excerpt, available - len(header))
                chunks.append(header + partial + "\n\n")
                kept += 1
                truncated = True
            break
        chunks.append(chunk)
        available -= len(chunk)
        kept += 1
        truncated = truncated or item_truncated

    prompt = prefix + "".join(chunks) + suffix
    return ContextBudget(
        prompt=prompt,
        source_count=len(sources),
        kept_source_count=kept,
        discarded_source_count=max(0, len(sources) - kept),
        context_chars=len(prompt),
        # This is intentionally labelled estimated; provider usage returned
        # after inference remains the source of truth for billing telemetry.
        estimated_input_tokens=(len(prompt) + 3) // 4,
        truncated=truncated or kept < len(sources),
    )


def build_bounded_patch_candidates(
    *,
    candidates: list[dict[str, Any]],
    max_context_chars: int,
    max_file_chars: int,
) -> PatchCandidateBudget:
    """Preserve exact path/hash provenance while bounding patch-model input.

    Candidate files are already selected by deterministic evidence and path
    policy. This function does not re-rank them; it admits them in that order
    until the explicit character budget is exhausted.
    """
    kept: list[dict[str, Any]] = []
    remaining = max_context_chars
    truncated = False
    for candidate in candidates:
        content, content_truncated = _truncate(str(candidate.get("content", "")), max_file_chars)
        compact = {**candidate, "content": content}
        encoded = json.dumps(compact, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) > remaining:
            # A path/hash-only partial record still lets the model explicitly
            # decline an unsupported edit instead of inventing file context.
            if remaining > 256:
                header = {key: value for key, value in compact.items() if key != "content"}
                allowed_content = max(0, remaining - len(json.dumps(header, separators=(",", ":"))) - 64)
                partial, _ = _truncate(content, allowed_content)
                kept.append({**header, "content": partial})
            truncated = True
            break
        kept.append(compact)
        remaining -= len(encoded)
        truncated = truncated or content_truncated

    return PatchCandidateBudget(
        candidates=kept,
        context_chars=max_context_chars - remaining,
        discarded_candidate_count=max(0, len(candidates) - len(kept)),
        truncated=truncated or len(kept) < len(candidates),
    )
