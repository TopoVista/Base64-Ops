# Base64 Ops implementation gap analysis

Audit date: 2026-10-13

This inventory is based on the current source tree and local test suite. It
does not treat mocked provider tests as live-provider verification.

## Reusable, implemented foundations

| Area | Status | Relevant implementation | Evidence / limitation |
| --- | --- | --- | --- |
| Authentication and tenant boundary | Implemented and tested | `backend/app/api/deps.py`, `app/services/auth_service.py` | Clerk bearer tokens are verified before browser-facing routes. Live Clerk configuration remains environment-dependent. |
| GitHub OAuth and repository boundary | Implemented and tested | `app/services/github_service.py`, `app/api/routes/github.py` | OAuth state is one-time and token storage is encrypted. GitHub API availability and scopes have not been exercised in this audit. |
| Read-only Actions intelligence | Implemented and tested | `app/git/actions.py`, `app/services/ci_investigation_service.py` | Runs, jobs, bounded redacted excerpts, run-SHA workflow evidence, and changed-path correlation exist. It is initiated by a session request, not a webhook. |
| Evidence-first investigation | Implemented and tested | `app/services/evidence_service.py`, `app/agent/graph.py` | Evidence IDs, provenance, bounded context, and untrusted-input handling exist. |
| Constrained repair and approval | Implemented and tested | `app/services/patch_engine.py`, `app/services/delivery_service.py`, `app/schemas/delivery.py` | Exact diffs, validation, risk, stale-plan invalidation, and approval binding exist. Arbitrary mutation remains intentionally unsupported. |
| Delivery CI observation | Implemented and tested | `app/services/delivery_ci_service.py`, `app/workers/worker.py` | A Mongo-backed job can poll Actions after an approved delivery. This is not candidate verification. |
| Operational memory | Implemented and tested | `app/memory/*` | Tenant-scoped evidence-gated memory distinguishes incident outcomes, delivered fixes, and merged fixes. Live merge lookup remains unverified. |
| Durable internal jobs | Implemented and tested | `app/services/background_job_service.py`, `app/workers/worker.py` | Lease, dedupe, bounded retry, and terminal status semantics exist for reindexing and delivery CI observation. |
| Observability and replay | Implemented and tested | `app/observability/*`, `app/schemas/replay.py`, `app/api/routes/dev.py` | Redacted trace/replay supports existing agent runs. |
| Code workbench | Implemented and partially browser-verified | `client/src/components/ide-workspace.tsx`, `vscode-editor.tsx` | Supports evidence-scoped code review and proposal flow. |
| Incident Inbox, controls, and export | Implemented and locally build-verified | `client/src/pages/incidents/index.tsx`, `app/api/routes/incidents.py`, `app/services/incident_export_service.py` | Renders tenant-scoped persisted incidents, enables server-verified repository automation opt-in, and exports only redacted evidence. Browser E2E and live data verification remain outstanding. |

## Partially implemented or incomplete

| Capability | Current state | Gap to close |
| --- | --- | --- |
| CI causality | Existing CI service gathers failed-run workflow, changed-file, and current/head evidence; incident dispatch persists a deterministic evidence-strength assessment, bounded same-workflow run comparison, and conservative repeat-failure group key. | No workflow/dependency diff comparison against the last success, recurrence aggregation, or incident timeline. |
| Automated CI follow-up | Signed workflow-run webhook intake, opt-in repository automation, durable event ledger, and incident-dispatch job type are implemented. | GitHub App installation validation and live webhook/worker verification remain outstanding. |
| Repair verification | Static/candidate validation and later read-only delivery CI observation exist. | No opt-in immutable candidate verification dispatch or auditable `VerificationRun`; no workflow is installed automatically. |
| Historical intelligence | Operational memory includes validity and freshness controls. | No flaky-test observation model, prevention-rule promotion, or advisory view tied to a new incident. |
| Frontend CI experience | Code workbench, CI evidence cards, Inbox/detail, repository automation controls, evidence export, and conservative repeated-failure grouping exist. | The complete user-facing repair/verification state machine remains incomplete. |
| Deployment | Render blueprints model API/worker roles and now a private Key Value dependency. | The deployed Docker service does not automatically consume Blueprint variables; its Render dashboard must be configured explicitly. The checked-in API plan is paid, not a complete free-tier profile. |

## Missing P0 functionality

1. GitHub App installation identity validation, which is distinct from the
   existing OAuth repository boundary.
2. Incident grouping, reconciliation, supersession, and successful-run
   comparison.
3. An incident data model that links a repair candidate, verification run, and
   final outcome without duplicating the existing delivery state.
4. User-controlled notification policy.
5. GitHub-native checks or PR-comment publication, which requires an explicit
   permission model beyond the current OAuth read path.

## Existing public contracts and security boundaries

- Browser API routes use Clerk authentication and user-scoped session ownership.
- GitHub OAuth callback is unauthenticated only because its signed, one-time state controls the user association.
- GitHub Actions logs and repository workflow YAML are redacted, bounded, and marked untrusted before persistence/model context.
- Approval is bound to exact repository SHA, branch, diff, and delivery arguments. CI evidence cannot confer mutation authority.
- Internal background jobs contain server IDs rather than browser/repository-controlled commands.
- MongoDB is the durable source for session, job, memory, and delivery state. Redis is rate-limit state only and must not be used as the sole event ledger.

## Proposed incremental implementation

### P0.1 — intake and ledger

Extend the existing GitHub router, Mongo database, `BackgroundJobService`, and
worker. Do not introduce another GitHub client or queue. The route will verify
the raw GitHub HMAC signature, bound the body size, persist a redacted event
envelope keyed by delivery ID, and enqueue only server-derived incident work.

### P0.2 — incident model and deterministic diagnosis

Reuse `CIInvestigationService` for run/job/log/workflow/changed-file collection.
Persist a compact incident record containing IDs and summaries, not raw logs.
Begin with deterministic categories and evidence-strength labels; an LLM may
supplement but must not replace evidence.

### P0.3 — user controls and presentation

Add an explicit repository automation setting plus read-only incident list and
detail endpoints. The frontend should surface only persisted, authorized
incident data and link to existing evidence/review screens.

### P0.4 — verification boundary

Keep the existing approval pipeline authoritative. A future verification
dispatcher must be opt-in, use a reviewed repository-owned workflow, pin the
candidate SHA/diff hash, and never convert CI YAML or LLM text into commands.

## Migration and compatibility risks

- Existing sessions store canonical clone URLs but not GitHub numeric repository
  IDs. The event ledger must store both and only associate a webhook with a
  previously authorized, opt-in session.
- A single OAuth token cannot safely be treated as a GitHub App installation
  token. Check/PR publication and verification dispatch remain unavailable
  until explicit permissions and an authorization boundary are added.
- Free Render web services cannot guarantee a continuously running worker.
  Intake must remain durable; automatic processing can be configured only when
  an approved worker/scheduler is present, with clear deferred status otherwise.
- No destructive schema migration is needed: all new collections and indexes
  are additive.

## Live verification status

- Local deterministic backend suite: 315 passed on 2026-10-13.
- Render startup configuration has been diagnosed from supplied logs but is not
  yet live-verified because required dashboard environment settings are being
  added.
- Live GitHub OAuth, Actions API, webhook delivery, GitHub App permissions,
  isolated verification, and GitHub-native reporting are unverified.
