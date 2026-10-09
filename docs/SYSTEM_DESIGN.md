# System design

## Current implementation

A FastAPI control plane authenticates users and scopes sessions, evidence, plans, approvals, traces, and memory by tenant. LangGraph coordinates context loading, repository mapping, retrieval, optional CI investigation, diagnosis, and approval interruption. MongoDB persists evidence and replay-safe state.

Repository and GitHub boundaries are typed services. GitHub Actions is read-only: runs, jobs, and bounded logs become redacted Evidence. The patch engine accepts only evidence-scoped paths, builds a candidate workspace, runs allowlisted validation, and creates a DeliveryPlan. Delivery validates the exact approved plan before branch/commit/draft-PR work.

## Safety and consistency

Evidence identity includes source/provenance state. Approval binds SHA, diff, arguments, and repository. Path policy blocks traversal, secrets, Git internals, and system paths. GitHub OAuth uses signed, TTL-bounded, one-time hashed state challenges. Traces contain safe summaries, not chain-of-thought or raw secrets. Replay reads persisted state only.

## Failure and scaling considerations

Read-only CI failures degrade to partial investigations with limitations. Transient external failures use bounded retry policy; policy and validation failures are not retried. The Render blueprint uses the API, an allowlisted maintenance cron, and an indexer worker. In production, `INDEXING_MODE=queued` persists a tenant/session-ID-only job; the worker reloads the tenant-scoped session, clones/indexes it, and updates index status. Indexing therefore does not hold an API request open. The job queue deduplicates active reindex requests and retains only redacted bounded failure data.

## Production SaaS controls

The API applies security headers, a streamed request-body ceiling, and an application-level sliding-window rate limit before routes run. Development uses an in-process limiter; production requires `REDIS_URL` and sets `RATE_LIMIT_FAIL_CLOSED=true`, making the limit shared across API instances and preventing token-heavy requests from bypassing protection when the limiter is unavailable. A short tenant-scoped repository-metadata cache reduces repeated GitHub refresh traffic without caching OAuth credentials or skipping live repository authorization. An edge WAF/CDN should still absorb volumetric DDoS before traffic reaches Render.

Token-consuming repository-agent requests reserve a tenant-scoped monthly entitlement in MongoDB before a graph stream starts. The record is atomically incremented by `(userId, period, meter)` and has a unique index, so concurrent tabs cannot exceed a plan limit. This is a request budget, not a fabricated token estimate; model-token telemetry remains in traces for later pricing calibration.

Razorpay is the current payment-provider boundary. Checkout accepts only a server-recognized tier and maps it to a server environment plan ID. The secret and webhook secret never reach Vercel. Webhooks are HMAC-verified against exact raw bytes, de-duplicated, and matched to a server-created checkout record before they can alter a tenant subscription. The Vite route table is lazy so route-only IDE, graph, and rich Markdown dependencies do not inflate every initial visit. See `SAAS_SECURITY_AND_SCALE.md` for rollout and scaling requirements.
