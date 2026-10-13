# Production deployment

## Vercel frontend

Create a Vercel project with `client` as its root directory. Its build command is `npm run build` and output directory is `dist`.

Set these production environment variables:

- `VITE_BASE_API_URL=https://<render-api-domain>/api/`
- `VITE_CLERK_PUBLISHABLE_KEY=<Clerk publishable key>`

The checked-in `client/vercel.json` rewrites all routes to `index.html`, so React Router deep links such as `/new`, `/session/:slugid`, `/session/:slugid/pipelines`, and the historical run inspector `/dev/runs/:runId` resolve correctly after a refresh. These pages still require the normal signed-in Clerk session; the rewrite only ensures that Vercel serves the SPA entry point rather than returning a platform 404.

## Render backend

### Render service count

- `render.free.yaml` is the hobby/demo profile: one Free `base64-ops-api-free` web service and one Free private Key Value instance. It is the only checked-in profile with `plan: free` for the API.
- `render.mvp.yaml` is the default low-cost paid profile: one `base64-ops-api` web service and one private Key Value instance, both managed by Render.
- `render.yaml` is the production-intelligence profile: **4** Render services, the API, maintenance cron, indexer, and read-only CI observer. Every role uses the `0.5c-512mb` tier, for a fixed **2 vCPU** baseline across four single-instance consumers.
- MongoDB Atlas remains an external managed dependency. Each checked-in Render profile provisions its own private Key Value service for rate limiting, so it is included in that profile's service count.

### Free-tier evaluation profile

Use `render.free.yaml` only for a personal demo or evaluation. It intentionally
uses distinct service names, so syncing it cannot silently change the paid
profiles. It has no always-on worker: repository indexing is bounded and inline,
and CI observation is read-only when a user opens the relevant UI. Do not set
`GITHUB_WEBHOOK_SECRET` for this profile unless a separately reviewed durable
dispatcher is operating; otherwise accepted webhook events would remain queued
without an eligible worker.

Render currently supports the `free` plan for web services and Key Value, but
free web services can be suspended and Free Key Value is in-memory only. Rate
limit state can therefore be lost on a Key Value restart; durable sessions,
evidence, approvals, and incident records remain in Atlas. Treat this profile
as a low-traffic evaluation configuration, not a production availability claim.

For the 100–500-user MVP, create the Render Blueprint from `render.mvp.yaml`. It provisions one `0.5c-512mb` FastAPI API role, performs bounded repository indexing inline, and lets the Code commits rail read the delivered branch's GitHub Actions status. This keeps the flagship Safe Repair workflow intact without paying for idle indexer and CI-observer workers.

Use `render.yaml` only after measured queue latency or post-delivery CI follow-up warrants the four-role setup: API, bounded maintenance cron, indexer, and read-only CI observer. The profile intentionally uses one instance per role on the 512 MB tier; do not add autoscaling or larger plans without revising the budget after a load test. It keeps clone/index and autonomous CI follow-up off the API request path.

Both blueprints keep `CI_AUTO_REFRESH_SECONDS=0`: normal browser session polling reads persisted state only. In the MVP profile, the Code rail performs its bounded read-only Actions lookup when open. In the scaled profile, the read-only delivery observer additionally performs bounded follow-up for delivered commits.

The indexer and CI observer reference the API service's `MONGO_URI` and `GITHUB_TOKEN_ENCRYPTION_KEY` through Render's service-to-service environment-variable references. Do not replace these with independently generated worker values: all replicas that read encrypted GitHub tokens must use the same encryption key. The workers intentionally do not receive the GitHub OAuth client secret, Razorpay secrets, Clerk configuration, or public API traffic settings.

Provide every `sync: false` value during Blueprint setup. Set `FRONTEND_ORIGIN` to the exact Vercel production domain and `BASE_URL` to the exact Render API domain, both including `https://` and without a trailing slash. Set `CLERK_ISSUER` to the Clerk Frontend API URL and `CLERK_AUTHORIZED_PARTIES` to the exact browser origins allowed to use production API tokens (including any intentionally supported preview origins). In every production profile, keep `TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP`; startup intentionally refuses production traffic without a single trusted edge-supplied identity header.

Register this GitHub OAuth callback:

`https://<render-api-domain>/api/github/callback`

### Optional GitHub App candidate verification

