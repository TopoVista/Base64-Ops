# Base64 Ops next-generation CI recovery progress

## Stage A — audit and baseline

Status: **IN PROGRESS**

- Read the master implementation requirements and audited the existing backend,
  frontend, deployment manifests, tests, and documentation index.
- Recorded the implementation inventory and P0 gaps in
  [IMPLEMENTATION_GAP_ANALYSIS.md](IMPLEMENTATION_GAP_ANALYSIS.md).
- Baseline backend command: `backend\\venv\\Scripts\\python.exe -m pytest backend\\tests -q`
  - Initial result: **315 passed**, one third-party deprecation warning.
  - After incident API, deterministic diagnosis, and Inbox/detail changes:
    **329 passed**, one third-party deprecation warning.
- Frontend validation after the Incident Inbox slice:
  - `npm run lint`: passed.
  - `npx tsc --noEmit`: passed.
  - `npm run build`: passed (Vite production output generated; existing large
    bundle advisory remains non-fatal).
- Existing uncommitted user change: `backend/.gitignore`. It is outside this
  work and will not be modified.

## P0 next slice — webhook intake and durable incident ledger

Status: **PARTIALLY IMPLEMENTED AND TESTED**

Planned reuse:

- GitHub API boundary: `app/services/github_service.py`.
- Actions evidence gathering: `app/services/ci_investigation_service.py`.
- Durable queue semantics: `app/services/background_job_service.py`.
- Worker lease/retry semantics: `app/workers/worker.py`.
- Existing evidence, approval, memory, and replay boundaries.

Implemented so far:

- `POST /api/github/webhooks` verifies `X-Hub-Signature-256` over the exact
  raw request body and rejects absent/invalid secrets and oversized payloads.
- Completed Actions runs, including successful same-SHA reconciliation
  signals, are persisted in an additive Mongo event ledger keyed by GitHub
  delivery ID before any dispatch or reconciliation occurs.
- Duplicate deliveries are idempotent. Raw webhook payloads are not retained;
  the ledger stores only bounded normalized metadata and a SHA-256 hash.
- Repository automation is opt-in and target-matched by a GitHub numeric
  repository ID derived from the authenticated user's own repository list.
- The optional `incident_dispatcher` worker role uses existing durable lease
  and bounded-retry infrastructure, then calls the existing read-only CI
  investigation service. It cannot perform a GitHub mutation.
- A minimal authenticated `/api/incidents` read/configuration API now exists;
  list/detail responses omit tenant IDs, queue/event IDs, and automation IDs.
- A compact Incident Inbox and incident-detail route now display persisted,
  user-authorized incident summaries with loading, empty, error, filter, and
  historical-run states. The views do not render raw CI logs or introduce a
  mutation control.
- Deterministic diagnosis labels distinguish insufficient, observed,
  correlated, and supported evidence. Changed-file correlation is explicitly
  described as non-causal unless further evidence supports it.
- Repository daily investigation budgets are checked from durable incident
  records by the worker. An exhausted budget is recorded as an observable,
  ignored incident instead of silently spending work.
- The CI investigation service now compares bounded, workflow/branch-matched
  Actions metadata when timestamps are available. It records a prior success,
  first observed failure, and same-SHA outcomes as redacted `tool_result`
  evidence. These signals are intentionally labeled correlation, never proof
  of causality or flakiness.
- When a prior comparable success uses a different commit, the workflow file
  at that successful SHA is retained as separate evidence and compared with
  the failed-run workflow. A difference remains correlation only.
- Automated incidents persist a versioned structured diagnosis with its
  supporting evidence IDs, relevant paths, historical comparison, applicability
  state, and a safe next action. The incident detail UI exposes the compact
  comparison and its limitations.
- Evidence-equivalent incidents now receive a conservative SHA/workflow/job/
  category group key after collection. Every event remains stored and
  traceable, while Inbox list queries show the group root and related run
  count. The grouping key does not use raw log text and therefore cannot merge
  failures solely on untrusted log similarity.
