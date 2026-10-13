# Base64 Ops next-generation CI recovery progress

## Stage A — audit and baseline

Status: **IN PROGRESS**

- Read the master implementation requirements and audited the existing backend,
  frontend, deployment manifests, tests, and documentation index.
- Recorded the implementation inventory and P0 gaps in
  [IMPLEMENTATION_GAP_ANALYSIS.md](IMPLEMENTATION_GAP_ANALYSIS.md).
- Baseline backend command: `backend\\venv\\Scripts\\python.exe -m pytest backend\\tests -q`
  - Initial result: **315 passed**, one third-party deprecation warning.
  - After P0.1 intake changes: **319 passed**, one third-party deprecation warning.
- A production frontend build was started during the audit but requires a clean
  rerun before it is recorded as passing.
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
- Completed failed/timed-out Actions runs are persisted in an additive Mongo
  event ledger keyed by GitHub delivery ID before a dispatch job is queued.
- Duplicate deliveries are idempotent. Raw webhook payloads are not retained;
  the ledger stores only bounded normalized metadata and a SHA-256 hash.
- Repository automation is opt-in and target-matched by a GitHub numeric
  repository ID derived from the authenticated user's own repository list.
- The optional `incident_dispatcher` worker role uses existing durable lease
  and bounded-retry infrastructure, then calls the existing read-only CI
  investigation service. It cannot perform a GitHub mutation.
- A minimal authenticated `/api/incidents` read/configuration API now exists.

Still required in this stage:

- GitHub App installation identity support (current OAuth-based integration
  has no installation-token boundary).
- Incident inbox/detail UI, live GitHub webhook verification, a deployed
  dispatcher/scheduler profile, and GitHub-native reporting.

## Current external blockers

1. The Render Docker service must have `TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP`
   in its dashboard environment, in addition to the configured Redis URL and
   fail-closed rate-limit setting. Until then startup validation correctly
   refuses production traffic.
2. Live GitHub webhook, GitHub App, and authorized test repositories are not
   configured in this workspace. Local mocked tests can be added without those
   credentials; live verification remains explicitly unverified.
