# Production readiness audit

Audit date: 2026-10-11

## Scope and method

This audit reviewed the checked-out application, not a deployed Render or
Vercel environment. It covered the Render Blueprints, Vercel SPA config,
runtime configuration validation, API readiness, cross-origin controls,
background-worker boundaries, execution safety, CI configuration, and the
automated verification available in this repository.

The audit does **not** claim that a cloud deployment, Atlas connection, Clerk
tenant, GitHub OAuth application, or live GitHub Actions integration was
exercised. Those operations require configured credentials and an explicitly
authorized environment. They are isolated release gates; they did not prevent
the local implementation and deterministic verification recorded below.

## Issue ledger

| ID | Severity | Affected files | Reproduction and impact | Correction and regression proof |
| --- | --- | --- | --- | --- |
| PR-001 | P1 | CI workflows, `backend/.python-version`, `backend/Dockerfile`, `backend/pyproject.toml` | Production selected Python 3.13.5 while CI used 3.11 or floated within 3.13, leaving the selected runtime untested and able to drift. | CI, local tooling, Docker, and the isolated workspace image now specify 3.13.5; the available local Python 3.13.1 compatibility run passed 312 tests and `ruff check .` passed. |
| PR-002 | P1 | `backend/requirements.txt`, `backend/tests/test_runtime_requirements.py` | A clean backend install could not import `jwt`, preventing protected routes from loading because the application imports PyJWT but only declared python-jose. | Added pinned `PyJWT[crypto]==2.15.1` and a dependency regression check; the clean Python 3.13 suite passed. |
| PR-003 | P1 | `backend/app/core/config.py`, `backend/app/core/security.py`, OAuth/config tests | Production accepted arbitrary non-default strings as the GitHub token encryption key. A mistyped value could silently change the encryption derivation. | Production now requires a valid 32-byte Fernet key and 32-byte OAuth-state secret, encrypts natively with Fernet, and offers a decrypt-only legacy-key migration path. 38 focused security/deployment tests and the full suite passed. |
| PR-004 | P2 | local `client/node_modules` only | An interrupted local `npm ci`, combined with a running Vite process locking a native module, left a partial transitive package and made the local build fail. This was not a lockfile or source failure: a fresh lockfile install built successfully. | Verified in an isolated temporary copy with `npm ci`, auth-route verification, production build, bundle budget, and high-severity audit. The active user Vite process was not stopped or modified. |
| PR-005 | P2 | `client/src/routes/route.ts` | The public `/sign-in` URL returned the SPA 404 view even though the root sign-in view worked. This breaks direct links and post-auth return targets that name the conventional sign-in path. | Added the `/sign-in` alias to the sign-in route. Local Playwright Chromium checks confirmed the sign-in view renders at both `/` and `/sign-in` without console or page errors. |
| PR-006 | P2 | `client/src/pages/auth/sign-in.tsx`, `client/src/pages/auth/sign-up.tsx` | At a 375-pixel viewport, both authentication screens had an 8-pixel horizontal overflow caused by a grid child retaining its min-content width. | Added `min-w-0` to the grid child. Local Playwright checks measured `scrollWidth === clientWidth === 375` for `/`, `/sign-in`, and `/sign-up`. |
| PR-007 | P1 | `backend/app/services/session_service.py`, `backend/app/api/routes/session.py`, `client/src/lib/api.ts`, `client/src/types/session.type.ts`, `backend/tests/test_delivery_policy.py` | `POST /session/{slug}/pr` used a legacy approval record (`action=create_pull_request`, `status=approve`) and could push/create a PR without validating the exact delivery-plan hash, base SHA, branch, or canonical arguments. | Deprecated and fail-closed the legacy route with HTTP 409; removed its unused frontend wrapper. The new offline regression test supplies a legacy approved record and asserts the route returns 409 before workspace preparation or branch push. Focused tests: 9 passed. |
| PR-008 | P2 | `backend/app/execution/docker_runtime.py`, `backend/tests/test_execution_sandbox.py` | Direct construction of `DockerSandboxRuntime` defaulted to Python 3.11 even though the API runtime, CI, Dockerfile, and configured workspace image use Python 3.13.5. A direct caller could therefore validate a proposal under a different interpreter than production. | Aligned the direct sandbox default to `python:3.13.5-slim` and added a regression test. The runtime manager still passes its configured image explicitly. |
| PR-009 | P1 | `render.yaml`, Blueprint tests, deployment/scale documentation | The checked-in intelligence profile assigned the API and workers 2–8 GB plans, which violated the requested initial topology of 512 MB Render services and overstated capacity before a measured load test. | Kept the four real, least-privileged roles but placed every one on `0.5c-512mb`; reduced API/worker Mongo connection pools and index corpus ceilings accordingly. Blueprint regression tests enforce that no production-profile role exceeds the 512 MB tier. |
| PR-010 | P1 | `render.yaml`, `render.mvp.yaml`, environment example, Blueprint tests | The Blueprints asked the host to generate `GITHUB_TOKEN_ENCRYPTION_KEY`, but generic secret generation is not a guarantee of the strict Fernet key format required at process startup. | Both Blueprints now require an explicit secret-manager value for the encryption key, docs provide the non-secret generation command, and tests require `sync: false`. OAuth-state secret generation remains safe because it has a length—not Fernet-format—contract. |

