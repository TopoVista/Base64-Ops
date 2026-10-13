# GitHub Actions intelligence

Base64 Ops uses GitHub Actions as a read-only operational signal source. The existing OAuth-backed GitHub service remains the only HTTP boundary; `GitHubActionsAdapter` normalizes workflow runs, jobs, and bounded job-log downloads into typed models.

## Investigation flow

For a CI-failure request, the graph classifies the request, resolves an explicit/latest/PR-head-matched failed run within the session repository, gathers failed jobs and bounded failure excerpts, then attaches the results to the normal evidence-first investigation. It separately records:

- the failed run SHA;
- the workflow file at that SHA when Git history is available;
- the current workflow revision when it differs; and
- a small, deterministic set of changed files correlated to the failure category.

The historical and current revisions are distinct evidence records. A changed file is correlation only, never proof of causation. If the relevant workflow condition changed after the failure, patch generation is skipped for that historical incident.

When bounded metadata identifies a prior comparable successful run at a
different SHA, Base64 Ops also reads the workflow file at that successful SHA.
The earlier workflow and failed workflow remain separate `repo_file` evidence.
Their content comparison is a limited correlation signal: a workflow change can
guide review but never proves it caused the failure.

## Safety boundaries

CI logs and workflow YAML are untrusted evidence. They cannot modify policy, request credentials, create approval, or enter the validator command registry. Every stored log excerpt is redacted before model context, persistence, tracing, SSE, or UI rendering. Complete job logs are not stored.

All Actions access is repository-scoped and authenticated through the existing GitHub connection. Missing log permission is a partial result: workflow/job metadata and repository evidence remain available. Actions mutations such as rerun, cancellation, workflow dispatch, and secret changes are intentionally absent.

## Bounds and replay

The limits `CI_LOG_MAX_DOWNLOAD_BYTES`, `CI_LOG_MAX_EXCERPT_BYTES`, `CI_LOG_MAX_EXCERPTS_PER_JOB`, and `CI_LOG_MAX_JOBS_PER_RUN` bound log processing. Truncation is carried in the evidence metadata and UI. The run inspector shows the normal trace plus `github.actions.resolve_run` and `ci.build_evidence` spans, alongside a persisted compact CI context (workflow, run/SHA pair, applicability, jobs, evidence IDs, and limitations). Replay reads these records without re-running GitHub calls.

## Automated incident intake and sharing

An optional repository setting enables signed `workflow_run` failure intake.
The webhook route verifies GitHub's exact-body HMAC, stores a compact,
deduplicated event envelope, and queues only server-derived incident work.
The dispatcher reuses the read-only investigation service; it does not add a
GitHub mutation capability. Repository identity is derived from the connected
user's GitHub repository view, never from a browser-supplied owner/name.

For an opted-in workflow, a later successful run on the exact same SHA is
attached as reconciliation context to the matching group root. It does not
close the incident, label a test flaky, or authorize a repair: a same-SHA
success is a useful transient-condition signal, not a root-cause verdict.

Pytest/Jest-style failed-test identifiers are extracted from already-bounded,
redacted failure windows and remain untrusted evidence. Base64 Ops marks a test
as **suspected** only when that identifier is present and bounded history has
mixed successful and failed runs at the same SHA. It neither reruns CI nor
declares a test flaky automatically.

Each extracted identifier now also creates a tenant-scoped
`flaky-test-observation-v1` record. The record stores a bounded workflow key,
failed-run SHA, comparable same-SHA outcome counts, evidence IDs, timestamp,
and explicit limitations. Successful Actions runs usually lack test-level
results, so a mixed result remains workflow-level support for a *suspected*
signal, never a test-level verdict. Observations are viewable through the
read-only `GET /api/incidents/flaky-tests` endpoint; they do not rerun,
quarantine, disable, or otherwise mutate CI.

The Incident Inbox lets a user select an owned repository session, opt in or
out, restrict workflows/branches, and set a bounded daily investigation limit.
The detail view can export a JSON or Markdown incident record. Export queries
are tenant- and session-scoped, include only evidence IDs retained by that
incident, allowlist presentation fields, and apply centralized redaction again
before bytes leave the API. Raw webhook payloads, OAuth data, queue IDs, and
evidence metadata are never exported.

An evidence-supported automated diagnosis can create an `INCIDENT_OUTCOME`
through the existing tenant-scoped memory policy. It is stored as
`partially_verified` historical context and is lower-precedence than current
run/repository evidence. It does not imply a repair was proposed, delivered,
merged, or verified. Users can add redacted feedback to an incident detail;
feedback is an untrusted review annotation and cannot alter evidence, approval,
or repository authorization.

## Candidate verification (optional GitHub App authority)

Candidate verification is an additional, opt-in GitHub App boundary; it is not
part of the OAuth-backed read-only Actions adapter. A repository owner may bind
one existing `.github/workflows/*.yml` workflow to one owned session from
Incident Inbox. The server validates the App installation against the
OAuth-authorized repository, confirms only that the reviewed workflow file
exists, and stores the binding without retaining App tokens or private-key
material.

For a separately approved immutable DeliveryPlan, Base64 creates a non-forced
`base64-verify/...` candidate branch from the exact base SHA, writes only the
exact reviewed blobs/tree/commit, and dispatches that existing workflow.
Workflow YAML and `run:` commands remain untrusted data: they never become
Base64 validator commands. Plans touching `.github/workflows/` are refused for
candidate verification. Dispatch is not a PR delivery and does not authorize a
normal push.

The `verification_runs` collection stores a tenant-scoped, bounded
`verification-run-v1` audit record. The `ci_observer` worker or an explicit
status refresh observes only the configured workflow's metadata for the exact
candidate branch/SHA. `targeted_verification_passed` is intentionally not
presented as full CI success. See [CANDIDATE_VERIFICATION.md](CANDIDATE_VERIFICATION.md)
for App permissions, workflow hardening, lifecycle, and limitations.

## Verification status

The deterministic adapter, CI investigation service, provenance separation, redaction, partial-permission behavior, and UI rendering are covered by mocked tests. A live authenticated GitHub Actions API call has not been exercised in this workspace.

The GitHub App candidate-verification boundary is implemented and mock-tested,
but disabled by default. A live App installation and authenticated Actions
dispatch have not been exercised in this workspace. Optional GitHub Check
reporting is also implemented and mock-tested: it requires a separate
repository opt-in, the already verified App binding, and explicit
`Checks: write`; it creates or updates only a neutral redacted Check after a
durable incident exists. PR comments remain unsupported. OAuth read access is
never silently promoted into verification or reporting authority.
