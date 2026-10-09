# Implementation Status

> Phase 14 update (2026-09-26): real local Git changed-path comparison, GitHub compare fallback, fail-safe unreachable-history handling, selected-memory revalidation, and verified PR merge promotion are now implemented. The phase remains `PARTIAL` solely because a live authenticated GitHub merge lookup has not been exercised; temporary-repository Git tests and mocked GitHub lifecycle tests pass.

> GitHub Actions CI Intelligence update (2026-09-26): `DONE` for the deterministic product vertical slice. Read-only run/job/log collection, explicit/latest/PR-head run selection, bounded redacted excerpts, workflow-path resolution, run-SHA/current-SHA provenance, workflow-at-ref evidence, conservative changed-file and safe log-path correlation, graph routing, typed applicability, historical/unknown duplicate-patch prevention, CI evidence cards, command-center summary, persisted replay context, safe trace stages, and a 13-scenario deterministic CI eval lane are implemented. Live authenticated Actions verification is `NOT EXERCISED`; it is an external verification status, not a claim about the deterministic feature.

> Portfolio/demo hardening update (2026-09-26): `DONE`. The repository now includes a product-first README, deterministic current/historical CI walkthroughs, PowerShell/POSIX demo and verification scripts, generated eval-summary support, architecture/safety diagrams, interview talk track, system design notes, threat model, and deployment readiness guidance. Live GitHub and live delivery verification remain `NOT EXERCISED`.

> Scale/security update (2026-10-05): `PARTIAL`. The Render blueprint declares the API, maintenance cron, autoscaled indexer workers, and autoscaled read-only CI observers. Production requires HTTPS origins, trusted hosts, Redis-backed fail-closed traffic protection, bounded Mongo connection pools, and non-development OAuth/encryption secrets. Readiness refuses traffic while Mongo or required Redis traffic protection is unavailable; database recovery recreates correctness-critical indexes before database-backed traffic resumes. Every model call has a bounded context, output, deadline, and retry count; server-side tenant-period token metering records provider-reported usage without trusting browser counters or inventing cost. Provider-reported usage from every graph model call, including structured patch proposals and final answers, is recorded separately so one call cannot hide another. Plan-scoped monthly model-token ceilings reject a new agent run after the observed period budget is exhausted; the current run remains bounded by its context/output limits and tenant agent lease. Low-risk product-guide traffic uses a lower-cost model while repository investigations use the balanced model. Product-guide history is redacted before persistence/model context and is explicitly untrusted. Clerk bearer headers are size-bounded before JWT/JWKS work and the subject is validated before becoming a tenant identity. Repository indexing now has explicit aggregate file, byte, and chunk ceilings, runs its blocking clone/corpus preparation off the worker event loop, deterministically selects the retained subset, and exposes a partial-corpus state rather than claiming complete retrieval coverage. Browser-polled session hydration no longer triggers GitHub/workspace discovery by default; CI is explicitly refreshed or observed post-delivery. Workers drain by stopping new claims on SIGTERM and use short renewable fencing leases; least-privileged workers share only the API-owned Mongo/token-encryption values they need, preventing token-decryption incompatibility. A non-billable k6 staging harness now tests ingress and authenticated reads only; it refuses model, indexing, approval, delivery, and billing surfaces. Live provider/deployment/load verification remains not exercised.

> Safe Repair MVP update (2026-10-06): `DONE` for the local product flow. “Repair latest CI failure” goes directly to the Code workbench, refreshes the latest failed run, gathers redacted evidence, and requests the normal evidence-scoped exact DeliveryPlan. The workbench renders the minimal green/red diff with failure rationale, pre-delivery validation state, a local apply/review step, editable AI-suggested commit message, and the existing approval-bound GitHub delivery action. Code-originated CI repairs retain traces, evidence, plans, approvals, and replay while deliberately not persisting their internal orchestration prompt or generated report in the Assistant transcript. The public `/demo` fixture has an auto-playable, click-through walkthrough. Live GitHub/model/delivery verification remains `NOT EXERCISED`.

