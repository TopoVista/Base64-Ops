# Architecture

```mermaid
flowchart LR
  UI[React / Vercel] -->|Clerk JWT + SSE| API[FastAPI control plane]
  API --> Graph[LangGraph investigation]
  Graph --> Context[Context pack + repository map]
  Graph --> RAG[Repository retrieval]
  RAG --> Evidence[Evidence store]
  Graph --> Policy[Deterministic policy]
  Policy --> Plan[Exact delivery plan]
  Plan --> Approval[Hash-bound human approval]
  Approval --> Delivery[Idempotent draft-PR delivery]
  API --> Mongo[(MongoDB)]
  API --> GitHub[GitHub OAuth / API]
  Graph --> OpenAI[Optional OpenAI]
```

The graph is intentionally small: workspace context, repository map, retrieval, optional read-only GitHub Actions context, action selection, investigation scaffold, optional approval, execution, and response. CI logs and workflow files enter only as untrusted, redacted evidence; they never become commands or mutation authority. The UI exposes safe execution events and evidence rather than hidden model reasoning.

## Safety boundary

```mermaid
flowchart LR
  U[Untrusted repo / CI / logs / memory] --> E[Redacted evidence boundary]
  E --> I[Policy-controlled investigation]
  I --> P[Evidence-scoped candidate patch]
  P --> S[Sandbox / allowlisted validation]
  S --> R[Deterministic risk]
  R --> A[Exact human approval]
  A --> X[Revalidated execution / draft PR]
```

No edge in this diagram lets content from the left register a validator, change policy, disclose credentials, or authorize execution.