## Verified controls

| Area | Observed behavior | Status |
| --- | --- | --- |
| Render readiness | `/health` remains diagnostic; `/ready` refuses traffic until MongoDB and the required rate-limit store are available. | Implemented |
| CORS and host boundary | Production accepts the configured HTTPS frontend origin, disables credentialed CORS, and requires `TRUSTED_HOSTS`. | Implemented |
| Production secret boundary | Render uses `sync: false` or generated values for secrets; the frontend consumes only public `VITE_*` variables. | Implemented |
| Execution safety | Render config sets `EXECUTION_MODE=disabled`. Active repository-code validation now fails closed whenever an isolated Docker provider is unavailable; it cannot silently fall back to the API/worker host. | Implemented |
| Worker separation | Indexing/graph work and post-delivery Actions observation are assigned to distinct, least-privileged worker roles. | Implemented |
| CI gate | Python/Ruff, deterministic evaluations, frontend type/build, auth-return verification, bundle budget, and dependency audit are defined in GitHub Actions. | Implemented |
| SPA routing | Vercel rewrites deep links to the Vite SPA entrypoint and applies browser security headers. | Implemented |
| Local browser flows | Playwright Chromium exercised public routes, unauthenticated protected-route redirects, the demo workflow, keyboard tour behavior, and mobile auth layout. | Passed locally |
| Delivery approval integrity | New delivery approvals bind exact arguments/diff/base SHA/branch; the unbound legacy delivery route is disabled rather than allowing a stale approval to mutate a repository. | Implemented and regression-tested |

## Current local verification results