Candidate verification is disabled unless
`GITHUB_CANDIDATE_VERIFICATION_ENABLED=true`. When enabled, set
`GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` as server-only Render secrets on
the API and `ci_observer` services. Do not set them in Vercel. The App needs
only Contents read/write and Actions read/write on explicitly installed
repositories. A repository owner must still bind an existing reviewed workflow
inside Incident Inbox and approve each candidate dispatch separately. See
[CANDIDATE_VERIFICATION.md](CANDIDATE_VERIFICATION.md); live App verification
is not included in the deterministic deployment checks.

Optional GitHub-native incident Check reporting additionally requires
`GITHUB_INCIDENT_REPORTING_ENABLED=true`, the App's **Checks: write**
permission, and an explicit per-repository Inbox preference. It publishes a
neutral redacted investigation Check only; do not enable it as a substitute for
candidate verification or delivery approval.

## Operating model

### Manually created Render services

A service created manually in the Render dashboard does not automatically read
this repository's Blueprint variables. Configure the same required environment
values in that service's dashboard. The startup failure
`TRUSTED_CLIENT_IP_HEADER must be configured in production` is resolved by
adding `TRUSTED_CLIENT_IP_HEADER=CF-Connecting-IP`, then redeploying. This is a
deliberate rate-limit safety check; do not replace it with `X-Forwarded-For` or
disable it.

- The API remains the only public backend service.
- The maintenance cron accepts only allowlisted internal job types from `background_jobs`; it never executes user-provided shell commands.
- GitHub writes, workflow dispatches, deployments, commits, pushes, and pull requests remain approval-gated.
- Add a dedicated worker only when a real, durable long-running job type exists; document its ownership and resource budget first.

## Release checklist

1. In Vercel, set the project root to `client`, use `npm run build`, and set the two `VITE_*` variables for **Production**, Preview, and Development as appropriate. The public API URL must end in `/api/`. The repository CI then verifies the production bundle budget and fails on high-or-worse npm dependency advisories.
2. In Render, create the Blueprint from this repository and enter every `sync: false` value in Render's secret manager. Do not place secrets in `render.yaml`, Vercel variables committed to Git, or client bundles.
3. Set `FRONTEND_ORIGIN` to the exact Vercel production origin and `BASE_URL` to the exact Render API origin. Register `<BASE_URL>/api/github/callback` with the GitHub OAuth application.
4. Allow Render's outbound addresses in the MongoDB Atlas network access rules, then wait for `GET <BASE_URL>/ready` to return `200` and `status: ready`.
5. Verify a signed-in browser can call `/api/auth/me`, reconnect GitHub, list repositories, and create a read-only session before enabling delivery work.
6. Before making a scale claim, run the non-billable staging load harness in [`../load/k6/README.md`](../load/k6/README.md). Do not send load-test traffic to model, approval, delivery, billing, or customer-data paths.

## Execution and durability boundary

Both deployment profiles run every Render role on the `0.5c-512mb` tier. In both cases, repository data, evidence, sessions, approvals, and traces are durable in Atlas; a Render filesystem is never the source of truth.

Render's native Python service does not provide the Docker isolation required to run repository-controlled test commands safely. The Blueprint therefore sets `EXECUTION_MODE=disabled`: static checks and all evidence/approval controls remain available, but an active validator is denied rather than falling back to execution inside the API process. Deploy a dedicated isolated worker before enabling active repository-command validation in production.

After an approved draft-PR delivery, GitHub Actions remains the authoritative CI result. Base64 displays the reported run state; it does not claim that tests passed until GitHub reports a completed successful run.
# Deployment readiness audit

The repository contains a Vercel SPA configuration and a Render blueprint for the API and internal workers. The production API binds to `$PORT`; `/health` is liveness only, while `/ready` is the strict traffic-readiness gate. Browser origin and OAuth callback values must be configured through `FRONTEND_ORIGIN` and `BASE_URL` rather than committed.

Before deployment, configure MongoDB, Clerk JWKS, GitHub OAuth client/callback URLs, and encrypted token/approval secrets using the host secret manager. Confirm that Vercel points `VITE_BASE_API_URL` at the deployed API, Render health checks succeed, and CORS has only the intended frontend origin. Docker sandbox availability is environment-specific; capability status must be interpreted as implemented/configured/available rather than assumed from deployed code.

Live GitHub Actions and live delivery checks are not exercised by this repository's deterministic suite.
