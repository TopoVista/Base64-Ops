# SaaS security, cost, and scale plan

## What is implemented now

- Clerk authenticates API users; production verifies the configured Clerk JWT issuer and, when present, restricts the `azp` claim to configured browser origins. Oversized bearer headers are rejected before JWT/JWKS work, and a token must contain a bounded string subject before it becomes a tenant key. A partial unique index makes each Clerk identity map to one internal tenant record even when separate API replicas receive concurrent first-login requests. All persisted sessions, evidence, approvals, memory, traces, and billing records are tenant scoped.
- Invalid Clerk tokens return a generic 401. JWKS/configuration outages return a retryable generic 503, without exposing verifier, provider, or token details.
- A sliding-window request limiter runs before routes. It uses Redis when `REDIS_URL` is configured; Render production is configured to fail closed when the shared limiter is unavailable. The in-memory limiter is for local development only. Production selects an edge-overwritten single-IP header (`CF-Connecting-IP` on Render) for identities and never parses caller-controlled `X-Forwarded-For` lists. Chat, repository indexing, CI refresh, proposal creation, approval execution, PR delivery, and pasted-log proposal routes share the stricter expensive-work budget rather than the ordinary API budget.
- API write requests have a declared and streamed-body ceiling, chat prompts are bounded across both supported message shapes, session search treats user text as escaped literals rather than executable Mongo regular expressions, and cross-origin API calls omit ambient browser credentials because Base64 uses explicit Clerk Bearer tokens.
- The expensive agent stream reserves one server-owned monthly entitlement atomically before retrieval or LLM work begins. Browser input cannot choose a tier, quota, provider plan, or price.
- Each browser action that can invoke the model carries a tenant-scoped idempotency identifier, including repository chat, model-backed product-guide answers, and reviewed CI proposals. The backend stores only its hash for a short retention period, so reconnects and retries cannot reserve a second entitlement or invoke the agent twice.
- The same tenant lease and entitlement guard applies when the CI workbench asks the graph to generate a proposal. The product guide serves documented questions deterministically; only an unknown guide question that actually needs a model consumes an entitlement, with bounded history and output.
- One active agent graph run is permitted per tenant. The lease is Redis-backed in production and is released when the stream ends (or expires after a bounded TTL), preventing concurrent tabs from multiplying LLM spend.
- Razorpay subscriptions are server-side. A client can request only `pro` or `team`; environment-owned plan IDs determine price and currency. Razorpay keys and webhook secrets remain backend-only. Hosted checkout redirects are accepted only over HTTPS to Razorpay-owned domains.
- Razorpay webhooks are verified over their exact raw body using HMAC, deduplicated for 90 days, ordered atomically by the provider event timestamp when supplied, and linked to a checkout record created by this API before subscription state changes. A provider payload or its `notes` cannot grant access by itself.
- A user can have only one unfinished/active checkout record at once, so payment-page retries cannot create duplicate recurring subscriptions. Provider subscription IDs are bound to the user’s current plan; a delayed cancellation from an older subscription cannot downgrade a newer active plan.
- Existing evidence redaction, untrusted-content boundaries, tenant checks, approval binding, path policy, and sandbox policy remain authoritative. Product-guide user input and history are redacted before persistence or model context as well; guide history cannot modify policy or request credentials.
- New sessions resolve the selected repository through the connected GitHub account before any clone. The workspace independently permits token-bearing clones only to canonical `https://github.com/<owner>/<repo>.git` URLs, preventing browser-supplied URLs from becoming token exfiltration or SSRF targets.
- GitHub OAuth completion accepts a return location only within the configured frontend origin; external post-OAuth redirects are rejected. Each connection state is signed, short-lived, stored only by hash, and atomically consumed before token exchange, so stale or replayed callbacks cannot link an account.
- GitHub Actions log downloads handle signed redirects without forwarding the GitHub OAuth bearer token to the redirected storage host, and accept only GitHub Actions download hosts. Repository-list metadata is cached per tenant for a bounded TTL; it never contains OAuth tokens, and selecting a repository still performs a live authorization check.

## Required production configuration

Set these as Render secrets, never `VITE_*` frontend values:

```text
REDIS_URL
TRUSTED_HOSTS=api.example.com
RATE_LIMIT_FAIL_CLOSED=true
TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP
RAZORPAY_KEY_ID
RAZORPAY_KEY_SECRET
RAZORPAY_WEBHOOK_SECRET
RAZORPAY_PRO_PLAN_ID
RAZORPAY_TEAM_PLAN_ID
```

Production startup rejects missing HTTPS origin settings, Mongo/Clerk configuration, GitHub OAuth credentials, shared Redis, fail-closed traffic protection, or the built-in development OAuth state/token-encryption defaults. This turns an insecure deployment mistake into an explicit failed deploy rather than a running API.

`/health` is a liveness endpoint and reports a degraded database dependency without exposing connection details. `/ready` is a strict readiness endpoint: it returns 503 while MongoDB or the required fail-closed Redis rate limiter is unavailable, so Render does not route customer traffic to a replica that cannot enforce tenant, quota, approval, and traffic-protection invariants. The Render Blueprint uses `/ready` for service health checks.

