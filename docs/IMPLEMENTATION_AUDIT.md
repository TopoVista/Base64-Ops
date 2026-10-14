# Base64 Ops implementation audit

Audit date: 2026-10-14

This audit describes the checked-in implementation and local deterministic
verification. It does not treat mocked GitHub, Clerk, MongoDB Atlas, Redis,
OpenAI, Render, Vercel, or GitHub App paths as live-provider verification.

## Current architecture

Base64 Ops is a React/Vite review workspace backed by a FastAPI control plane.
Clerk authenticates browser users; the browser supplies a Clerk bearer token
only to the API, where tenant-scoped dependencies validate it. FastAPI owns
sessions, GitHub OAuth, MongoDB persistence, bounded repository indexing,
server-sent investigation events, and LangGraph orchestration. MongoDB Atlas
Vector Search is an optional accelerator; persisted lexical chunks remain the
reliable retrieval fallback when embeddings or a vector index are unavailable.
GitHub is the repository provider. Backend workspaces are read-only unless a
separately bound exact DeliveryPlan has passed the normal approval path.

The CI recovery extension remains evidence-first. Read-only GitHub Actions
metadata, bounded redacted log windows, workflow files at the failed SHA, and
changed-path context become ordinary evidence. An optional signed
`workflow_run` webhook intake records a compact deduplicated event, queues
server-derived incident work, and never turns CI text or workflow YAML into a
command or a mutation authority.

## Important implementation locations

| Area | Current location | Current behavior |
| --- | --- | --- |
| API entrypoint | `backend/app/main.py` | FastAPI lifespan, Mongo connection/recovery, CORS, API routers, worker/index initialization, and safe readiness state. |
| Auth and tenancy | `backend/app/api/deps.py`, `backend/app/services/auth_service.py` | Validates Clerk JWTs, bounds headers, and maps the browser identity to a scoped backend user. |
| Agent graph | `backend/app/agent/{state,graph,policy,tools,prompts}.py` | Builds evidence-first context, routes Actions investigations when requested, applies deterministic policy, and interrupts before mutable actions. |
| Evidence and CI | `backend/app/services/evidence_service.py`, `ci_investigation_service.py`, `backend/app/git/actions.py` | Persists redacted provenance, reads repository-scoped Actions data, preserves run-SHA/current-SHA separation, and records explicit limitations. |
| Incidents and jobs | `backend/app/services/incident_service.py`, `background_job_service.py`, `backend/app/workers/worker.py` | Verifies signed webhook events, deduplicates/leases durable work, groups equivalent incidents, writes evidence-gated memory, and observes delivery CI read-only. |
| Memory and prevention | `backend/app/memory/*`, `prevention_rule_service.py`, `regression_prevention_service.py` | Stores freshness-aware operational memory and advisory-only repeated-incident patterns; rules are tenant/session scoped and never authorize a mutation. |
| Candidate verification | `candidate_verification_service.py`, `app/api/routes/incidents.py` | Provides an optional GitHub App installation/workflow binding and separately approved non-forced candidate ref boundary; it is disabled without explicit server configuration. |
| Patch and delivery | `patch_engine.py`, `delivery_service.py`, `app/schemas/delivery.py` | Selects evidence-related files, validates source hashes and candidate diffs, classifies deterministic risk, and binds approval to exact bytes/SHA/branch/arguments before draft-PR delivery. |
| Retrieval and graph | `rag_service.py`, `workspace_service.py`, `app/intelligence/graph/*`, `app/intelligence/impact/*` | Builds bounded lexical corpora and immutable static graph snapshots for safe advisory retrieval/impact analysis. |
| Observability and replay | `backend/app/observability/*`, `app/api/routes/dev.py` | Persists redacted spans and reconstructs historical runs without re-executing providers, validators, or mutations. |
| Frontend | `client/src/pages/session/index.tsx`, `pages/incidents/index.tsx`, `components/ide-workspace.tsx` | Renders sessions, evidence, incident inbox/detail/export, automation configuration, advisory recurrence rules, exact diff review, approval, and replay surfaces. |
| Deployment | `render.yaml`, `render.mvp.yaml`, `backend/Dockerfile`, `client/vercel.json`, `docs/DEPLOYMENT.md` | Documents both a bounded MVP profile and an expanded Render role profile with Vercel frontend hosting; no live deployment is asserted. |
| Tests and evals | `backend/tests`, `backend/evals` | Covers deterministic policy, incident lifecycle, security boundaries, Actions evidence, candidate verification, and fixture-based evaluation. |

## Current investigation and recovery flow

1. A signed-in user selects an owned GitHub repository session or an opted-in
   signed webhook identifies an already-authorized repository.
2. The graph or incident dispatcher gathers bounded repository, workflow,
   changed-path, and redacted CI-log evidence at the failed run SHA.
3. Historical/current repository state stays separate. A changed file is a
   correlation signal, never automatic proof of causation.
4. Typed hypotheses and diagnosis are linked to evidence IDs. Insufficient
   provider access remains an explicit limitation.
5. A requested repair uses the normal constrained patch -> candidate workspace
   -> validation -> deterministic risk -> exact DeliveryPlan -> approval path.
6. Optional candidate verification requires an additional exact approval and a
   configured GitHub App boundary. Normal GitHub Actions observation remains
   read-only unless the approved delivery service performs its existing draft
   PR operation.
7. Replay, incident export, feedback, flaky observations, and recurrence rules
   preserve redaction and tenant boundaries. None changes tool policy or
   mutation authority.

## Remaining gaps and intentionally unsupported behavior

- Live GitHub OAuth/Actions/webhook/App/Check, Clerk, Atlas, Redis, OpenAI,
  Render, Vercel, and worker verification requires an authorized external
  environment and is not implied by local tests.
- Candidate workflow log/artifact ingestion and automatic candidate cleanup are
  not implemented. A targeted candidate status is not equivalent to full CI.
- Repository indexing currently replaces the bounded lexical corpus for a
  session; incremental chunk reuse and graph-to-graph diffing remain future
  work.
- Static graph extraction is intentionally bounded and does not prove runtime
  calls, deployment topology, or causality. Broader extractors and
  graph-aware retrieval remain partial.
- There is no arbitrary shell execution from a model, CI log, workflow `run:`
  statement, browser text field, or incident export. GitHub PR comments,
  automatic merges, production deployment, and rollback automation are
  intentionally unsupported.
- Browser E2E coverage for authenticated provider journeys and a complete safe
  signed-in screenshot fixture are not yet available. The public `/demo`
  screenshot is reproducible but demonstrates presentation only.

## Verification snapshot

The latest local implementation verification recorded 369 backend tests,
clean Ruff, 28/28 deterministic evaluation cases, LangGraph initialization,
frontend TypeScript, Vite production build, and a bundle-budget check. The
only observed frontend build advisory is the existing large-chunk warning; it
does not fail the configured bundle budget. See
`docs/IMPLEMENTATION_STATUS.md` for phase-level status and external blockers.
