# Production deployment

## Vercel frontend

Create a Vercel project with `client` as its root directory. Its build command is `npm run build` and output directory is `dist`.

Set these production environment variables:

- `VITE_BASE_API_URL=https://<render-api-domain>/api/`
- `VITE_CLERK_PUBLISHABLE_KEY=<Clerk publishable key>`

The checked-in `client/vercel.json` rewrites all routes to `index.html`, so React Router deep links such as `/new` and `/session/:slugid` resolve correctly.

## Render backend

Create a Render Blueprint from `render.yaml`. It provisions the public API, five role-isolated workers, and one maintenance cron job at the `0.5c-512mb` plan. All services use MongoDB Atlas as the shared durable store; Render filesystems are intentionally treated as ephemeral.

Provide every `sync: false` value during Blueprint setup. Set `FRONTEND_ORIGIN` to the exact Vercel production domain and `BASE_URL` to the exact Render API domain, both including `https://` and without a trailing slash.

Register this GitHub OAuth callback:

`https://<render-api-domain>/api/github/callback`

## Operating model

- The API remains the only public backend service.
- Workers accept only allowlisted internal job types from `background_jobs`; they never execute user-provided shell commands.
- GitHub writes, workflow dispatches, deployments, commits, pushes, and pull requests remain approval-gated.
- Start with the API and RAG worker if cost is a concern; scale out other workers only once their corresponding job volume exists.
# Deployment readiness audit

The repository contains a Vercel SPA configuration and a Render blueprint for the API, workers, and maintenance job. The production API binds to `$PORT` and provides `/health`; browser origin and OAuth callback values must be configured through `FRONTEND_ORIGIN` and `BASE_URL` rather than committed.

Before deployment, configure MongoDB, Clerk JWKS, GitHub OAuth client/callback URLs, and encrypted token/approval secrets using the host secret manager. Confirm that Vercel points `VITE_BASE_API_URL` at the deployed API, Render health checks succeed, and CORS has only the intended frontend origin. Docker sandbox availability is environment-specific; capability status must be interpreted as implemented/configured/available rather than assumed from deployed code.

Live GitHub Actions and live delivery checks are not exercised by this repository's deterministic suite.