- Group roots retain a bounded, presentation-safe repeated-failure timeline
  (run ID, SHA, observed time, categories, and failed-job names). It excludes
  raw log content and explicitly remains a recurrence signal, not causality or
  a flaky-test verdict.
- A later signed successful run is reconciled only with an opted-in incident
  root for the same repository, workflow, branch, and SHA. It is shown as a
  possible transient-condition signal and never closes the incident or grants
  a repair authorization.
- Failed pytest/Jest-style identifiers are now retained as bounded untrusted
  evidence. A `suspected` flaky signal needs both an extracted identifier and
  mixed same-SHA history; it has no auto-rerun or auto-resolution behavior.
- Each extracted failed-test identifier now creates a tenant-scoped,
  bounded `flaky-test-observation-v1` record. It captures the test identity,
  workflow/SHA scope, comparable same-SHA counts, evidence references, and
  limitations. A read-only `/api/incidents/flaky-tests` route exposes only
  public presentation fields; it does not treat workflow-level successes as
  proof that an individual test is flaky.
- Incident grouping retries when evidence collection completed but the final
  grouping write was interrupted; a collected record becomes terminal only
  after it is a root or group member.
- The Inbox now exposes the existing server-verified repository automation
  controls (opt-in, workflow/branch filters, and daily budget) rather than a
  disconnected UI toggle. Incident detail supports portable JSON or Markdown
  exports that re-redact and allowlist evidence fields at export time.
- Incident detail accepts a tenant-scoped, centrally redacted user annotation
  (`helpful`, `incorrect`, or `needs_more_evidence`). It cannot rewrite
  evidence, change an approval, or acquire repository authority.
- Correlated or supported automated diagnoses write an `INCIDENT_OUTCOME`
  through the existing MemoryWritePolicy. These entries are partially verified
  historical context, never a delivered or merged fix; a write error is an
  incident limitation rather than a reason to discard the incident.

Still required in this stage:

- Live GitHub webhook verification, a deployed dispatcher/scheduler profile,
  actual runner-environment metadata where GitHub exposes it, prevention-rule
  promotion, and live candidate/App verification. Dependency/lockfile and
  declared-runner workflow comparisons are now bounded correlation signals;
  optional neutral GitHub Check reporting has a separate opt-in and App
  permission boundary.

## Verified-repair contract

Status: **PARTIALLY IMPLEMENTED AND TESTED**

- Every newly persisted exact `DeliveryPlan` now contains an immutable
  `candidate-verification-v1` record with its base SHA and candidate diff hash.
- It records either `proposed` / `not_independently_tested`, or (when existing
  registry-backed checks pass) `static_validation_passed` /
  `static_validation` with a bounded command-free result summary. Neither
  state proves targeted candidate verification or full CI success.
- An optional GitHub App installation-token boundary is implemented. It
  requires a repository-scoped opt-in binding to an existing reviewed workflow,
  creates a separate exact dispatch approval, creates a non-forced candidate
  branch only after approval, and persists a `verification-run-v1` outcome.
  OAuth credentials do not gain dispatch authority from this record. The
  feature is disabled by default; mocked tests pass, while live GitHub App
  installation, dispatch, and runner behavior remain explicitly unverified.
  See `CANDIDATE_VERIFICATION.md`.

## Current external blockers

1. The Render Docker service is currently refusing startup until
   `TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP` is added in its dashboard
   environment. This is a required production safeguard, not a code defect.
   The supplied deployment logs confirm that the image build completed before
   configuration validation stopped Uvicorn.
2. Live GitHub webhook, GitHub App, and authorized test repositories are not
   configured in this workspace. Local mocked tests cover the GitHub App
   boundary without credentials; live verification remains explicitly
   unverified.

## Deployment profile update

- `render.free.yaml` is an additive, test-validated Free Render profile with
  distinct resource names. It provides the API control plane plus volatile
  private Key Value only; it deliberately has no persistent worker and does
  not configure webhook intake by default.
- The existing `render.yaml` and `render.mvp.yaml` remain paid `0.5c-512mb`
  profiles. No existing deployment was silently downgraded.
