# Safe staging load tests

These [k6](https://grafana.com/docs/k6/latest/) scripts are a capacity-validation harness, not a synthetic claim that Base64 Ops supports 10,000 users. They intentionally exercise only inexpensive read paths:

- `public-health.js` checks CDN/API ingress via `GET /health`.
- `authenticated-session-read.js` checks Clerk authentication, tenant isolation, rate limiting, and the Mongo read path with a disposable staging user.

They never call chat, product-guide inference, indexing, CI refresh, proposal generation, approval, delivery, or billing endpoints. A load test must not create model spend, mutate a repository, consume customer entitlement, or use production customer data.

## Prerequisites

1. Use a separate staging Render service, Atlas database, Redis instance, Clerk development/staging instance, and Vercel preview/isolated domain.
2. Set `BASE_URL` to the HTTPS staging API origin, without `/api/`.
3. For authenticated reads only, provision a short-lived `LOAD_BEARER_TOKEN` for a disposable Clerk test user. Do not put the token in a command history, repository, CI log, or k6 output artifact.
4. Confirm staging rate limits and Atlas connection limits are sized for the planned test. Do not weaken production protection for a load test.

## Run progressively

Install k6 outside this repository and begin with a small ramp:

```powershell
$env:BASE_URL = "https://api-staging.example.com"
k6 run load/k6/public-health.js
```

For the authenticated read test:

```powershell
$env:BASE_URL = "https://api-staging.example.com"
$env:LOAD_BEARER_TOKEN = "staging-only-short-lived-token"
k6 run load/k6/authenticated-session-read.js
```

Increase `RAMP_RPS`, `SUSTAIN_RPS`, `MAX_VUS`, and durations one step at a time. Record each run's source revision, Render replica count, Atlas tier/connection count, Redis health, p50/p95/p99 latency, 429/5xx rates, CPU/memory, queue age, and client-side k6 saturation.

## 10,000-user evidence gate

Do not interpret 10,000 registered accounts as 10,000 simultaneous requests. Establish a written target for concurrent active users and read/write mix, then demonstrate it in stages:

1. Public ingress baseline: no unexpected 5xxs at the agreed read-only arrival rate.
2. Authenticated read baseline: p95 and error-rate thresholds hold while Redis and Mongo stay within capacity.
3. Queue isolation: separately test indexer/CI worker backlog recovery with synthetic server-created jobs, never customer repositories.
4. Cost safety: verify the tests never invoke model or payment routes and inspect provider usage afterwards.
5. Resilience: repeat a bounded run while replacing one API replica and while a worker drains. `/ready` must prevent traffic from reaching a replica that loses Mongo or required Redis protection.

Only call a capacity target verified after these staging results, alerting, and a deployment rollback drill are retained. The default thresholds in the scripts are starting SLO hypotheses, not proven production guarantees.
