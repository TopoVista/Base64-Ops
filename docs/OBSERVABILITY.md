# Observability and replay

Every chat run creates a Mongo-backed `RunTrace` and an `agent.run` span. Trace summaries contain timing, safe counts, status, and error type—not chain-of-thought, raw prompts, source files, tokens, headers, cookies, secrets, or model scratchpads.

All trace summaries pass through the shared redactor. The `GET /api/session/{slugId}/runs/{runId}` endpoint and the developer-inspector alias `GET /api/dev/runs/{runId}` reconstruct the same historical replay, always scoped to the authenticated user and its owning session. They return the persisted investigation snapshot, timeline, repository provenance, trace/spans when recorded, bounded evidence summaries, approval, delivery plan/diff, delivery result, and CI context. Replay never executes an action.

Before either endpoint sends a response, replay data is recursively passed through the centralized redaction service and then validated against the allowlisted `RunReplayResponse` contract. This protects legacy snapshots written before newer persistence safeguards existed: secrets in historical diagnoses, timeline details, CI context, trace summaries, diffs, or validation summaries are redacted before they can reach the browser. The contract also excludes internal ownership fields, execution arguments, source bodies, trace metadata, and unknown future persisted fields. Replay is structurally limited to `historical: true` and `replay_mode: "read_only"`; any other mode fails response validation. Model-use telemetry has its own allowlist for model identity, bounded token/context counters, cost, and provider-error state, rather than forwarding arbitrary provider metadata. A legacy oversized unified diff is bounded at the replay boundary and explicitly marked `diff_truncated`; the UI calls it a preview and never claims it is the complete exact delivery diff.

Some valid runs (for example, an explicit CI refresh or a code-proposal-only run) do not create a graph trace. Those runs remain replayable from their persisted investigation snapshot. The response marks the trace as unavailable and supplies a limitation rather than returning a misleading not-found error. The inspector is intentionally read-only: it does not re-fetch GitHub, re-run validation, or apply a patch. Full candidate source bytes are excluded from the replay response; only the reviewable exact diff is exposed.

`GET /api/session/{slugId}/runs` supplies a bounded, tenant-scoped run-history feed for the workspace History page. It merges persisted investigation snapshots with trace metadata, refreshes delivery status from the canonical delivery-result record, and returns only summary metadata needed to select a replay. It does not load evidence excerpts or candidate source bytes.

`TraceRecorder` has local and Mongo exporters and an exporter protocol for a future OpenTelemetry adapter. Retrieval telemetry records selected evidence IDs, timing, and counts; its query field is centrally redacted and capped at 1,000 characters before persistence. Token/cost and detailed retrieval-rank telemetry remain limited to real provider usage and rank signals rather than invented estimates.
# GitHub Actions spans

CI investigations add `github.actions.resolve_run`, `ci.investigation`, `ci.build_evidence`, `ci.workflow.failed_sha`, `ci.workflow.current_head`, `ci.changed_paths`, `ci.repository_correlation`, and `ci.applicability` spans to the existing run trace. Their safe summaries contain only IDs, SHA prefixes, counts, status, and applicability. CI log bodies, OAuth values, and workflow command text are never copied into span payloads. Replay reads persisted evidence and spans; it does not call GitHub again.

Change-impact requests add an `impact.analysis` span only when the exact
delivery plan's originating run has a persisted trace. Its input/output are
limited to the delivery-plan ID, changed-file count, graph availability,
status, bounded result counts, and truncation state. Paths, source contents,
workflow commands, tenant IDs, and candidate bytes are not copied into trace
summaries. The persisted impact report is itself read-only and scoped to the
owning tenant/session. Historical replay resolves that report only through the
same tenant, session, and delivery-plan binding, then projects a small
allowlist that excludes graph node IDs, relationship endpoint IDs, ownership
fields, and raw source content.