> MVP deployment update (2026-10-06): `DONE`. `render.mvp.yaml` is the default low-cost 100–500-user profile: one 512 MB API service, bounded inline indexing, and read-only CI status observation in the Code commits rail. `render.yaml` remains the optional measured-growth profile with separate worker roles. Both preserve Redis-backed fail-closed rate limits, Atlas durability, token/tenant limits, and the same evidence/approval boundaries.

> MVP UX refinement update (2026-10-06): `DONE`. Code now offers the primary repair action without a Pipelines detour, explains visible evidence-lock → minimal-diff → validation/approval progress, and turns a historical already-fixed failure into an informational state that cannot create a duplicate repair. `/demo` is a standalone, auto-playing and click-through Code-workbench fixture (not a chat-shaped command-center simulation). It presents the same observed → inferred → proposed → approval-bound-delivery sequence, without accessing GitHub or allowing mutations.

Status reflects executable code in this repository as of 2026-10-06. `BLOCKED` means credentials or an external service are required; it does not mean a simulated integration exists.

| Phase | Status | Notes |
| --- | --- | --- |
| 0. Audit | DONE | `IMPLEMENTATION_AUDIT.md` documents the existing system and gaps. |
| 1. First-class evidence | DONE | Mongo `evidence` records are scoped, hashed, redacted, streamed, and shown in the UI. |
| 2. Structured investigation | DONE | Typed schemas and multi-hypothesis LLM synthesis scaffold implemented. |
| 3. Context engineering | DONE | Trusted policy + request + repository map context pack built with candidate file boundaries. Documented in `CONTEXT_ENGINEERING.md`. |
| 4. Repository map | DONE | Repository maps are built deterministically and persisted by session/commit. |
| 5. Hybrid RAG | DONE | Lexical-first chunk persistence and retrieval work on every supported Mongo deployment. Atlas vector search is an optional configured accelerator; its absence cannot empty the repository index. |
| 6. Incremental indexing | NOT STARTED | Indexing currently replaces session repository chunks. |
| 7. Explicit investigation graph | DONE | LangGraph flow executes load_context -> repo_map -> retrieve -> plan -> investigate -> generate_delivery -> approval -> execute -> respond. |
| 8. Read-only diagnostic tools | DONE | Git status, Docker build, and Compose validation implemented with capability detection. |
| 9. Hypothesis-driven investigation | DONE | Evidence-linked provisional hypothesis and structured proposals implemented. |
| 10. Safe policy engine | DONE | Registered deterministic read/proposal/write/privileged policies determine approval. |
| 11. Strong approval binding | DONE | Canonical SHA-256 approval binding checks plan, diff, action, arguments, repository, and HEAD before execution. |
| 12. Generalized Patch Engine | DONE | Evidence-backed candidate selection, structured edit proposals, path security validation, original hash verification, candidate workspace diff generation, surface classification, and risk rules implemented. Zero LLM post-approval execution. |
| 13. Change impact | NOT STARTED | Intentionally deferred. |
| 14. Operational memory | PARTIAL | Semantic memory kinds, verification lifecycle, path-aware freshness, historical recall, policy-controlled writes, and current-evidence precedence are implemented. Memory cannot authorize mutation. Delivered ≠ merged. Local Git changed-path comparison is wired; live authenticated GitHub merge lookup remains not exercised. |
| 15. User feedback | NOT STARTED | |
| 16. Agent observability | DONE | Per-node tracing, token usage telemetry, retrieval score metadata, and protected `/dev/runs/:runId` run inspector implemented. |
| 17. Timeline UI | DONE | Safe graph events, evidence, patch proposal, risk level, and exact diff reviewer displayed in UI. |
| 18. Screenshot evidence | NOT STARTED | |
| 19. Playbooks | NOT STARTED | |
| 20. Prompt-injection defense | DONE | Trusted policy treats retrieved repository data as untrusted evidence; static sanity check rejects malicious code injection. |
| 21. Secret redaction | DONE | Central redaction covers evidence, validation summaries, execution failures, and PR descriptions. Memory values redacted before persistence. |
| 22. MCP | DEFERRED | Intentionally deferred. Typed adapters are used. |
| 23. Evaluation harness | DONE | 28 deterministic cases, including 13 GitHub Actions CI cases. Results are generated through `evals.runner` and can be written to `artifacts/eval-summary.json`. |
| 24. Dev Run Inspector UI | DONE | Protected `/dev/runs/:runId` UI displays graph nodes, retrieval scores, token telemetry, PatchProposal files, validation status, and diffs. |
| 25. Failure states | DONE | Failure taxonomy, failure stage persistence, and bounded retry rules implemented. |
| 26. Product UI | DONE | The MVP navigation centers Safe Repair, CI evidence, evidence, repository map, and run history. “Repair latest CI failure” enters the Code workbench rather than chat, where the minimal exact diff, rationale, validation state, local apply/review, suggested commit message, approval-bound push, and post-push CI rail are presented together. Historical failures already fixed on current HEAD cannot produce duplicate proposals. The public, mutation-disabled `/demo` route is an auto-playable and click-through Code-workbench tour of that proof chain. |
| 27. Configuration cleanup | DONE | Configurable patch limits and memory settings (`memory_max_context_entries=5`, `memory_learning_enabled`). |
| 28. GitHub auth hardening | DONE | OAuth state signing and encrypted token storage implemented. |
| 29. Security hardening | DONE | Tenant-scoped queries, strict `RepositoryPathPolicy`, allowlisted commands, redaction, memory-cannot-authorize invariant. |
| 30. Testing | DONE | 256 backend tests passed, 28/28 deterministic evals, clean Ruff, frontend TypeScript, bundle-budget verification, npm audit, LangGraph initialization, and Vite build passed in the latest local verification. The production bundle has 120 JavaScript chunks (4.79 MB total; 662.1 KB largest) within the repository's configured budget. Live provider and load tests remain not exercised. |
| 31. Documentation | DONE | `PATCH_ENGINE.md`, `EXECUTION_SANDBOX.md`, `LIVE_EVALUATION.md`, `AGENT_SAFETY.md`, `EVALUATION.md`, `OBSERVABILITY.md`, `ARCHITECTURE.md`, `CONTEXT_ENGINEERING.md`, `OPERATIONAL_MEMORY.md`, `FEATURE_PARITY_MATRIX.md`, `IMPLEMENTATION_STATUS.md`. |
| 32. CI Regression Gate | DONE | `.github/workflows/ci.yml` runs backend tests, Ruff, deterministic evals, frontend build, a JavaScript bundle budget, and high-severity npm advisory gating. |
| 33. Execution Runtime Sandbox | DONE | Subsystem `app.execution` implemented with request/result models, command registry, environment sanitization, process tree termination, output bounding, candidate workspace integrity checks, Docker sandbox runtime (`--network none`), and `GET /api/system/capabilities` API. |
| 34. Live Evaluation Framework | PARTIAL | Configurable evaluation mode (`python -m evals.runner --mode live`), budget guards (`--max-cases`), and manual dispatch workflow (`.github/workflows/live-evals.yml`) exist. It currently executes deterministic coverage only; authenticated live model/GitHub delivery verification is explicitly reported as `not_exercised`, not a passing check. |
| 35. Operational Memory API | DONE | `GET /api/memory/{repository_id}`, `GET /api/memory/{repository_id}/staleness`, `POST /api/memory/{repository_id}/check-staleness`, `DELETE /api/memory/entry/{entry_id}` — all user-scoped. |

## External requirements

Full authenticated execution requires `MONGO_URI`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_OAUTH_STATE_SECRET`, `GITHUB_TOKEN_ENCRYPTION_KEY`, `OPENAI_API_KEY`, `FRONTEND_ORIGIN`, and `BASE_URL` values. Configure `CLERK_AUTHORIZED_PARTIES` with any browser origins intentionally permitted to send API tokens.

## Operational Memory DONE criteria

Phase 14 is marked **PARTIAL** because the following external verification is not yet complete:

- `MERGED_FIX` end-to-end: requires live GitHub integration; write policy is implemented and tested in unit tests but not exercised against real GitHub PR merge events.

Phase 14 becomes DONE when at least one live integration test confirms `MERGED_FIX` is written only after a GitHub PR actually merges.
