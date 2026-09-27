# System design

## Current implementation

A FastAPI control plane authenticates users and scopes sessions, evidence, plans, approvals, traces, and memory by tenant. LangGraph coordinates context loading, repository mapping, retrieval, optional CI investigation, diagnosis, and approval interruption. MongoDB persists evidence and replay-safe state.

Repository and GitHub boundaries are typed services. GitHub Actions is read-only: runs, jobs, and bounded logs become redacted Evidence. The patch engine accepts only evidence-scoped paths, builds a candidate workspace, runs allowlisted validation, and creates a DeliveryPlan. Delivery validates the exact approved plan before branch/commit/draft-PR work.

## Safety and consistency

Evidence identity includes source/provenance state. Approval binds SHA, diff, arguments, and repository. Path policy blocks traversal, secrets, Git internals, and system paths. Traces contain safe summaries, not chain-of-thought or raw secrets. Replay reads persisted state only.

## Failure and scaling considerations

Read-only CI failures degrade to partial investigations with limitations. Transient external failures use bounded retry policy; policy and validation failures are not retried. The Render blueprint separates API and worker roles. Future production scaling could add queue-backed work distribution, cache GitHub metadata per repository/run, and deploy a trace exporter, but those are not claimed as current behavior.
