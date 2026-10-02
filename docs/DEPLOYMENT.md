# Production deployment

## Vercel frontend

Create a Vercel project with `client` as its root directory. Its build command is `npm run build` and output directory is `dist`.

Set these production environment variables:

- `VITE_BASE_API_URL=https://<render-api-domain>/api/`
- `VITE_CLERK_PUBLISHABLE_KEY=<Clerk publishable key>`

The checked-in `client/vercel.json` rewrites all routes to `index.html`, so React Router deep links such as `/new`, `/session/:slugid`, and `/session/:slugid/pipelines` resolve correctly.

## Render backend

Create a Render Blueprint from `render.yaml`. It provisions exactly two `0.5c-512mb` Render services: the public FastAPI API and a bounded six-hour maintenance cron. Agent runs, repository indexing, and read-only GitHub inspection are request-scoped today, so the blueprint does not pretend they are independently scalable workers. All durable state uses MongoDB Atlas; Render filesystems are intentionally ephemeral.

Provide every `sync: false` value during Blueprint setup. Set `FRONTEND_ORIGIN` to the exact Vercel production domain and `BASE_URL` to the exact Render API domain, both including `https://` and without a trailing slash.

Register this GitHub OAuth callback:

`https://<render-api-domain>/api/github/callback`

## Operating model

- The API remains the only public backend service.
- The maintenance cron accepts only allowlisted internal job types from `background_jobs`; it never executes user-provided shell commands.
- GitHub writes, workflow dispatches, deployments, commits, pushes, and pull requests remain approval-gated.
- Add a dedicated worker only when a real, durable long-running job type exists; document its ownership and resource budget first.

## Release checklist

1. In Vercel, set the project root to `client`, use `npm run build`, and set the two `VITE_*` variables for **Production**, Preview, and Development as appropriate. The public API URL must end in `/api/`.
2. In Render, create the Blueprint from this repository and enter every `sync: false` value in Render's secret manager. Do not place secrets in `render.yaml`, Vercel variables committed to Git, or client bundles.
3. Set `FRONTEND_ORIGIN` to the exact Vercel production origin and `BASE_URL` to the exact Render API origin. Register `<BASE_URL>/api/github/callback` with the GitHub OAuth application.
4. Allow Render's outbound addresses in the MongoDB Atlas network access rules, then wait for `GET <BASE_URL>/health` to report `database: connected`.
5. Verify a signed-in browser can call `/api/auth/me`, reconnect GitHub, list repositories, and create a read-only session before enabling delivery work.

## 512 MB service boundary

The Blueprint deliberately runs the public API and maintenance cron at Render's `0.5c-512mb` size. Repository data, evidence, sessions, approvals, and traces are durable in Atlas; a Render filesystem is never the source of truth.

Render's native Python service does not provide the Docker isolation required to run repository-controlled test commands safely. The Blueprint therefore sets `EXECUTION_MODE=disabled`: static checks and all evidence/approval controls remain available, but an active validator is denied rather than falling back to execution inside the API process. Deploy a dedicated isolated worker before enabling active repository-command validation in production.

After an approved draft-PR delivery, GitHub Actions remains the authoritative CI result. Base64 displays the reported run state; it does not claim that tests passed until GitHub reports a completed successful run.
# Deployment readiness audit

The repository contains a Vercel SPA configuration and a Render blueprint for the API and maintenance job. The production API binds to `$PORT` and provides `/health`; browser origin and OAuth callback values must be configured through `FRONTEND_ORIGIN` and `BASE_URL` rather than committed.

Before deployment, configure MongoDB, Clerk JWKS, GitHub OAuth client/callback URLs, and encrypted token/approval secrets using the host secret manager. Confirm that Vercel points `VITE_BASE_API_URL` at the deployed API, Render health checks succeed, and CORS has only the intended frontend origin. Docker sandbox availability is environment-specific; capability status must be interpreted as implemented/configured/available rather than assumed from deployed code.

Live GitHub Actions and live delivery checks are not exercised by this repository's deterministic suite.
