import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { DiffViewer } from "@/components/diff-viewer";
import { Loader } from "@/components/loader";
import { getDevRunReplay, getSystemCapabilities } from "@/lib/api";

interface DevRunData {
  run_id: string;
  historical: boolean;
  replay_mode: "read_only";
  session?: {
    slug_id?: string | null;
    repository?: string | null;
  };
  repository?: {
    headSha?: string | null;
    head_sha?: string | null;
    branch?: string | null;
  };
  investigation?: {
    summary?: string | null;
    diagnosis?: string | null;
    status?: string | null;
  } | null;
  timeline: Array<{
    label?: string | null;
    detail?: string | null;
    status?: string | null;
    timestamp?: string | null;
  }>;
  limitations: string[];
  tool_result?: { success: boolean; output: string } | null;
  trace: {
    id?: string | null;
    status?: string | null;
    total_duration_ms?: number;
    started_at?: string;
    completed_at?: string;
    model_usage?: Record<string, unknown>;
  } | null;
  spans: Array<{
    id?: string | null;
    name?: string | null;
    status?: string | null;
    duration_ms?: number;
    safe_input_summary?: Record<string, unknown>;
    safe_output_summary?: Record<string, unknown>;
    error_type?: string;
    error_message?: string;
  }>;
  delivery_plan: {
    id?: string | null;
    title?: string | null;
    rationale?: string | null;
    risk_level?: string | null;
    risk_reasons: string[];
    evidence_ids: string[];
    files: Array<{
      path?: string | null;
      change_type?: string | null;
      original_hash?: string | null;
      proposed_hash?: string | null;
      unified_diff?: string | null;
      diff_truncated?: boolean;
    }>;
    validation_steps: Array<{
      id?: string | null;
      kind?: string | null;
      description?: string | null;
    }>;
  } | null;
  approval: {
    id?: string | null;
    status?: string | null;
    risk_level?: string | null;
    diff_hash?: string | null;
  } | null;
  delivery_result: {
    id?: string | null;
    status?: string | null;
    failure_stage?: string;
    pull_request_url?: string;
    commit_sha?: string;
    branch_name?: string;
    validation_results?: Array<{
      step_id?: string | null;
      status: "passed" | "failed" | "skipped" | "error";
      summary: string;
      duration_ms?: number;
      evidence_id?: string;
    }>;
  } | null;
  ci_investigation: {
    run_id?: number | null;
    workflow_name?: string | null;
    head_sha?: string | null;
    current_head_sha?: string | null;
    run_is_historical?: boolean | null;
    applicability?: string | null;
    applicability_reason?: string | null;
    failed_jobs?: Array<{ name?: string | null; failed_step?: string | null }>;
    evidence_ids?: string[];
    workflow_evidence_ids?: string[];
    changed_file_evidence_ids?: string[];
    relevant_paths?: string[];
    limitations?: string[];
  } | null;
  evidence_count: number;
  evidence: Array<{
    id?: string | null;
    path?: string | null;
    source?: string | null;
    retrieval_method?: string | null;
    score?: number;
    source_type?: string | null;
    commit_sha?: string | null;
    state?: string | null;
    truncated?: boolean;
  }>;
  change_impact?: {
    id?: string | null;
    status?: "available" | "unavailable" | null;
    plan_base_sha?: string | null;
    graph_commit_sha?: string | null;
    changed_paths?: string[];
    unmapped_paths?: string[];
    directly_affected?: ReplayImpactTarget[];
    indirectly_affected?: ReplayImpactTarget[];
    related_tests?: ReplayImpactTarget[];
    related_workflows?: ReplayImpactTarget[];
    truncated?: boolean;
    limitations?: string[];
    created_at?: string | null;
  } | null;
}

interface ReplayImpactTarget {
  node_type?: string | null;
  name?: string | null;
  path?: string | null;
  qualified_name?: string | null;
  distance?: number | null;
  relations?: string[];
  explanation?: string | null;
}

