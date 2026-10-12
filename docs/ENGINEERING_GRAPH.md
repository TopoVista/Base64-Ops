# Engineering graph foundation

The Engineering Intelligence graph is a repository-fact graph, distinct from
the LangGraph orchestration graph. It provides a bounded, revision-specific
map that other capabilities can query without treating inferred relationships
as proof of causation.

## What is recorded

Each immutable snapshot is scoped to one authenticated user, one session, one
repository, and one Git commit SHA. It currently records deterministic static
facts for:

- repository files and containment;
- Python modules, classes, functions, tests, imports, and statically visible
  FastAPI route decorators via the Python standard-library AST;
- local JavaScript/TypeScript import relationships as a deliberately labelled
  static-import fallback;
- GitHub Actions workflow and job topology via safe YAML parsing; and
- declared package dependencies from supported manifests.

Nodes have a stable logical identity and a commit-specific version identity.
Thus a same-named function at the same path can be related across revisions
without being mistaken for unchanged source code.

## Trust and safety

The builder never imports repository modules, starts a process, evaluates a
package script, or executes a workflow `run:` command. Workflow commands are
not stored in graph attributes. They remain untrusted repository data for the
existing evidence boundary.

Every relationship carries a discovery method and an epistemic status. Parsed
imports are observed static facts; unresolved inputs and heuristic static
imports remain explicitly distinguishable. Graph correlation must not be
presented as root-cause proof.

## Build lifecycle

Graph construction runs alongside the existing reindex operation, which is
already either a bounded inline operation or a durable worker job depending on
`INDEXING_MODE`. A graph-build failure does not discard a successfully indexed
RAG corpus; the session records the graph status separately. The API never
constructs a graph in response to a browser `GET` request.

Mongo snapshots are inserted once per `(user, session, repository, commit)`.
Older revisions are retained, rather than overwritten, and the graph is only
queried within the requesting tenant and session.

## APIs

- `GET /api/session/{slug}/intelligence/graph`
- `GET /api/session/{slug}/intelligence/graph/status`
- `GET /api/session/{slug}/intelligence/graph/nodes/{node_id}?depth=1&limit=100`

The first two routes return build metadata only. The node route returns a
focused, bidirectional neighborhood with explicit traversal limits. It omits
tenant IDs, evidence IDs, raw source, and workflow commands from browser
responses.

## Current limits

## Change impact analysis

The **Changes** workspace can analyze an existing exact `DeliveryPlan` through
`POST /api/session/{slug}/delivery-plan/{plan_id}/impact`. This is a
read-only, deterministic reverse-dependency traversal over the graph snapshot
at the plan's `base_sha`; it does not call an LLM, execute repository code,
change a candidate workspace, create approval, or mutate GitHub.

The analysis returns direct and indirect structural dependents, parsed tests
contained in reached test files, and any reached workflow/job nodes. Every
result says **potentially affected**: a static import or dependency is useful
review evidence, not proof of runtime causality or a prediction that CI will
fail. Results include hop distance, the observed relation chain, graph/plan
SHA provenance, explicit bounds, and limitations.

The service refuses to mix revisions. If no snapshot exists at the plan SHA,
or if a proposed path was not captured in that snapshot, it reports
`unavailable`/an unmapped-path limitation instead of querying current HEAD or
inventing impact. Analyses are persisted in a tenant/session-scoped Mongo
collection for replay via `GET /api/session/{slug}/intelligence/impact/{id}`.
The protected historical run replay also restores the latest plan-scoped impact
report alongside the exact diff and validation record. That replay view hides
internal graph node IDs and displays only relation labels, paths, explanatory
text, SHA provenance, and bounded limitations. The internal `impact.analysis`
trace span records only counts and status.

This is the temporal graph foundation, not a claim of complete runtime
architecture discovery. The current release bounds a build to 500 files, 4,000
nodes, and 8,000 edges. Go, Docker/Compose, API consumer, call-site, and
cross-repository analysis remain intentionally unavailable until each has a
tested extractor and provenance model. A partial or unsupported build is
reported in `coverage.limitations`, never silently presented as complete.
