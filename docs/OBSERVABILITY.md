# Observability and replay

Every chat run creates a Mongo-backed `RunTrace` and an `agent.run` span. Trace summaries contain timing, safe counts, status, and error type—not chain-of-thought, raw prompts, source files, tokens, headers, cookies, secrets, or model scratchpads.

All trace summaries pass through the shared redactor. The `GET /api/session/{slugId}/runs/{runId}` endpoint returns a historical replay scoped to the authenticated user: trace, spans, evidence, approval, and delivery result. Replay never executes an action.

`TraceRecorder` has local and Mongo exporters and an exporter protocol for a future OpenTelemetry adapter. Retrieval telemetry records selected evidence IDs, timing, and counts; its query field is centrally redacted and capped at 1,000 characters before persistence. Token/cost and detailed retrieval-rank telemetry remain limited to real provider usage and rank signals rather than invented estimates.
# GitHub Actions spans

CI investigations add `github.actions.resolve_run`, `ci.investigation`, `ci.build_evidence`, `ci.workflow.failed_sha`, `ci.workflow.current_head`, `ci.changed_paths`, `ci.repository_correlation`, and `ci.applicability` spans to the existing run trace. Their safe summaries contain only IDs, SHA prefixes, counts, status, and applicability. CI log bodies, OAuth values, and workflow command text are never copied into span payloads. Replay reads persisted evidence and spans; it does not call GitHub again.