interface CapabilitiesData {
  execution: {
    runtime: string;
    docker_available: boolean;
    network_isolation: boolean;
    resource_limits: boolean;
    active_validation: boolean;
    unavailable_reason?: string | null;
    environment_sanitizing: boolean;
    workspace_integrity_checks: boolean;
  };
}

const replayTabs = ["overview", "plan", "spans", "retrieval"] as const;
type ReplayTab = (typeof replayTabs)[number];

function replayLoadMessage(error: unknown): string {
  const response =
    error && typeof error === "object" && "response" in error
      ? (error as { response?: { status?: unknown } }).response
      : undefined;
  const status =
    typeof response?.status === "number" ? response.status : undefined;
  if (status === 401 || status === 403) {
    return "You no longer have permission to view this historical run. Sign in again and retry.";
  }
  if (status === 404) {
    return "This historical run is unavailable to this account. It may have been removed or belong to another workspace.";
  }
  return "The historical run could not be loaded. Nothing was rerun or changed; retry in a moment.";
}

export default function DevRunInspector() {
  const { runId } = useParams<{ runId: string }>();
  const [data, setData] = useState<DevRunData | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesData | null>(
    null,
  );
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ReplayTab>("overview");
  const [retryVersion, setRetryVersion] = useState(0);
  const replayTabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const selectReplayTab = (tab: ReplayTab) => {
    setActiveTab(tab);
  };

  const handleReplayTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = replayTabs.indexOf(activeTab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % replayTabs.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + replayTabs.length) % replayTabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = replayTabs.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = replayTabs[nextIndex];
    setActiveTab(nextTab);
    replayTabRefs.current[nextIndex]?.focus();
  };

  useEffect(() => {
    let active = true;
    async function fetchInspector() {
      if (!runId) {
        setData(null);
        setCapabilities(null);
        setError("This run reference is missing from the URL.");
        setLoading(false);
        return;
      }
      try {
        setLoading(true);
        setError(null);
        setData(null);
        setCapabilities(null);
        const [replay, capability] = await Promise.all([
          getDevRunReplay<DevRunData>(runId),
          getSystemCapabilities<CapabilitiesData>().catch(() => null),
        ]);
        if (!active) return;
        setData(replay);
        setCapabilities(capability);
      } catch (err: unknown) {
        if (!active) return;
        setData(null);
        setError(replayLoadMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    }
    fetchInspector();
    return () => {
      active = false;
    };
  }, [runId, retryVersion]);

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Loader />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mx-auto flex min-h-[22rem] max-w-2xl items-center p-6">
        <section className="w-full rounded-xl border border-destructive/30 bg-destructive/[0.04] p-6 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-destructive">
            Historical replay unavailable
          </p>
          <h2 className="mt-2 text-xl font-semibold tracking-tight text-foreground">
            We could not load this run
          </h2>
          <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
            {error || "No historical data was returned for this run ID."}
          </p>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            Retrying only requests the persisted replay record. It never reruns
            tools, GitHub calls, validation, or delivery.
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => setRetryVersion((version) => version + 1)}
              className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            >
              Retry replay
            </button>
            <Link
              to="/new"
              className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
            >
              Return to workspace
            </Link>
          </div>
        </section>
      </div>
    );
  }

  const {
    trace,
    spans,
    delivery_plan,
    approval,
    delivery_result,
    evidence,
    ci_investigation,
  } = data;
  const formatDuration = (duration?: number) =>
    duration === undefined || duration === null
      ? "Unavailable"
      : duration >= 1000
        ? `${(duration / 1000).toFixed(2)} s`
        : `${duration} ms`;
  const formatScore = (score?: number) =>
    typeof score === "number" && Number.isFinite(score)
      ? score.toFixed(3)
      : "Not scored";
  const formatTimestamp = (value?: string | null) => {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  };
  const formatSafeSummary = (value?: Record<string, unknown>) => {
    if (!value || Object.keys(value).length === 0) return null;
    return JSON.stringify(value, null, 2);
  };
  const planFiles = delivery_plan?.files ?? [];
  const riskReasons = delivery_plan?.risk_reasons ?? [];
  const modelUsage = trace?.model_usage ?? {};
  const validationResults = delivery_result?.validation_results ?? [];
  const changeImpact = data.change_impact;
  const renderImpactTargets = (targets: ReplayImpactTarget[] | undefined, empty: string) =>
    targets?.length ? (
      <ul className="mt-2 space-y-2">
        {targets.map((target, index) => (
          <li
            key={`${target.path ?? target.qualified_name ?? target.name ?? "impact"}-${index}`}
            className="rounded-md border bg-muted/20 px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <code className="font-medium text-foreground">
                {target.path ?? target.qualified_name ?? target.name ?? "Unknown component"}
              </code>
              {target.node_type && (
                <span className="rounded bg-background px-1.5 py-0.5 uppercase text-muted-foreground">
                  {target.node_type.replaceAll("_", " ")}
                </span>
              )}
              {target.distance && (
                <span className="text-muted-foreground">
                  {target.distance} hop{target.distance === 1 ? "" : "s"}
                </span>
              )}
              {target.relations?.length ? (
                <span className="text-muted-foreground">
                  via {target.relations.join(" → ").replaceAll("_", " ")}
                </span>
              ) : null}
            </div>
            {target.explanation && (
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{target.explanation}</p>
            )}
          </li>
        ))}
      </ul>
    ) : (
      <p className="mt-2 text-xs text-muted-foreground">{empty}</p>
    );
  // Delivery is the terminal outcome when it exists. A graph run can finish
  // successfully while its subsequent validated delivery fails, so presenting
  // the trace status first would make a failed historical change look green.
  const replayStatus = delivery_result?.status ?? trace?.status ?? "recorded";
  const replaySucceeded =
    delivery_result?.status === "success" ||
    (!delivery_result && trace?.status === "completed");
  const replayFailed =
    delivery_result?.status === "failed" ||
    (!delivery_result && trace?.status === "failed");

  return (
    <div className="mx-auto max-w-6xl p-6 space-y-6 text-foreground">
      {/* Header */}
      <div className="flex flex-col gap-4 border-b pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">Run Inspector</h1>
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase ${
                replaySucceeded
                  ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                  : replayFailed
                    ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                    : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
              }`}
            >
              {replayStatus}
            </span>
          </div>
          <p className="text-sm text-muted-foreground mt-1 font-mono">
            Run ID: {data.run_id}
          </p>
        </div>
        <div className="shrink-0 text-left text-xs text-muted-foreground space-y-2 sm:text-right">
          <div className="space-y-1">
            {trace?.total_duration_ms !== undefined &&
              trace?.total_duration_ms !== null && (
                <div>
                  Duration:{" "}
                  <span className="font-semibold text-foreground">
                    {formatDuration(trace.total_duration_ms)}
                  </span>
                </div>
              )}
            {data.evidence_count > 0 && (
              <div>
                Evidence Items:{" "}
                <span className="font-semibold text-foreground">
                  {data.evidence_count}
                </span>
              </div>
            )}
          </div>
          {data.session?.slug_id && (
            <Link
              to={`/session/${data.session.slug_id}/history`}
              className="inline-flex rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              Back to session history
            </Link>
          )}
        </div>
      </div>

      {/* Execution Sandbox Trust Signal */}
      {capabilities && (
        <div className="rounded-lg border bg-muted/30 p-3 text-xs flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 font-mono">
            <span className="font-semibold text-foreground">
              Execution Sandbox:
            </span>
            <span className="rounded bg-primary/10 px-2 py-0.5 text-primary font-bold">
              {capabilities.execution.runtime}
            </span>
          </div>
          <div className="flex items-center gap-4 text-muted-foreground">
            <span>
              Network Isolation:{" "}
              <strong
                className={
                  capabilities.execution.network_isolation
                    ? "text-green-600"
                    : "text-amber-600"
                }
              >
                {capabilities.execution.network_isolation
                  ? "Active"
                  : capabilities.execution.active_validation
                    ? "Local development only"
                    : "Unavailable"}
              </strong>
            </span>
            <span>
              Sanitized Env:{" "}
              <strong className="text-foreground">Stripped</strong>
            </span>
            <span>
              Integrity Checks:{" "}
              <strong className="text-green-600">Active</strong>
            </span>
          </div>
          {!capabilities.execution.active_validation && capabilities.execution.unavailable_reason && (
            <p className="w-full text-amber-700 dark:text-amber-300">
              {capabilities.execution.unavailable_reason}
            </p>
          )}
        </div>
      )}

      <section className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-4 text-sm">
        <h2 className="font-semibold text-blue-700 dark:text-blue-200">
          Historical replay
        </h2>
        <p className="mt-1 text-muted-foreground">
          This view reads the persisted run record only. It never reruns tools,
          accesses GitHub, validates commands, or applies a patch.
        </p>
        {(data.repository?.headSha || data.repository?.head_sha) && (
          <p className="mt-2 font-mono text-xs text-muted-foreground">
            Recorded repository SHA:{" "}
            {data.repository.headSha || data.repository.head_sha}
          </p>
        )}
        {data.limitations.length > 0 && (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-amber-700 dark:text-amber-200">
            {data.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        )}
      </section>

      {ci_investigation && (
        <section className="rounded-lg border border-cyan-500/25 bg-cyan-500/5 p-4 text-sm">
          <h2 className="font-semibold text-cyan-700 dark:text-cyan-200">
            GitHub Actions investigation
          </h2>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 text-muted-foreground">
            <p>
              Workflow:{" "}
              <span className="text-foreground">
                {ci_investigation.workflow_name ?? "Unknown"}
              </span>
            </p>
            <p>
              Run:{" "}
              <span className="text-foreground">
                #{ci_investigation.run_id}
              </span>
            </p>
            <p>
              Failed SHA:{" "}
              <span className="font-mono text-foreground">
                {ci_investigation.head_sha?.slice(0, 12) ?? "Unavailable"}
              </span>
            </p>
            <p>
              Current SHA:{" "}
              <span className="font-mono text-foreground">
                {ci_investigation.current_head_sha?.slice(0, 12) ??
                  "Unavailable"}
              </span>
            </p>
            <p>
              Status:{" "}
              <span className="text-foreground">
                {ci_investigation.applicability?.replaceAll("_", " ") ??
                  "not determined"}
              </span>
            </p>
            <p>
              Failed jobs:{" "}
              <span className="text-foreground">
                {ci_investigation.failed_jobs?.length ?? 0}
              </span>
            </p>
          </div>
          {ci_investigation.applicability_reason && (
            <p className="mt-3 text-muted-foreground">
              {ci_investigation.applicability_reason}
            </p>
          )}
          {(ci_investigation.limitations?.length ?? 0) > 0 && (
            <p className="mt-2 text-amber-700 dark:text-amber-200">
              Limited visibility: {ci_investigation.limitations?.join(" ")}
            </p>
          )}
        </section>
      )}

      {/* Navigation Tabs */}
      <div
        className="flex gap-4 overflow-x-auto border-b text-sm font-medium whitespace-nowrap"
        role="tablist"
        aria-label="Run inspector sections"
      >
        <button
          id="replay-overview-tab"
          ref={(element) => {
            replayTabRefs.current[0] = element;
          }}
          onClick={() => selectReplayTab("overview")}
          onKeyDown={handleReplayTabKeyDown}
          role="tab"
          aria-selected={activeTab === "overview"}
          aria-controls="replay-overview"
          tabIndex={activeTab === "overview" ? 0 : -1}
          className={`shrink-0 pb-2 border-b-2 transition-colors ${
            activeTab === "overview"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Replay overview
        </button>
        <button
          id="replay-plan-tab"
          ref={(element) => {
            replayTabRefs.current[1] = element;
          }}
          onClick={() => selectReplayTab("plan")}
          onKeyDown={handleReplayTabKeyDown}
          role="tab"
          aria-selected={activeTab === "plan"}
          aria-controls="replay-plan"
          tabIndex={activeTab === "plan" ? 0 : -1}
          className={`shrink-0 pb-2 border-b-2 transition-colors ${
            activeTab === "plan"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Delivery Plan & Patch
        </button>
        <button
          id="replay-spans-tab"
          ref={(element) => {
            replayTabRefs.current[2] = element;
          }}
          onClick={() => selectReplayTab("spans")}
          onKeyDown={handleReplayTabKeyDown}
          role="tab"
          aria-selected={activeTab === "spans"}
          aria-controls="replay-spans"
          tabIndex={activeTab === "spans" ? 0 : -1}
          className={`shrink-0 pb-2 border-b-2 transition-colors ${
            activeTab === "spans"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Graph Spans & Tracing ({spans.length})
        </button>
        <button
          id="replay-retrieval-tab"
          ref={(element) => {
            replayTabRefs.current[3] = element;
          }}
          onClick={() => selectReplayTab("retrieval")}
          onKeyDown={handleReplayTabKeyDown}
          role="tab"
          aria-selected={activeTab === "retrieval"}
          aria-controls="replay-retrieval"
          tabIndex={activeTab === "retrieval" ? 0 : -1}
          className={`shrink-0 pb-2 border-b-2 transition-colors ${
            activeTab === "retrieval"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Retrieval Debug View ({evidence.length})
        </button>
      </div>

      {activeTab === "overview" && (
        <div
          id="replay-overview"
          role="tabpanel"
          aria-labelledby="replay-overview-tab"
          className="grid gap-4 lg:grid-cols-2"
        >
          <section className="rounded-xl border bg-card p-5">
            <h3 className="text-sm font-semibold">Recorded investigation</h3>
            <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
              {data.investigation?.diagnosis ||
                data.investigation?.summary ||
                "No structured diagnosis was persisted for this run."}
            </p>
            {data.tool_result && (
              <div className="mt-4 rounded-md border bg-muted/30 p-3 text-xs">
                <span className="font-semibold">Tool outcome: </span>
                {data.tool_result.success ? "completed" : "did not complete"}
                {data.tool_result.output && (
                  <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap text-muted-foreground">
                    {data.tool_result.output}
                  </pre>
                )}
              </div>
            )}
          </section>
          <section className="rounded-xl border bg-card p-5">
            <h3 className="text-sm font-semibold">Historical timeline</h3>
            {data.timeline.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                No timeline entries were persisted.
              </p>
            ) : (
              <ol className="mt-3 space-y-3 border-l pl-4">
                {data.timeline.map((entry, index) => (
                  <li
                    key={`${entry.label ?? "event"}-${index}`}
                    className="relative text-sm"
                  >
                    <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-primary" />
                    <p className="font-medium">{entry.label ?? "Run event"}</p>
                    {entry.detail && (
                      <p className="text-muted-foreground">{entry.detail}</p>
                    )}
                    {formatTimestamp(entry.timestamp) && (
                      <p className="mt-1 font-mono text-xs text-muted-foreground">
                        {formatTimestamp(entry.timestamp)}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>
          <section className="rounded-xl border bg-card p-5">
            <h3 className="text-sm font-semibold">
              Model and delivery telemetry
            </h3>
            {Object.keys(modelUsage).length === 0 && !delivery_result ? (
              <p className="mt-2 text-sm text-muted-foreground">
                No provider usage or delivery result was persisted for this run.
              </p>
            ) : (
              <div className="mt-3 space-y-4 text-sm">
                {Object.keys(modelUsage).length > 0 && (
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
                    {Object.entries(modelUsage).map(([key, value]) => (
                      <div key={key}>
                        <dt className="text-xs text-muted-foreground">
                          {key.replaceAll("_", " ")}
                        </dt>
                        <dd className="font-mono text-foreground">
                          {typeof value === "object"
                            ? JSON.stringify(value)
                            : String(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
                {delivery_result && (
                  <div className="border-t pt-3 text-muted-foreground">
                    <p>
                      Delivery:{" "}
                      <span className="font-medium text-foreground">
                        {delivery_result.status}
                      </span>
                    </p>
                    {delivery_result.branch_name && (
                      <p className="mt-1">
                        Branch:{" "}
                        <span className="font-mono text-foreground">
                          {delivery_result.branch_name}
                        </span>
                      </p>
                    )}
                    {delivery_result.commit_sha && (
                      <p className="mt-1">
                        Commit:{" "}
                        <span className="font-mono text-foreground">
                          {delivery_result.commit_sha.slice(0, 12)}
                        </span>
                      </p>
                    )}
                    {delivery_result.pull_request_url && (
                      <a
                        className="mt-2 inline-flex text-primary underline underline-offset-4"
                        href={delivery_result.pull_request_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open draft pull request
                      </a>
                    )}
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      {/* Tab: Delivery Plan & Patch */}
      {activeTab === "plan" && (
        <div
          id="replay-plan"
          role="tabpanel"
          aria-labelledby="replay-plan-tab"
          className="space-y-6"
        >
          {delivery_plan ? (
            <div className="rounded-xl border bg-card p-5 space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-semibold">
                    {delivery_plan.title}
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    {delivery_plan.rationale}
                  </p>
                </div>
                <div className="text-right">
                  <span
                    className={`inline-block rounded px-2 py-1 text-xs font-bold uppercase ${
                      delivery_plan.risk_level === "low"
                        ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
                        : delivery_plan.risk_level === "high"
                          ? "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300"
                          : delivery_plan.risk_level === "critical"
                            ? "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
                            : "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300"
                    }`}
                  >
                    Risk: {delivery_plan.risk_level}
                  </span>
                </div>
              </div>

              {/* Risk Reasons */}
              {riskReasons.length > 0 && (
                <div className="rounded-lg bg-muted/50 p-3 text-xs">
                  <span className="font-semibold block mb-1">
                    Risk Factors:
                  </span>
                  <ul className="list-disc list-inside space-y-0.5 text-muted-foreground">
                    {riskReasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Approval status */}
              {approval && (
                <div className="flex items-center justify-between text-xs border-t pt-3 font-mono">
                  <span>
                    Approval Binding:{" "}
                    <span className="text-foreground">{approval.status}</span>
                  </span>
                  <span>
                    Diff Hash:{" "}
                    <span className="text-foreground">
                      {approval.diff_hash
                        ? `${approval.diff_hash.slice(0, 16)}...`
                        : "Unavailable"}
                    </span>
                  </span>
                </div>
              )}

              {/* Proposed Files & Diff */}
              <div className="space-y-4 pt-2">
                <h4 className="text-sm font-semibold">
                  Proposed Candidate Edits ({planFiles.length} file(s))
                </h4>
                {planFiles.map((file, idx) => (
                  <div
                    key={file.path ?? `file-${idx}`}
                    className="rounded-lg border bg-background overflow-hidden"
                  >
                    <div className="px-4 py-2 border-b bg-muted/40 flex items-center justify-between text-xs font-mono">
                      <span>
                        {file.path ?? "Unnamed file"} ({file.change_type ?? "change"})
                      </span>
                      {file.original_hash && (
                        <span className="text-muted-foreground">
                          Orig SHA: {file.original_hash.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    {file.diff_truncated && (
                      <p className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
                        This historical diff was shortened for safe viewing. It
                        is a preview, not the complete exact delivery diff.
                      </p>
                    )}
                    <DiffViewer patch={file.unified_diff ?? undefined} />
                  </div>
                ))}
              </div>

              {changeImpact && (
                <section className="rounded-lg border border-cyan-500/25 bg-cyan-500/[0.04] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h4 className="text-sm font-semibold">Recorded change impact</h4>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        Historical structural analysis only. It identifies potential dependents, not proof of runtime failure.
                      </p>
                    </div>
                    <span className={`rounded px-2 py-1 text-xs font-semibold uppercase ${
                      changeImpact.status === "available"
                        ? "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-200"
                        : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                    }`}>
                      {changeImpact.status ?? "unavailable"}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>Plan SHA: <code className="text-foreground">{changeImpact.plan_base_sha?.slice(0, 12) ?? "unavailable"}</code></span>
                    <span>Graph SHA: <code className="text-foreground">{changeImpact.graph_commit_sha?.slice(0, 12) ?? "unavailable"}</code></span>
                    {changeImpact.truncated ? <span className="text-amber-700 dark:text-amber-200">Bounded results</span> : null}
                  </div>
                  {changeImpact.changed_paths?.length ? (
                    <p className="mt-3 text-xs text-muted-foreground">
                      Changed: <code className="text-foreground">{changeImpact.changed_paths.join(", ")}</code>
                    </p>
                  ) : null}
                  {changeImpact.status === "available" && (
                    <div className="mt-4 grid gap-4 lg:grid-cols-2">
                      <div>
                        <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Direct dependents</h5>
                        {renderImpactTargets(changeImpact.directly_affected, "No direct dependents were recorded.")}
                      </div>
                      <div>
                        <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Indirect dependents</h5>
                        {renderImpactTargets(changeImpact.indirectly_affected, "No indirect dependents were recorded.")}
                      </div>
                      <div>
                        <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Related tests</h5>
                        {renderImpactTargets(changeImpact.related_tests, "No related tests were recorded.")}
                      </div>
                      <div>
                        <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Related workflows</h5>
                        {renderImpactTargets(changeImpact.related_workflows, "No related workflows were recorded.")}
                      </div>
                    </div>
                  )}
                  {changeImpact.unmapped_paths?.length ? (
                    <p className="mt-3 text-xs text-muted-foreground">Unmapped paths: {changeImpact.unmapped_paths.join(", ")}</p>
                  ) : null}
                  {changeImpact.limitations?.length ? (
                    <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-700 dark:text-amber-200">
                      {changeImpact.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
                    </ul>
                  ) : null}
                </section>
              )}

              <div className="grid gap-4 border-t pt-4 lg:grid-cols-2">
                <section>
                  <h4 className="text-sm font-semibold">Planned validation</h4>
                  {(delivery_plan.validation_steps ?? []).length === 0 ? (
                    <p className="mt-2 text-sm text-muted-foreground">
                      No validation steps were attached to this plan.
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-2 text-sm">
                      {delivery_plan.validation_steps.map((step, index) => (
                        <li
                          key={step.id ?? `${step.kind ?? "validation"}-${index}`}
                          className="rounded-md border bg-muted/20 px-3 py-2"
                        >
                          <span className="font-medium">
                            {step.description ?? "Validation step"}
                          </span>
                          <span className="ml-2 font-mono text-xs text-muted-foreground">
                            {step.kind ?? "unspecified"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section>
                  <h4 className="text-sm font-semibold">Recorded validation</h4>
                  {validationResults.length === 0 ? (
                    <p className="mt-2 text-sm text-muted-foreground">
                      No post-approval validation result was recorded.
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-2 text-sm">
                      {validationResults.map((result) => (
                        <li
                          key={result.step_id}
                          className="rounded-md border bg-muted/20 px-3 py-2"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono text-xs">
                              {result.step_id}
                            </span>
                            <span
                              className={`rounded px-1.5 py-0.5 text-xs font-semibold uppercase ${
                                result.status === "passed"
                                  ? "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"
                                  : result.status === "skipped"
                                    ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                                    : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
                              }`}
                            >
                              {result.status}
                            </span>
                          </div>
                          <p className="mt-1 text-muted-foreground">
                            {result.summary}
                          </p>
                          {result.duration_ms !== undefined && (
                            <p className="mt-1 font-mono text-xs text-muted-foreground">
                              {formatDuration(result.duration_ms)}
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            </div>
          ) : (
            <div className="rounded-xl border p-8 text-center text-muted-foreground">
              No delivery plan or candidate patch was generated for this run.
            </div>
          )}
        </div>
      )}

      {/* Tab: Spans */}
      {activeTab === "spans" && (
        <div
          id="replay-spans"
          role="tabpanel"
          aria-labelledby="replay-spans-tab"
          className="space-y-3"
        >
          {spans.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No node spans recorded yet.
            </p>
          ) : (
            spans.map((span, index) => (
              <details
                key={span.id ?? `${span.name ?? "span"}-${index}`}
                className="rounded-lg border bg-card text-sm"
              >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4 marker:content-none">
                  <div>
                    <span className="font-semibold font-mono">{span.name ?? "Recorded span"}</span>
                    {typeof span.safe_input_summary?.label === "string" &&
                      span.safe_input_summary.label && (
                      <span className="text-xs text-muted-foreground ml-3">
                        {span.safe_input_summary.label}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-4 text-xs font-mono">
                    {span.duration_ms !== undefined && (
                      <span className="text-muted-foreground">
                        {formatDuration(span.duration_ms)}
                      </span>
                    )}
                    <span
                      className={`px-2 py-0.5 rounded font-semibold uppercase ${
                        span.status === "ok"
                          ? "bg-green-100 text-green-700"
                          : "bg-red-100 text-red-700"
                      }`}
                    >
                      {span.status ?? "recorded"}
                    </span>
                  </div>
                </summary>
                {(formatSafeSummary(span.safe_input_summary) ||
                  formatSafeSummary(span.safe_output_summary) ||
                  span.error_type) && (
                  <div className="grid gap-3 border-t bg-muted/20 p-4 text-xs lg:grid-cols-2">
                    {formatSafeSummary(span.safe_input_summary) && (
                      <div>
                        <p className="mb-1 font-semibold">Safe input summary</p>
                        <pre className="max-h-44 overflow-auto whitespace-pre-wrap font-mono text-muted-foreground">
                          {formatSafeSummary(span.safe_input_summary)}
                        </pre>
                      </div>
                    )}
                    {formatSafeSummary(span.safe_output_summary) && (
                      <div>
                        <p className="mb-1 font-semibold">
                          Safe output summary
                        </p>
                        <pre className="max-h-44 overflow-auto whitespace-pre-wrap font-mono text-muted-foreground">
                          {formatSafeSummary(span.safe_output_summary)}
                        </pre>
                      </div>
                    )}
                    {span.error_type && (
                      <div className="lg:col-span-2">
                        <p className="font-semibold text-red-700 dark:text-red-300">
                          {span.error_type}
                        </p>
                        {span.error_message && (
                          <p className="mt-1 text-muted-foreground">
                            {span.error_message}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </details>
            ))
          )}
        </div>
      )}

      {/* Tab: Retrieval Debug */}
      {activeTab === "retrieval" && (
        <div
          id="replay-retrieval"
          role="tabpanel"
          aria-labelledby="replay-retrieval-tab"
          className="space-y-3"
        >
          {evidence.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No evidence items retrieved.
            </p>
          ) : (
            evidence.map((ev, i) => (
              <div
                key={ev.id ?? `${ev.path ?? "evidence"}-${i}`}
                className="rounded-lg border bg-card p-4 flex items-center justify-between text-sm"
              >
                <div>
                  <div className="font-semibold font-mono text-primary">
                    {ev.path || "Unknown source"}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    Source: {ev.source || "repo"} | Type:{" "}
                    {ev.source_type || "repo_file"} | Method:{" "}
                    {ev.retrieval_method || "evidence"}
                  </div>
                  {ev.commit_sha && (
                    <div className="mt-1 text-xs font-mono text-muted-foreground">
                      SHA: {ev.commit_sha.slice(0, 12)}{" "}
                      {ev.state ? `· ${ev.state}` : ""}
                      {ev.truncated ? " · excerpt truncated" : ""}
                    </div>
                  )}
                </div>
                <div className="text-right text-xs font-mono text-muted-foreground">
                  <div>ID: {ev.id}</div>
                  <div>Score: {formatScore(ev.score)}</div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