| Command | Measured result |
| --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest -q` | **314 passed**, 1 third-party Starlette/AnyIO deprecation warning, 46.24s. |
| `backend/.venv/Scripts/python.exe -m ruff check .` | Passed: `All checks passed!` |
| `backend/.venv/Scripts/python.exe -m evals.runner --mode deterministic` | **28 passed, 0 failed**; evidence recall, citation precision, tool selection, and approval-policy accuracy were all 1.0; unsupported claims 0. |
| Fresh isolated client: `npm ci && npm run verify:auth-routing && npm run build && npm run verify:bundle` | `npm ci` installed 603 packages; auth redirect safety **5 cases passed**; production build transformed 4,482 modules in 19.53s; bundle budget passed with 125 JS chunks, 4.85 MB total, 662.1 KB largest chunk. |
| Fresh isolated client: `npm audit --audit-level=high` | Passed exit threshold; 7 low-severity KaTeX-chain findings remain with no available fix. |
| `docker compose config --quiet` | Passed static Compose validation. |
| `git diff --check` | Passed; Git emitted only CRLF conversion notices for existing working-tree files. |

The checked-out client's existing `node_modules` remains an invalid partial
install: the source build reproduces `UNRESOLVED_IMPORT` for missing
`es-toolkit/dist/predicate/isEqual.mjs`. The same source and lockfile pass in
the fresh installation above, so this is a local generated-artifact issue, not
a source change or a justification to alter the lockfile. The user-owned Vite
process was left untouched.

## Corrected production-profile issue

`render.yaml` now keeps the full production-intelligence profile within the
requested 512 MB-per-service envelope. It has four fixed, single-instance
roles for a total 2-vCPU baseline; this is a bounded starting point rather
than a claim that large repositories or unrestricted concurrent model work fit
in that budget:

| Service | Render plan | Provisioned CPU | Responsibility |
| --- | --- | ---: | --- |
| Public API | `0.5c-512mb` | 0.5 | authenticated API, SSE, approvals, job dispatch |
| Intelligence indexer | `0.5c-512mb` | 0.5 | bounded clone/index/graph construction only |
| CI observer | `0.5c-512mb` | 0.5 | read-only delivery CI reconciliation |
| Maintenance cron | `0.5c-512mb` | 0.5 | expiry and recovery tasks |

The compact `render.mvp.yaml` remains a one-service `0.5c-512mb` option for
low-cost use. It is intentionally not represented as equivalent capacity, and
larger plans or autoscaling require a measured staging justification.

## Exact external gates and local substitutes

| Blocked operation | Required external dependency / permission | Evidence | Local substitute completed | Next concrete action |
| --- | --- | --- | --- | --- |
| Complete GitHub OAuth, list an account's repositories, and create a real draft PR | Authorized GitHub OAuth application, user consent, and a non-production repository; external writes are not authorized for this audit. | No GitHub connector is configured in this workspace. | Fake repository-boundary tests cover canonical URLs, redirect safety, tenant cache isolation, disconnect, malformed provider data, and authorization boundaries. The legacy PR mutation route is fail-closed and its regression test proves an unbound approval cannot prepare or push a workspace. | In an authorized test tenant, connect GitHub and run one read-only repository-listing test; separately approve an exact delivery plan in a disposable repository and verify a draft PR. |
| Rendered browser smoke test | Browser access to the developer machine's local frontend. | Browser Use attempted both `http://127.0.0.1:5173/demo` and `http://localhost:5173/demo` and received `ERR_CONNECTION_REFUSED` before any application response. Direct local requests later returned `200` for `/demo` and `200` from backend `/ready`, establishing that the Browser runtime cannot reach this machine's loopback interface. | Static frontend checks and locally running service readiness were verified; no sign-in, GitHub connection, or mutation was attempted. | Run the smoke test from a browser with access to the developer machine or against an authorized preview deployment. |
| Submit/validate a real Render Blueprint or deploy to Vercel | Render/Vercel account access and target environment secrets. | `Get-Command render,vercel` returned no installed CLIs; no provider connection is configured. Deployment is also outside this audit's authorized actions. | `docker compose config --quiet`, Render Blueprint tests, production configuration tests, startup/readiness tests, and fresh production frontend build all pass locally. | Install the providers' CLIs or use their authorized CI identities, set secrets in the target secret managers, validate the Blueprint, and deploy only with explicit approval. |
| Connect Atlas/Redis/Clerk/Razorpay/OpenAI production services | Service credentials, network allow-listing, and a designated tenant. | No service credentials were supplied or used; no production connection was attempted. | Deterministic fixtures/fakes cover authentication failure safety, tenant isolation, rate-limit readiness behavior, OAuth-state security, idempotency, redaction, replay, stale-plan rejection, malformed responses, and partial failures. | Populate a staging secret store, allow egress, then run authenticated smoke tests and readiness checks without using production data. |
| Execute active repository validation | An externally hosted isolated execution provider. | Production configuration intentionally uses `EXECUTION_MODE=disabled`; no isolated provider is configured. | Execution-sandbox tests verify that unavailable isolation fails closed rather than running repository code on the API or worker host. | Deploy and configure an isolated provider in a non-production environment, then enable and test it explicitly. |

`npm audit --audit-level=high` exits successfully, but reports seven
low-severity KaTeX-chain advisories for which the installed dependency graph
exposes no available fix. They remain a release-review item rather than a
claim of zero dependency risk.

## Remaining release gates

1. Enter every Render `sync: false` value in the secret manager and set the
   exact `BASE_URL`, `FRONTEND_ORIGIN`, `TRUSTED_HOSTS`, Clerk issuer/JWKS,
   and Clerk authorized parties.
2. Register `<BASE_URL>/api/github/callback` in the GitHub OAuth application.
3. Allow the Render egress addresses in Atlas, then require `/ready` to return
   HTTP 200 before directing Vercel traffic to the API.
4. Configure the Vercel Production and Preview environment variables without
   exposing server-only credentials.
5. Run an authenticated smoke test: sign in, reconnect GitHub, list a
   repository, create a read-only session, inspect evidence, and verify an
   approval can be rejected without mutation.
6. Only enable a higher-capacity or autoscaled profile after a non-production
   load test demonstrates the need and the resource budget is revised.
