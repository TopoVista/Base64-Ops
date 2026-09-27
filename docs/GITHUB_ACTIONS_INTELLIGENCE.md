# GitHub Actions intelligence

Base64 Ops uses GitHub Actions as a read-only operational signal source. The existing OAuth-backed GitHub service remains the only HTTP boundary; `GitHubActionsAdapter` normalizes workflow runs, jobs, and bounded job-log downloads into typed models.

## Investigation flow

For a CI-failure request, the graph classifies the request, resolves an explicit/latest/PR-head-matched failed run within the session repository, gathers failed jobs and bounded failure excerpts, then attaches the results to the normal evidence-first investigation. It separately records:

- the failed run SHA;
- the workflow file at that SHA when Git history is available;
- the current workflow revision when it differs; and
- a small, deterministic set of changed files correlated to the failure category.

The historical and current revisions are distinct evidence records. A changed file is correlation only, never proof of causation. If the relevant workflow condition changed after the failure, patch generation is skipped for that historical incident.

## Safety boundaries

CI logs and workflow YAML are untrusted evidence. They cannot modify policy, request credentials, create approval, or enter the validator command registry. Every stored log excerpt is redacted before model context, persistence, tracing, SSE, or UI rendering. Complete job logs are not stored.

All Actions access is repository-scoped and authenticated through the existing GitHub connection. Missing log permission is a partial result: workflow/job metadata and repository evidence remain available. Actions mutations such as rerun, cancellation, workflow dispatch, and secret changes are intentionally absent.

## Bounds and replay

The limits `CI_LOG_MAX_DOWNLOAD_BYTES`, `CI_LOG_MAX_EXCERPT_BYTES`, `CI_LOG_MAX_EXCERPTS_PER_JOB`, and `CI_LOG_MAX_JOBS_PER_RUN` bound log processing. Truncation is carried in the evidence metadata and UI. The run inspector shows the normal trace plus `github.actions.resolve_run` and `ci.build_evidence` spans, alongside a persisted compact CI context (workflow, run/SHA pair, applicability, jobs, evidence IDs, and limitations). Replay reads these records without re-running GitHub calls.

## Verification status

The deterministic adapter, CI investigation service, provenance separation, redaction, partial-permission behavior, and UI rendering are covered by mocked tests. A live authenticated GitHub Actions API call has not been exercised in this workspace.
