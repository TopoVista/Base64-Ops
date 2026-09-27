# Backend / Frontend Feature-Parity Matrix

This is an implementation audit, not a roadmap. A feature is **usable** only
when its backend representation reaches an authenticated frontend surface.

| Backend capability marked DONE | Authoritative backend representation | Existing frontend surface | Audit result / required integration |
| --- | --- | --- | --- |
| First-class evidence | `GET /session/{slug}/evidence`, `evidence.items` SSE, persisted `investigations` snapshot | Command Center `EvidencePanel` | Usable: live and persisted evidence hydrate into the same cards. |
| Structured investigation | `AgentState.investigation` / `InvestigationResult`, `investigation.result` SSE | Command Center `InvestigationPanel` | Usable: summary, evidence-linked hypotheses, status, and limitations are shown. |
| Context engineering + repository map | `context_pack`, `repository_map`, `repository.context` SSE | Command Center Operation Context | Usable: repository, branch, SHA, file count, and CI-map count are shown. |
| Hybrid RAG | lexical `rag_chunks`, optional Atlas vector accelerator, `rag.sources` SSE, `/rag/sources`, direct-read fallback | RAG Sources panel + command-input status | Usable: lexical chunks persist before optional vector work; indexing remains useful without Atlas Search and reports concrete storage/connectivity status. |
| Explicit graph + timeline | `AgentState.timeline`, trace spans, node-update SSE | Command Center `TimelinePanel` | Usable: completed node events are emitted while the graph stream remains open and persist for replay. |
| Read-only diagnostics | `AgentState.tool_result`, `tool.result` SSE, persisted `investigations` snapshot | Command Center `Read-only Diagnostic` panel | Usable: only compact centrally-redacted tool output is streamed and retained with the run. |
| Hypothesis-driven investigation | `InvestigationResult.hypotheses` | Command Center `InvestigationPanel` | Usable: structured hypothesis status, explanation and evidence count are displayed. |
| Safe policy + approval binding | approval SSE and persisted ApprovalRecord | Approval banner/panel | Usable: the plan ID, base branch/SHA, exact-diff hash, approval binding hash, evidence count, validators, and current decision are visible. |
| Generalized patch engine | `DeliveryPlan`, exact unified diff, `delivery.plan` SSE | Command Center `DeliveryPlanPanel` + approval banner | Usable: plan, risk, validation steps, exact diffs and bound approval are available before decision. |
| Agent observability + replay | `/dev/runs/{runId}`, `/session/{slug}/runs/{runId}` | `/dev/runs/:runId`, Command Center Investigation replay link | Usable: active runs link directly to the configured API-backed inspector. |
| Timeline UI | `AgentState.timeline`, node-update SSE | Timeline panel | Usable: node status and detail stream during the graph run and hydrate from the persisted run snapshot. |
| Prompt-injection defense / redaction | policy/redaction services | Indirect | Usable by design; UI must label repository, workflow and CI content as untrusted evidence. |
| Evaluation harness | `evals.runner`, `artifacts/eval-summary.json` | Documentation only | Acceptable developer capability; not a normal command-center surface. |
| Dev run inspector | `/api/dev/runs/{runId}` | `DevRunInspector` | Usable: it uses the configured API base URL and is linked directly from the active investigation. |
| Failure states | failure taxonomy + index-status fields + error responses | Command Center `Repository Index` panel + input status | Usable: ready, indexing, empty, and failed states persist across reload; failures show the concrete safe reason while direct read-only inspection remains available. |
| Product UI | streamed session state | Command Center | In progress: this matrix drives completion. |
| Execution sandbox | capabilities endpoint + validation results | Inspector + Command Center Delivery Plan / approval banner | Usable: validator plan and status are exposed before approval; sandbox detail remains in the inspector. |
| Operational Memory API | `/api/memory/*`, `AgentState.operational_memory`, `memory.related` SSE | Command Center `RelatedHistoryPanel` | Usable: history is separate from evidence and labelled by freshness. |
| GitHub Actions CI Intelligence | `ci.summary`, CI Evidence, replay context | CI evidence card / inspector | Usable: the command center shows failed/current SHA, applicability, failed jobs, limitations, redacted excerpts, and the replay link. |

## Supporting DONE capabilities

