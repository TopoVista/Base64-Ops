from app.services.context_budget import build_bounded_answer_context, build_bounded_patch_candidates


def test_context_budget_keeps_ranked_sources_and_evidence_ids_within_cap() -> None:
    result = build_bounded_answer_context(
        user_request="Why did CI fail?",
        sources=[
            {"evidenceId": "ev_1", "kind": "ci_log", "source": "ci.yml", "excerpt": "A" * 300},
            {"evidenceId": "ev_2", "kind": "repo", "source": "app.py", "excerpt": "B" * 300},
        ],
        tool_result={"output": "safe"},
        investigation={"evidence_ids": ["ev_1", "ev_2"]},
        max_context_chars=1_000,
        max_source_chars=200,
    )

    assert result.context_chars <= 1_000
    assert "[ev_1]" in result.prompt
    assert "UNTRUSTED EVIDENCE" in result.prompt
    assert result.kept_source_count >= 1
    assert result.estimated_input_tokens > 0


def test_context_budget_never_exceeds_source_cap_or_discards_provenance_silently() -> None:
    result = build_bounded_answer_context(
        user_request="diagnose",
        sources=[{"evidenceId": "ev_1", "source": "a.py", "excerpt": "x" * 10_000}],
        tool_result=None,
        investigation=None,
        max_context_chars=3_000,
        max_source_chars=200,
    )

    assert result.context_chars <= 3_000
    assert "[ev_1]" in result.prompt
    assert "excerpt truncated by context budget" in result.prompt
    assert result.truncated


def test_patch_candidate_budget_preserves_paths_and_hashes_within_limit() -> None:
    result = build_bounded_patch_candidates(
        candidates=[
            {"path": "backend/main.py", "hash": "a" * 64, "content": "a" * 2_000, "reasons": ["evidence"]},
            {"path": "client/src/app.ts", "hash": "b" * 64, "content": "b" * 2_000, "reasons": ["evidence"]},
        ],
        max_context_chars=1_200,
        max_file_chars=800,
    )

    assert result.context_chars <= 1_200
    assert result.candidates[0]["path"] == "backend/main.py"
    assert result.candidates[0]["hash"] == "a" * 64
    assert result.truncated
