# Production readiness

Base64 Ops is deployable only when its hosting configuration preserves the
evidence, tenant, approval, and execution boundaries enforced in code.

## Deployment profiles

- **Compact MVP:** `render.mvp.yaml` provides one `0.5c-512mb` API service.
  Indexing is inline and active repository-code validation is disabled.
- **Production intelligence:** `render.yaml` provisions four fixed roles—the
  public API, maintenance cron, indexer, and read-only CI observer—each on
  Render's `0.5c-512mb` tier. The profile therefore starts at 2 vCPUs across
  four single-instance, one-at-a-time roles; it is a bounded baseline, not a
  capacity claim.

Both profiles use MongoDB Atlas and Redis as managed external dependencies.
They do not make Render's local filesystem authoritative.

## Non-negotiable production settings

Set these through the Render secret manager, never in Git or Vercel:

- `MONGO_URI`, `REDIS_URL`
- `CLERK_JWKS_URL`, `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES`
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`
- a generated `GITHUB_OAUTH_STATE_SECRET` and an operator-generated
  `GITHUB_TOKEN_ENCRYPTION_KEY`
- `OPENAI_API_KEY` and any billing-provider secrets

Set `ENVIRONMENT=production`, an HTTPS `BASE_URL`, an HTTPS
`FRONTEND_ORIGIN`, and exact `TRUSTED_HOSTS`. Production startup rejects
missing safety-critical API settings. Keep `EXECUTION_MODE=disabled` unless a
separate, authorized isolated execution provider has actually been deployed.

`GITHUB_TOKEN_ENCRYPTION_KEY` must be a URL-safe base64-encoded 32-byte Fernet
key. Generate it with `Fernet.generate_key()` and enter it as a Render secret;
do not rely on a generic host-generated password for a strict cryptographic
format. New deployments should leave `GITHUB_TOKEN_ENCRYPTION_LEGACY_KEY`
empty. To rotate a deployment that
used the historical passphrase-derived key, set a newly generated Fernet key as
the primary value and place the old 32-byte-or-longer value in the legacy
variable temporarily. It is decrypt-only, so reconnecting GitHub rewrites the
token using the primary key; remove the legacy value once migration completes.

## Release acceptance

Before enabling customer traffic:

1. Validate the intended Blueprint in the target Render workspace.
2. Confirm `GET <BASE_URL>/ready` returns `200` with `status: ready`.
3. Deploy the Vercel SPA with `VITE_BASE_API_URL` ending in `/api/` and a
   Clerk publishable key.
4. Verify a signed-in Vercel origin can make an authenticated API request.
5. Verify GitHub OAuth callback and repository listing with a non-production
   test account.
6. Verify rejection leaves an approval/delivery plan unexecuted. Do not use a
   proposal or a draft PR as proof of merge, CI success, or production health.

See [the detailed audit](PRODUCTION_READINESS_AUDIT.md) and
[deployment guide](DEPLOYMENT.md) for current verification scope and the
remaining manual gates.

## Latest verification and status

On 2026-10-12, a clean Python 3.13 environment passed 314 backend tests and
Ruff; deterministic evaluations passed 28/28 overall and 13/13 CI cases. A
separate clean Node 22 install from `client/package-lock.json` passed the
auth-return check, TypeScript production build, bundle budget, and the
high-severity npm audit gate. The original local `node_modules` tree was not
used for that proof because an active Vite process had locked a native module
after an interrupted install.

**CONDITIONALLY READY.** The source, deterministic tests, CI runtime, and
checked-in deployment profile are aligned, but live Render/Vercel/Atlas/Redis/
Clerk/GitHub/OAuth validation still require an authorized deployment.
Browser Use did attempt the local demo route but its isolated runtime could not
reach the developer machine's loopback server; direct local readiness checks
were healthy. Repository-code execution remains intentionally disabled until
an independently isolated provider is configured.
