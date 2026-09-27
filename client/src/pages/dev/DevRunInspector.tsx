import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { DiffViewer } from "@/components/diff-viewer";
import { Loader } from "@/components/loader";
import { useAuth } from "@clerk/clerk-react";
import { BASE_API_URL } from "@/lib/env";

interface DevRunData {
  run_id: string;
  trace: {
    id: string;
    status: string;
    total_duration_ms?: number;
    started_at?: string;
    completed_at?: string;
    model_usage?: Record<string, any>;
  } | null;
  spans: Array<{
    id: string;
    name: string;
    status: string;
    duration_ms?: number;
    safe_input_summary?: Record<string, any>;
    safe_output_summary?: Record<string, any>;
  }>;
  delivery_plan: {
    id: string;
    title: string;
    rationale: string;
    risk_level: string;
    risk_reasons: string[];
    evidence_ids: string[];
    files: Array<{
      path: string;
      change_type: string;
      original_hash?: string;
      proposed_hash?: string;
      unified_diff: string;
    }>;
    validation_steps: Array<{
      id: string;
      kind: string;
      description: string;
    }>;
  } | null;
  approval: {
    id: string;
    status: string;
    risk_level: string;
    diff_hash: string;
  } | null;
  delivery_result: {
    id: string;
    status: string;
    failure_stage?: string;
    pull_request_url?: string;
  } | null;
  ci_investigation: {
    run_id: number;
    workflow_name?: string;
    head_sha: string;
    current_head_sha?: string;
    run_is_historical: boolean;
    applicability?: string;
    applicability_reason?: string;
    failed_jobs: Array<{ name: string; failed_step?: string }>;
    evidence_ids: string[];
    workflow_evidence_ids: string[];
    changed_file_evidence_ids: string[];
    relevant_paths: string[];
    limitations: string[];
  } | null;
  evidence_count: number;
  evidence: Array<{
    id: string;
    path: string;
    source?: string;
    retrieval_method?: string;
    score?: number;
  }>;
}

interface CapabilitiesData {
  execution: {
    runtime: string;
    docker_available: boolean;
    network_isolation: boolean;
    resource_limits: boolean;
    active_validation: boolean;
    environment_sanitizing: boolean;
    workspace_integrity_checks: boolean;
  };
}