Create the Pro and Team recurring plans in Razorpay first. Configure a HTTPS webhook at:

```text
https://api.example.com/api/billing/webhooks/razorpay
```

Subscribe to active/resumed, paused, cancelled/completed, and payment-failed subscription events. Use a long unique webhook secret. Perform Razorpay live-mode verification only with a controlled test customer and test plan; never paste provider secrets into chat, source control, or Vercel.

Razorpay is selected because it supports recurring subscriptions and Indian payment methods, while its SaaS offering advertises international payments; rollout availability and regulatory eligibility must still be confirmed with the merchant account. [Razorpay subscription plans](https://razorpay.com/docs/api/payments/subscriptions/create-plan/), [Razorpay SaaS payments](https://razorpay.com/solutions/saas/), and [Razorpay webhook security checklist](https://razorpay.com/security/checklist/) are the authoritative integration references.

The Vercel frontend has an explicit CSP, clickjacking/referrer/permissions protections, HSTS, and immutable caching for hash-named `/assets/` files. Its CSP permits Clerk, Google-hosted fonts, secure API connections, and Monaco workers. `connect-src` intentionally allows HTTPS/WSS while environments use a configurable `VITE_BASE_API_URL`; tighten it to the exact deployed API and Clerk origins when domains are finalized, and verify the policy in a deployed browser before adding stricter directives.

## MVP and scale deployment profiles

The default deployment target is 100–500 users, not a speculative 10,000-user fleet. `render.mvp.yaml` keeps the durable design—Atlas, Redis-backed limits, Vercel, evidence, approval binding, and strict quotas—but operates with one 512 MB API service. It uses bounded inline indexing and `CI_VERIFICATION_MODE=on_read`, so the Code commits rail observes Actions without a permanently running worker. That trade-off is intentional: an index or CI status may take longer, but it avoids paying for idle capacity before usage proves it necessary.

`render.yaml` remains the measured-growth profile. Switch only when queue age, API latency, or delivery-CI observation data proves the one-service profile insufficient; it separates index and CI observer workers without changing the evidence, policy, approval, or tenant-isolation architecture.

## Measured growth architecture

```text
Vercel CDN/WAF
  -> static React application
  -> API gateway / edge rate limits
       -> Render FastAPI API role (fixed `0.5c-512mb` production profile)
            -> Redis: limits, short-lived locks, cache
            -> MongoDB Atlas: tenant data, evidence, approvals, metering
            -> queue: bounded long-running indexing / CI follow-up work
                 -> fixed indexer and read-only CI observer roles
            -> GitHub / Razorpay / model APIs
```

Repository indexing is moved behind a tenant-scoped Mongo job and a dedicated `base64-indexer` Render worker. In queued mode, even a first repository chat enqueues indexing rather than building an index in the API replica; it uses bounded direct repository evidence while the worker prepares the reusable corpus. The job carries only a session ID, has a database-enforced single active dedupe key, reloads repository details from the tenant session before clone/index work, and has a renewable fencing-token lease so a stale worker cannot renew or mark a reclaimed job complete. Clone work and bounded file/chunk construction run off the worker event loop, preserving lease heartbeats. The corpus is explicitly capped by `RAG_INDEX_MAX_FILES`, `RAG_INDEX_MAX_TOTAL_BYTES`, and `RAG_INDEX_MAX_CHUNKS`; a retained subset is marked partial in session state instead of being presented as complete coverage. Sessions also have a unique tenant/slug constraint, so concurrent API replicas cannot create two workspaces for one session. Browser session hydration never starts GitHub Actions discovery by default (`CI_AUTO_REFRESH_SECONDS=0`), because browser polling must stay database-local at scale; users can explicitly refresh CI and the delivery observer continues its bounded follow-up. If Atlas recovers after a degraded API startup, the API recreates its quota, idempotency, billing, job, and metadata-cache indexes before reporting the database as recovered. If any required index cannot be established, the API closes the database connection and degrades rather than serving correctness-sensitive paths without their invariants. Post-delivery CI observation runs in a separate read-only `base64-ci-observer` worker: it only reports runs whose head SHA equals the delivered commit, never reruns or changes GitHub Actions. Validation remains API-path work today and must move to its own isolated job before high-concurrency mutation workflows are enabled.

Render workers reference the API's persisted Mongo URI and GitHub token-encryption key rather than independently generating their own values. That preserves decryption compatibility without duplicating the OAuth client secret into workers. Role-specific production validation rejects missing worker dependencies, while the indexer and CI observer retain only the credentials needed to process server-created jobs. On SIGTERM, a worker stops claiming new jobs and drains its active claim when possible; its 15-minute renewable fencing lease bounds recovery if the platform force-stops it.

The compact Blueprint keeps one 512 MB API instance for low-cost MVP use. The production-intelligence Blueprint keeps the same tier across four fixed roles: API, indexer, read-only CI observer, and maintenance cron. This is a 2-vCPU, single-instance baseline rather than proof that the product can absorb arbitrary concurrent clone or model workloads. Mongo fencing leases and active dedupe keys keep individual background jobs safe across future worker scaling, but scaling is intentionally not configured until a load test justifies a revised capacity budget. The API Mongo pool is capped at 12 connections per replica (workers use 3), with a 5-second queue timeout. Size the Atlas connection ceiling against actual replica counts before changing these values.

## Cost and accuracy rules

1. Answer documented product-guide questions deterministically before calling a model.
2. Use repository map + indexed lexical retrieval first; admit only bounded, ranked evidence into model context. Repository chunks carry compact server-generated search terms, so a query does not become a first-1,000-chunks scan as a repository grows.
3. Cache immutable GitHub metadata by repository/run/SHA with short TTLs. Repository-list metadata is currently cached by tenant for 60 seconds and invalidated on connect/disconnect. Never cache user OAuth tokens in response payloads.
4. Reserve an agent-run entitlement before inference, then aggregate provider-reported prompt, completion, and total tokens from repository runs and model-backed guide responses in a tenant-period usage ledger. A configured tier token ceiling blocks the next model run after observed usage reaches it; the current run remains bounded by the context/output limits and tenant lease. Price plans only after collecting real distribution data; never invent a token cost.
5. Use a smaller model for deterministic-first product support and other bounded low-risk work; reserve the stronger balanced model for evidence-backed diagnosis and structured patch proposals. The Render Blueprint uses `gpt-5.6-terra` for repository investigations and `gpt-5.6-luna` for unknown product-guide questions.
6. Cap index files, aggregate index bytes, index chunks, evidence count, file bytes, CI log bytes, output bytes, graph recursion, and retries. When an index cap is reached, persist and display a partial-corpus state rather than silently treating retrieval as complete. A cost limit must fail explicitly, not silently degrade into unbounded context.
7. Keep final diagnoses structured and cite evidence IDs. A confident answer without evidence is an error condition, not an acceptable product trade-off.

The browser lazy-loads all route pages, plus the Monaco workbench, dependency graph renderer, and rich diagram tooling. Normal chat uses lightweight Markdown because a repository-grade editor is available on demand; this avoids making every sign-in, billing, demo, support, or investigation session download language grammars and visualization runtimes. `npm run verify:bundle` enforces a 750 KB maximum JavaScript chunk and a 5.5 MB maximum total JavaScript budget after the production build; CI also fails on high-or-worse npm advisories. These are regression tripwires, not a substitute for real-user performance monitoring.

The repository-answer path enforces `RAG_RETRIEVAL_LIMIT`, `LLM_MAX_CONTEXT_CHARS`, `LLM_MAX_SOURCE_CHARS`, `LLM_MAX_OUTPUT_TOKENS`, `LLM_TIMEOUT_SECONDS`, and `LLM_MAX_RETRIES`. Patch generation independently bounds evidence-selected candidate files with `LLM_MAX_PATCH_CANDIDATE_CHARS` and uses the same output ceiling. A run trace records selected/discarded chunks, estimated input tokens, and provider-reported prompt/completion/total tokens when available. Estimates are diagnostic only; pricing must use provider-reported usage after a representative production observation period.

## Security invariants

- CI logs, repositories, workflow YAML, browser editor text, and operational memory are untrusted data—not instructions.
- Payment webhooks grant nothing without a valid HMAC and matching server-created checkout.
- Billing state can increase an entitlement; it cannot bypass approval, tenant checks, path policy, validation, or delivery controls.
- DDoS mitigation is layered: CDN/WAF first, shared Redis rate limit second, tenant quota third, and bounded work queues fourth.
- Do not log raw Authorization headers, GitHub/Razorpay secrets, payment payloads, private repository contents, or hidden model reasoning. Central redaction additionally covers OpenAI-style keys, JWTs, AWS access keys, Slack tokens, and Mongo/Postgres/MySQL/Redis/AMQP connection strings before evidence, traces, UI payloads, or worker errors persist.

## Before public launch

- Enable Vercel firewall/WAF and a Render/Cloudflare edge policy with country/IP abuse rules appropriate to customers.
- Use a managed Redis with eviction/availability alarms, Atlas IP controls, least-privilege database users, backups, and restore drills.
- Run the safe staged ingress/read harness in [`../load/k6/README.md`](../load/k6/README.md) before making a capacity claim. It deliberately excludes model, indexing, approval, delivery, and payment routes; retain results for each tested release and treat its initial thresholds as hypotheses, not guarantees.
- Load-test the indexer queue and add separate jobs/workers for validation and CI follow-up before allowing high concurrent mutation workflows.
- Configure alerting for 429s, 5xxs, queue age, webhook verification failures, quota denials, provider latency, and unexpected token usage.
- Run penetration testing for auth/JWT validation, OAuth callback/state, IDOR/tenant isolation, webhook replay, SSRF, path traversal, prompt injection, and upload/parser surfaces.
- Set a data-retention schedule for evidence, raw provider event markers, traces, and deleted accounts.
