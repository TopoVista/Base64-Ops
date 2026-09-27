# Why Base64 Ops exists

Base64 Ops should not compete with VS Code at editing files. It should remove the operational context-switching that surrounds code changes: repository state, CI/CD evidence, runbooks, risk classification, approvals, and the handoff from diagnosis to a reviewable pull request.

## Product thesis

The durable incentive is **evidence-backed delivery with controlled autonomy**:

1. **One operational surface.** A user can select a repository, inspect the current state, retrieve relevant sources, and see the execution timeline without moving between editor, terminal, CI dashboard, runbook, and chat.
2. **Explain before acting.** Every recommendation must cite retrieved repository or runbook evidence, show its expected blast radius, and offer a rollback path.
3. **Autonomy that earns trust.** Read-only diagnostics run directly. Any action that can mutate source control, infrastructure, or deployments pauses for human approval.
4. **Persistent operational memory.** Sessions preserve questions, evidence, timeline events, approvals, and outcomes so the next engineer starts with context instead of a blank chat.

## Research-to-product implications

Google DORA identifies reducing cognitive load as the primary purpose of platform engineering and reports that clear feedback on the outcome of tasks is strongly associated with a positive platform experience. That means Base64 Ops must prioritize visible progress, sources, and outcomes over opaque autonomous actions.

GitHub’s research reports widespread AI-tool use and suggests teams need to operationalize AI across the software lifecycle, not just use a coding assistant in isolation. The product should therefore connect AI to reviewable delivery workflows rather than replace the editor.

## Differentiators to build next

| Capability | User outcome | Trust mechanism |
| --- | --- | --- |
| Incident and change workspaces | Diagnose a failing deploy from logs, diffs, ownership, and runbooks in one session. | Evidence citations and immutable timeline. |
| Plan → simulate → approve → execute | Turn a natural-language request into a constrained, reviewable plan. | Explicit permissions, command previews, blast radius, rollback. |
| Repo-aware deployment checks | Validate Docker, Compose, CI, migrations, and policy before a change reaches production. | Read-only defaults and check results attached to the PR. |
| Operational memory | Reuse successful runbooks and prior incident knowledge across sessions. | Source provenance and per-repository isolation. |
| Outcome scorecard | Show time-to-diagnosis, approval latency, failure rate, and rollback success. | Metrics describe outcomes, not model activity. |

## 90-day delivery sequence

1. **Trust foundation:** ship the Base64 Guide, deployment configuration validation, source citations, and structured approval cards.
2. **Async execution:** move cloning, indexing, diagnostics, and notification jobs to the role-isolated workers defined in `render.yaml`; add job retries, idempotency keys, and dead-letter review.
3. **Delivery loops:** attach an evidence bundle to every pull request, add CI and deployment adapters, and show a post-deploy verification timeline.
4. **Measure value:** instrument task outcome feedback and the scorecard. Do not optimize for agent messages or tokens; optimize for fewer context switches, faster safe diagnosis, and successful reviewed changes.

## Guardrails

- Never execute a user-provided shell command directly.
- Require an approval record before any write, commit, push, workflow dispatch, deployment, rollback, or PR creation.
- Keep repository data and conversations scoped to the authenticated user and repository.
- Expose exact configuration failures in the UI; a silent button is a product failure.
- Treat AI output as a proposal with evidence, not a source of authority.

## Research sources

- [DORA: Platform engineering](https://dora.dev/capabilities/platform-engineering/)
- [GitHub: AI across software development teams](https://github.blog/news-insights/research/survey-ai-wave-grows/)
