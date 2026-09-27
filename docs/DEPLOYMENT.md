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
# Deployment readiness audit

The repository contains a Vercel SPA configuration and a Render blueprint for the API and maintenance job. The production API binds to `$PORT` and provides `/health`; browser origin and OAuth callback values must be configured through `FRONTEND_ORIGIN` and `BASE_URL` rather than committed.

Before deployment, configure MongoDB, Clerk JWKS, GitHub OAuth client/callback URLs, and encrypted token/approval secrets using the host secret manager. Confirm that Vercel points `VITE_BASE_API_URL` at the deployed API, Render health checks succeed, and CORS has only the intended frontend origin. Docker sandbox availability is environment-specific; capability status must be interpreted as implemented/configured/available rather than assumed from deployed code.

Live GitHub Actions and live delivery checks are not exercised by this repository's deterministic suite.
