# Base64 Ops implementation audit

Audit date: 2026-09-24

## Current architecture

Base64 Ops is a React/Vite command-center frontend and a FastAPI control plane. Clerk authenticates browser users; the browser sends a Clerk bearer token to the API. FastAPI owns sessions, GitHub OAuth, MongoDB persistence, RAG indexing, SSE streaming, and a small LangGraph workflow. MongoDB Atlas Vector Search is optional; a Mongo lexical fallback is used when embeddings are unavailable. GitHub is the repository provider and workspace clones live on the backend filesystem.

## Important implementation locations

| Area | Current location | Current behavior |
| --- | --- | --- |
| API entrypoint | `backend/app/main.py` | FastAPI lifespan, Mongo connection, CORS, auth/GitHub/session/assistant routers. |
| Auth | `backend/app/api/deps.py`, `backend/app/services/auth_service.py` | Validates Clerk JWTs and upserts a backend user. |
| Agent | `backend/app/agent/{state,graph,policy,tools,prompts}.py` | `load_context -> retrieve -> plan -> [approval] -> execute -> respond`. |
| Persistence | `backend/app/db/mongo.py`, `backend/app/services/session_service.py` | `users`, `sessions`, `messages`, `approvals`, `rag_chunks`, `runbooks`, `github_accounts`; guide history and worker jobs are also present. |
| RAG | `backend/app/services/rag_service.py` | Indexes supported source files into `rag_chunks`; Atlas similarity search with basic regex fallback. |
| GitHub | `backend/app/services/github_service.py`, `backend/app/api/routes/github.py` | OAuth, encrypted stored user token, repository listing, PR API calls. |
| Workspaces/tools | `backend/app/services/workspace_service.py`, `backend/app/agent/tools.py` | Clones repo; runs allowlisted git/Docker checks; branch creation and git mutations are approval-gated. |
| Sessions/streaming | `backend/app/api/routes/session.py`, `backend/app/utils/sse.py` | `POST /api/session/chat` emits session, timeline, sources, approval, and message SSE events. |
| Frontend | `client/src/components/chat`, `client/src/hooks/use-agent-session.ts` | Chat UI consumes SSE; right rail displays context, timeline, RAG sources, and approvals. |
| Deployment | `render.yaml`, `backend/Dockerfile`, `client/vercel.json`, `docs/DEPLOYMENT.md` | Vercel SPA frontend and exactly two bounded Render services: the API and an allowlisted maintenance cron. |
| Tests | `backend/tests` | pytest coverage for policy, chunking, and product-guide fallback. |

## Existing LangGraph flow

The graph retrieves repository chunks, selects an action with deterministic keyword policy, interrupts before actions classified as mutable, then executes a narrow toolbox action and synthesizes a response. It already avoids creating a branch during initial read-only context loading. The approval interrupt persists an approval record in Mongo through the session service.

## Existing gaps against the evidence-first target

- Sources are transient RAG dictionaries, not persistent provenance records with stable IDs, hashes, line ranges, commit IDs, or redaction.
- The graph has no structured investigation, hypotheses, context pack, repository map, diagnosis, risk, or validation-plan model.
- Retrieval is vector-or-regex rather than tenant-safe hybrid retrieval with lexical ranking and provenance.
- Approval records do not bind a canonical plan/diff/head hash, so the approved intent cannot be cryptographically compared with later execution state.
- `create_pull_request` currently pushes outside the graph approval path; it must be bound to an approved delivery plan before becoming a normal UI action.
- There is no incident memory, evaluation harness, OTel-compatible trace model, screenshot evidence, playbook loader, or MCP adapter/server.
- The UI has sources and a timeline but not progressive evidence cards or an investigation result panel.
- Real repository, MongoDB, Clerk, GitHub OAuth, and OpenAI connections remain configuration-dependent and cannot be validated without the required secrets.

## Implementation approach

P0 will add a minimal, durable evidence and investigation foundation without replacing Clerk, GitHub OAuth, SSE, MongoDB, or the existing frontend. The graph will be expanded by typed, deterministic nodes and persistence boundaries. External integrations remain optional and must report a clear degraded state rather than fabricate evidence.