export default function DevRunInspector() {
  const { runId } = useParams<{ runId: string }>();
  const { getToken } = useAuth();
  const [data, setData] = useState<DevRunData | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"spans" | "plan" | "retrieval">("plan");

  useEffect(() => {
    async function fetchInspector() {
      if (!runId) return;
      try {
        setLoading(true);
        const token = await getToken();
        const headers = { Authorization: `Bearer ${token}` };

        const [resRun, resCap] = await Promise.all([
          fetch(`${BASE_API_URL}dev/runs/${runId}`, { headers }),
          fetch(`${BASE_API_URL}system/capabilities`, { headers }),
        ]);

        if (!resRun.ok) {
          throw new Error(`Failed to load dev run inspector: ${resRun.statusText}`);
        }
        const jsonRun = await resRun.json();
        setData(jsonRun);

        if (resCap.ok) {
          const jsonCap = await resCap.json();
          setCapabilities(jsonCap);
        }
      } catch (err: any) {
        setError(err.message || "Error loading telemetry");
      } finally {
        setLoading(false);
      }
    }
    fetchInspector();
  }, [runId, getToken]);

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Loader />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 text-red-500">
        <h2 className="text-xl font-bold">Error loading inspector</h2>
        <p className="mt-2">{error || "No data returned for this run ID."}</p>
        <Link to="/" className="mt-4 inline-block text-blue-500 underline">
          Return to dashboard
        </Link>
      </div>
    );
  }

  const { trace, spans, delivery_plan, approval, delivery_result, evidence, ci_investigation } = data;

  return (
    <div className="mx-auto max-w-6xl p-6 space-y-6 text-foreground">
      {/* Header */}
      <div className="flex items-center justify-between border-b pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">Run Inspector</h1>
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase ${
                trace?.status === "completed" || delivery_result?.status === "success"
                  ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                  : trace?.status === "failed" || delivery_result?.status === "failed"
                    ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                    : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
              }`}
            >
              {trace?.status || delivery_result?.status || "active"}
            </span>
          </div>
          <p className="text-sm text-muted-foreground mt-1 font-mono">Run ID: {runId}</p>
        </div>
        <div className="text-right text-xs text-muted-foreground space-y-1">
          {trace?.total_duration_ms && (
            <div>Duration: <span className="font-semibold text-foreground">{trace.total_duration_ms} ms</span></div>
          )}
          {data.evidence_count > 0 && (
            <div>Evidence Items: <span className="font-semibold text-foreground">{data.evidence_count}</span></div>
          )}
        </div>
      </div>

      {/* Execution Sandbox Trust Signal */}
      {capabilities && (
        <div className="rounded-lg border bg-muted/30 p-3 text-xs flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 font-mono">
            <span className="font-semibold text-foreground">Execution Sandbox:</span>
            <span className="rounded bg-primary/10 px-2 py-0.5 text-primary font-bold">
              {capabilities.execution.runtime}
            </span>
          </div>
          <div className="flex items-center gap-4 text-muted-foreground">
            <span>
              Network Isolation:{" "}
              <strong className={capabilities.execution.network_isolation ? "text-green-600" : "text-amber-600"}>
                {capabilities.execution.network_isolation ? "Disabled (Isolated)" : "Restricted Local"}
              </strong>
            </span>
            <span>
              Sanitized Env: <strong className="text-foreground">Stripped</strong>
            </span>
            <span>
              Integrity Checks: <strong className="text-green-600">Active</strong>
            </span>
          </div>
        </div>
      )}

      {ci_investigation && (
        <section className="rounded-lg border border-cyan-500/25 bg-cyan-500/5 p-4 text-sm">
          <h2 className="font-semibold text-cyan-700 dark:text-cyan-200">GitHub Actions investigation</h2>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 text-muted-foreground">
            <p>Workflow: <span className="text-foreground">{ci_investigation.workflow_name ?? "Unknown"}</span></p>
            <p>Run: <span className="text-foreground">#{ci_investigation.run_id}</span></p>
            <p>Failed SHA: <span className="font-mono text-foreground">{ci_investigation.head_sha.slice(0, 12)}</span></p>
            <p>Current SHA: <span className="font-mono text-foreground">{ci_investigation.current_head_sha?.slice(0, 12) ?? "Unavailable"}</span></p>
            <p>Status: <span className="text-foreground">{ci_investigation.applicability?.replaceAll("_", " ") ?? "not determined"}</span></p>
            <p>Failed jobs: <span className="text-foreground">{ci_investigation.failed_jobs.length}</span></p>
          </div>
          {ci_investigation.applicability_reason && <p className="mt-3 text-muted-foreground">{ci_investigation.applicability_reason}</p>}
          {ci_investigation.limitations.length > 0 && <p className="mt-2 text-amber-700 dark:text-amber-200">Limited visibility: {ci_investigation.limitations.join(" ")}</p>}
        </section>
      )}

      {/* Navigation Tabs */}
      <div className="flex gap-4 border-b text-sm font-medium">
        <button
          onClick={() => setActiveTab("plan")}
          className={`pb-2 border-b-2 transition-colors ${
            activeTab === "plan"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Delivery Plan & Patch
        </button>
        <button
          onClick={() => setActiveTab("spans")}
          className={`pb-2 border-b-2 transition-colors ${
            activeTab === "spans"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Graph Spans & Tracing ({spans.length})
        </button>
        <button
          onClick={() => setActiveTab("retrieval")}
          className={`pb-2 border-b-2 transition-colors ${
            activeTab === "retrieval"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Retrieval Debug View ({evidence.length})
        </button>
      </div>

      {/* Tab: Delivery Plan & Patch */}
      {activeTab === "plan" && (
        <div className="space-y-6">
          {delivery_plan ? (
            <div className="rounded-xl border bg-card p-5 space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-semibold">{delivery_plan.title}</h3>
                  <p className="text-sm text-muted-foreground mt-1">{delivery_plan.rationale}</p>
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
              {delivery_plan.risk_reasons.length > 0 && (
                <div className="rounded-lg bg-muted/50 p-3 text-xs">
                  <span className="font-semibold block mb-1">Risk Factors:</span>
                  <ul className="list-disc list-inside space-y-0.5 text-muted-foreground">
                    {delivery_plan.risk_reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Approval status */}
              {approval && (
                <div className="flex items-center justify-between text-xs border-t pt-3 font-mono">
                  <span>Approval Binding: <span className="text-foreground">{approval.status}</span></span>
                  <span>Diff Hash: <span className="text-foreground">{approval.diff_hash.slice(0, 16)}...</span></span>
                </div>
              )}

              {/* Proposed Files & Diff */}
              <div className="space-y-4 pt-2">
                <h4 className="text-sm font-semibold">Proposed Candidate Edits ({delivery_plan.files.length} file(s))</h4>
                {delivery_plan.files.map((file, idx) => (
                  <div key={idx} className="rounded-lg border bg-background overflow-hidden">
                    <div className="px-4 py-2 border-b bg-muted/40 flex items-center justify-between text-xs font-mono">
                      <span>{file.path} ({file.change_type})</span>
                      {file.original_hash && (
                        <span className="text-muted-foreground">Orig SHA: {file.original_hash.slice(0, 8)}</span>
                      )}
                    </div>
                    <DiffViewer patch={file.unified_diff} />
                  </div>
                ))}
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
        <div className="space-y-3">
          {spans.length === 0 ? (
            <p className="text-sm text-muted-foreground">No node spans recorded yet.</p>
          ) : (
            spans.map((span) => (
              <div key={span.id} className="rounded-lg border bg-card p-4 flex items-center justify-between text-sm">
                <div>
                  <span className="font-semibold font-mono">{span.name}</span>
                  {span.safe_input_summary?.label && (
                    <span className="text-xs text-muted-foreground ml-3">
                      {span.safe_input_summary.label}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-4 text-xs font-mono">
                  {span.duration_ms !== undefined && (
                    <span className="text-muted-foreground">{span.duration_ms} ms</span>
                  )}
                  <span
                    className={`px-2 py-0.5 rounded font-semibold uppercase ${
                      span.status === "ok" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                    }`}
                  >
                    {span.status}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab: Retrieval Debug */}
      {activeTab === "retrieval" && (
        <div className="space-y-3">
          {evidence.length === 0 ? (
            <p className="text-sm text-muted-foreground">No evidence items retrieved.</p>
          ) : (
            evidence.map((ev, i) => (
              <div key={i} className="rounded-lg border bg-card p-4 flex items-center justify-between text-sm">
                <div>
                  <div className="font-semibold font-mono text-primary">{ev.path}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    Source: {ev.source || "repo"} | Method: {ev.retrieval_method || "rag"}
                  </div>
                </div>
                <div className="text-right text-xs font-mono text-muted-foreground">
                  <div>ID: {ev.id}</div>
                  {ev.score !== undefined && <div>Score: {ev.score.toFixed(3)}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