These entries are deliberately not all command-center panels: some are
authorization, CI, or developer-verification mechanisms. Each entry is listed
so a backend-only capability cannot be mistaken for a missing product surface.

| DONE status entry | Backend/API/event/state representation | Exact frontend or operator surface | Audit result |
| --- | --- | --- | --- |
| Audit | `docs/IMPLEMENTATION_AUDIT.md` | Project documentation | Documentation-only; no runtime UI is appropriate. |
| Safe policy engine | `AgentState.action`, approval interrupt, `approval.requested` SSE | `ChatInput` approval banner and `Approvals` panel | Usable: policy is surfaced as a pending decision; the browser cannot bypass it. |
| Strong approval binding | `ApprovalRecord` plan/base/diff/approval hashes | `Approvals` panel and `ChatInput` exact-diff reviewer | Usable: the exact plan, base SHA, diff, approval binding, evidence count, and validators are shown before decision. |
| Prompt-injection defense | trusted policy boundary and untrusted-evidence markers | `EvidencePanel` CI/repository cards; assistant answer boundary | Usable: evidence remains data and cannot expose tool controls or mutation actions. |
| Secret redaction | centralized `RedactionService` at evidence, tool, trace, validation, and delivery boundaries | Every command-center/replay text surface | Usable: UI receives already-redacted data; no secret rendering path is added. |
| Evaluation harness | `python -m evals.runner`, generated artifact | [EVALUATION.md](EVALUATION.md) and CI output | Developer/CI capability; no customer panel is appropriate. |
| Failure taxonomy | structured failures, persisted index status/error | `Repository Index`, timeline, delivery-result panels | Usable: a safe concrete reason is retained instead of disappearing into a toast. |
| Configuration cleanup | bounded limits and settings | `Repository Index`, `Delivery Plan`, and inspector capability data | Usable: configured limits affect the visible bounded data and are inspectable in developer replay. |
| GitHub auth hardening | signed OAuth state and encrypted tokens | `ChatInput` “Connect GitHub” action | Usable: the only user entry point is the protected connection flow; tokens are never rendered. |
| Security hardening | tenant query scopes, path policy, command allowlist | Command-center data scoped by session; no cross-user navigation | Usable by design; it must not have a UI toggle that weakens it. |
| Testing | pytest, Ruff, frontend build | [README.md](../README.md) verification instructions | Engineering verification surface; no runtime UI is appropriate. |
| Documentation | `docs/` | README and linked operator documentation | Usable documentation surface. |
| CI regression gate | `.github/workflows/ci.yml` | GitHub Actions/PR checks | Operator surface is GitHub, not the Base64 command center. |
| Execution runtime sandbox | `GET /api/system/capabilities`, sandbox validation result | `Delivery Plan` validators and `DevRunInspector` | Usable: a user sees plan/result summaries; raw sandbox controls are intentionally not exposed. |
| Live evaluation framework | `python -m evals.runner --mode live`, allowlist/budget guards | [LIVE_EVALUATION.md](LIVE_EVALUATION.md) and manual Actions workflow | Explicit operator-only capability; intentionally absent from normal UI. |
| Operational Memory API | `/api/memory/*`, `memory.related` SSE | `Related History` panel | Usable: relevant verified history is separate from current evidence. |

## Completion evidence

The command center is complete only when one deterministic session displays:

`repository context -> timeline -> evidence -> hypotheses -> diagnosis -> related history -> CI context -> failed/current SHA -> applicability -> delivery plan -> exact diff -> validation -> risk -> approval -> delivery result`.

Every capability above now has an explicit product, operator, or
documentation surface. The deterministic browser-rendered smoke capture is
available at [demo-command-center-full.png](../artifacts/demo-command-center-full.png)
and was taken from `/demo` after a production frontend build. It proves the
complete visible command-center chain without requiring a live GitHub mutation.

## Deterministic rendered fixture

Anyone can open `/demo` to render the `ci-stale-working-directory`
fixture through the exact Command Center snapshot contract. It visibly covers
repository context, timeline, read-only diagnostic, evidence, hypotheses,
memory, CI applicability, plan, exact diff, validators, exact approval
binding, and a clearly labelled fixture-only delivery result. It disables
every mutating control and does not access GitHub, clone a repository, create
a branch, commit, or pull request.
