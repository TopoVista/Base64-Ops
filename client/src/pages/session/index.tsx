import ChatInterface from "@/components/chat";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { IDEWorkspace } from "@/components/ide-workspace";
import {
  decideSessionApproval,
  getSessionBySlug,
  getSessionCodeFile,
  getSessionCodeFiles,
  getSessionDependencyGraph,
  getSessionRagSources,
  getSessionRecentCommits,
  proposeSessionCodeEdit,
  refreshLatestSessionCi,
  reindexSessionRag,
  streamDiagnostic,
  updateSessionDeliveryCommitMessage,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EvidenceItem } from "@/types/agent.type";
import type { SingleSessionResponse } from "@/types/session.type";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Code2,
  FileCode2,
  FileSearch,
  GitBranch,
  GitCommitHorizontal,
  GitPullRequest,
  RefreshCw,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useEffect, useMemo, useState } from "react";
import { Streamdown } from "streamdown";
import { mermaid } from "@streamdown/mermaid";

const sections = [
  "overview",
  "assistant",
  "pipelines",
  "investigation",
  "knowledge",
  "code",
  "changes",
  "history",
] as const;
type Section = (typeof sections)[number];
const label = (value?: string | null) =>
  value ? value.replaceAll("_", " ") : "Unavailable";
const shortSha = (sha?: string | null) =>
  sha ? sha.slice(0, 12) : "Unavailable";

function PageTitle({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-6 py-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
          {description}
        </p>
      </div>
      {actions}
    </header>
  );
}
function Panel({
  title,
  icon: Icon,
  children,
  className,
}: {
  title: string;
  icon?: typeof Activity;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-xl border border-border bg-card p-4 shadow-sm",
        className,
      )}
    >
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
        {Icon ? <Icon className="size-4 text-primary" /> : null}
        {title}
      </div>
      {children}
    </section>
  );
}
function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border bg-muted/25 p-4 text-sm leading-6 text-muted-foreground">
      {children}
    </p>
  );
}
function EvidenceCard({ item }: { item: EvidenceItem }) {
  const ci = item.sourceType === "ci_log";
  return (
    <article className="rounded-lg border border-border bg-background p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">
          {ci ? "GitHub Actions" : item.sourceType}
        </Badge>
        {item.metadata?.state === "failed_run" ? (
          <Badge variant="secondary">Historical CI evidence</Badge>
        ) : null}
        {item.metadata?.truncated ? (
          <Badge variant="secondary">Excerpt truncated</Badge>
        ) : null}
      </div>
      <p className="mt-2 font-medium">{item.title}</p>
      {ci ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {item.metadata?.workflow_name ?? "Workflow"} ·{" "}
          {item.metadata?.job_name ?? "job"}
          {item.metadata?.failed_step ? ` · ${item.metadata.failed_step}` : ""}
        </p>
      ) : null}
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
        {item.excerpt}
      </p>
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {item.path ? <span>{item.path}</span> : null}
        {item.commitSha ? (
          <span className="font-mono">{shortSha(item.commitSha)}</span>
        ) : null}
        {item.metadata?.html_url ? (
          <a
            className="inline-flex items-center gap-1 text-primary hover:underline"
            href={item.metadata.html_url}
            target="_blank"
            rel="noreferrer"
          >
            Open in GitHub <ArrowUpRight className="size-3" />
          </a>
        ) : null}
      </div>
    </article>
  );
}
function ContextBar({ data }: { data: SingleSessionResponse }) {
  const snapshot = data.commandCenter;
  const repo = snapshot?.repository;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-border bg-background/90 px-6 py-3 text-xs text-muted-foreground">
      <span className="font-medium text-foreground">
        {data.session.title ?? data.session.repoName ?? "Operation"}
      </span>
      <span>
        {repo?.name ?? data.session.repoName ?? "Repository not connected"}
      </span>
      <span>
        <GitBranch className="mr-1 inline size-3" />
        {repo?.branch ??
          data.session.branchName ??
          data.session.defaultBranch ??
          "branch unavailable"}
      </span>
      <span className="font-mono">SHA {shortSha(repo?.commit_sha)}</span>
      <Badge
        variant="outline"
        className={
          data.session.indexStatus === "ready"
            ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
            : ""
        }
      >
        Index {data.session.indexStatus ?? "not started"}
      </Badge>
      <Link
        to={`/session/${data.session.slugId}/pipelines`}
        state={{ autoAnalyzeCi: true }}
        className="ml-auto inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-1 font-medium text-primary hover:bg-primary/10"
      >
        <Workflow className="size-3" />
        Analyze CI failures
      </Link>
    </div>
  );
}
function Overview({ data }: { data: SingleSessionResponse }) {
  const s = data.commandCenter;
  const ci = s?.ci;
  const approval = s?.approvals?.find((a) => a.status === "pending");
  return (
    <>
      <PageTitle
        title="Overview"
        description="The current operational picture for this repository, backed by the latest persisted investigation."
      />
      <div className="grid gap-4 p-6 xl:grid-cols-3">
        <Panel title="Repository health" icon={CheckCircle2}>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Repository</dt>
              <dd className="truncate">
                {s?.repository?.name ??
                  data.session.repoName ??
                  "Not connected"}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Current SHA</dt>
              <dd className="font-mono">
                {shortSha(s?.repository?.commit_sha)}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted-foreground">Index</dt>
              <dd>{data.session.indexStatus ?? "not started"}</dd>
            </div>
          </dl>
        </Panel>
        <Panel title="Latest investigation" icon={FileSearch}>
          {s?.investigation ? (
            <>
              <p className="text-sm font-medium">
                {label(s.investigation.status)}
              </p>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {s.investigation.summary}
              </p>
              <Link
                className="mt-3 inline-block text-sm text-primary hover:underline"
                to={`/session/${data.session.slugId}/investigation`}
              >
                Review evidence and hypotheses
              </Link>
            </>
          ) : (
            <Empty>Run an investigation to create a traceable diagnosis.</Empty>
          )}
        </Panel>
        <Panel title="Pending approval" icon={ShieldCheck}>
          {approval ? (
            <>
              <p className="text-sm font-medium">{approval.summary}</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Risk: {approval.riskLevel ?? "approval required"}
              </p>
              <Link
                className="mt-3 inline-block text-sm text-primary hover:underline"
                to={`/session/${data.session.slugId}/changes`}
              >
                Review exact change
              </Link>
            </>
          ) : (
            <Empty>
              No approval is pending. Read-only investigation never needs
              approval.
            </Empty>
          )}
        </Panel>
        <Panel title="CI signal" icon={Workflow} className="xl:col-span-2">
          {ci ? (
            <div className="grid gap-2 text-sm md:grid-cols-2">
              <p>
                <span className="text-muted-foreground">Workflow:</span>{" "}
                {ci.workflow_name ?? "Unknown"} · run #{ci.run_id}
              </p>
              <p>
                <span className="text-muted-foreground">Run SHA:</span>{" "}
                <span className="font-mono">{shortSha(ci.head_sha)}</span>
              </p>
              <p>
                <span className="text-muted-foreground">Failed jobs:</span>{" "}
                {ci.failed_jobs.map((job) => job.name).join(", ") || "None"}
              </p>
              <p>
                <span className="text-muted-foreground">Current status:</span>{" "}
                {label(ci.applicability)}
              </p>
            </div>
          ) : (
            <Empty>
              No GitHub Actions failure has been investigated in this session.
            </Empty>
          )}
        </Panel>
        <Panel title="Next safe actions" icon={Activity}>
          {data.session.repoUrl ? (
            <div className="flex flex-wrap gap-2">
              <Link
                className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
                to={`/session/${data.session.slugId}/assistant`}
              >
                Ask the assistant
              </Link>
              <Link
                className="rounded-md border border-border px-3 py-2 text-sm font-medium hover:bg-muted"
                to={`/session/${data.session.slugId}/knowledge`}
              >
                Check knowledge index
              </Link>
            </div>
          ) : (
            <Empty>Connect a repository before beginning a session.</Empty>
          )}
        </Panel>
      </div>
    </>
  );
}
function PipelinesLegacy({ data }: { data: SingleSessionResponse }) {
  const s = data.commandCenter;
  const ci = s?.ci;
  const ciEvidence = (s?.evidence ?? []).filter(
    (e) => e.sourceType === "ci_log",
  );
  return (
    <>
      <PageTitle
        title="Pipelines"
        description="Read-only GitHub Actions evidence associated with the selected run. Workflow commands are displayed as repository data and are never executed by Base64."
      />
      <div className="grid gap-4 p-6 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <div className="space-y-4">
          <Panel title="Run and job status" icon={Workflow}>
            {ci ? (
              <>
                <div className="mb-4 rounded-lg border border-border bg-muted/25 p-3 text-sm">
                  <p className="font-medium">
                    {ci.workflow_name ?? "GitHub Actions"} · run #{ci.run_id}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    Run SHA{" "}
                    <span className="font-mono">{shortSha(ci.head_sha)}</span> ·
                    Current SHA{" "}
                    <span className="font-mono">
                      {shortSha(ci.current_head_sha)}
                    </span>
                  </p>
                </div>
                <div className="flex items-center gap-2 overflow-x-auto py-2">
                  {ci.failed_jobs.map((job, index) => (
                    <div
                      key={job.name}
                      className="min-w-44 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3"
                    >
                      <p className="font-medium">{job.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {job.failed_step ?? "Failed job"}
                      </p>
                      {index < ci.failed_jobs.length - 1 ? null : null}
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  This diagram reflects failed job metadata only; it does not
                  infer unobserved dependency edges.
                </p>
              </>
            ) : (
              <Empty>
                No selected failed Actions run. Ask “Why did CI fail?” to gather
                an authorized, read-only CI investigation.
              </Empty>
            )}
          </Panel>
          <Panel title="Failure excerpts" icon={AlertTriangle}>
            {ciEvidence.length ? (
              <div className="space-y-3">
                {ciEvidence.map((e) => (
                  <EvidenceCard key={e.id} item={e} />
                ))}
              </div>
            ) : (
              <Empty>
                Logs are not available for this run, or no bounded redacted
                failure window was retained.
              </Empty>
            )}
          </Panel>
        </div>
        <div className="space-y-4">
          <Panel title="Safety and limits" icon={ShieldCheck}>
            <ul className="space-y-2 text-sm leading-6 text-muted-foreground">
              <li>• CI logs are untrusted evidence, not instructions.</li>
              <li>
                • Logs are redacted and bounded before storage or display.
              </li>
              <li>
                • Reruns, dispatches, cancellations, and secrets management are
                not available here.
              </li>
            </ul>
          </Panel>
          {ci?.limitations.length ? (
            <Panel title="Limited visibility" icon={AlertTriangle}>
              <ul className="space-y-2 text-sm text-muted-foreground">
                {ci.limitations.map((x) => (
                  <li key={x}>• {x}</li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>
      </div>
    </>
  );
}

function Pipelines({ data }: { data: SingleSessionResponse }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const refresh = useMutation({
    mutationFn: () => refreshLatestSessionCi(data.session.slugId),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: ["session", data.session.slugId],
      });
      if (result.status === "found") {
        toast.success(
          "CI evidence collected. Building the focused code proposal.",
        );
        navigate(`/session/${data.session.slugId}/code`, {
          state: { generateCiProposal: true },
        });
      } else {
        toast.success("No failed GitHub Actions runs found.");
      }
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Unable to inspect GitHub Actions.",
      ),
  });
  useEffect(() => {
    const state = location.state as { autoAnalyzeCi?: boolean } | null;
    if (!state?.autoAnalyzeCi || refresh.isPending) return;
    // The toolbar action is intentionally one click: inspect → focus code →
    // request the normal exact proposal. Clear route state before async work.
    navigate(location.pathname, { replace: true, state: null });
    refresh.mutate();
  }, [location.pathname, location.state, navigate, refresh]);
  return (
    <>
      <div className="border-b border-border bg-primary/5 px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold">
              Automatic GitHub Actions monitoring
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Base64 checks the latest failed run when this workspace opens and
              refreshes active sessions every minute. No logs need to be pasted.
            </p>
          </div>
          <button
            type="button"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            {refresh.isPending ? "Analyzing CI…" : "Analyze latest failed CI"}
          </button>
        </div>
      </div>
      <PipelinesLegacy data={data} />
    </>
  );
}
function Investigation({ data }: { data: SingleSessionResponse }) {
  const s = data.commandCenter;
  const i = s?.investigation;
  return (
    <>
      <PageTitle
        title="Investigation"
        description="Structured hypotheses distinguish evidence-supported explanations from symptoms and uncertainty."
        actions={
          i ? (
            <Link
              className="text-sm text-primary hover:underline"
              to={`/dev/runs/${i.run_id}`}
            >
              Open run replay
            </Link>
          ) : null
        }
      />
      <div className="grid gap-4 p-6 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <Panel title="Diagnosis" icon={FileSearch}>
          {i ? (
            <>
              <Badge variant="outline">{label(i.status)}</Badge>
              <p className="mt-3 leading-7 text-muted-foreground">
                {i.summary}
              </p>
              <div className="mt-5 space-y-3">
                {i.hypotheses.map((h) => (
                  <article
                    key={h.id}
                    className="rounded-lg border border-border p-3"
                  >
                    <div className="flex flex-wrap justify-between gap-2">
                      <p className="font-medium">{h.title}</p>
                      <Badge variant="secondary">{h.status}</Badge>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      {h.explanation}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Evidence:{" "}
                      {h.evidence_ids.length
                        ? h.evidence_ids.join(", ")
                        : "none"}
                    </p>
                  </article>
                ))}
              </div>
            </>
          ) : (
            <Empty>No diagnosis has been persisted yet.</Empty>
          )}
        </Panel>
        <div className="space-y-4">
          <Panel title="Evidence" icon={BookOpen}>
            {s?.evidence?.length ? (
              <p className="text-sm text-muted-foreground">
                {s.evidence.length} retained evidence item
                {s.evidence.length === 1 ? "" : "s"} supports this run.
              </p>
            ) : (
              <Empty>No evidence retained.</Empty>
            )}
          </Panel>
          <Panel title="Limitations" icon={AlertTriangle}>
            {i?.limitations.length ? (
              <ul className="space-y-2 text-sm text-muted-foreground">
                {i.limitations.map((x) => (
                  <li key={x}>• {x}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                No limitations were recorded.
              </p>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
function Knowledge({
  data,
  slugId,
}: {
  data: SingleSessionResponse;
  slugId: string;
}) {
  const qc = useQueryClient();
  const {
    data: sourceData,
    isPending: sourcesPending,
    error: sourcesError,
  } = useQuery({
    queryKey: ["session-rag-sources", slugId],
    queryFn: () => getSessionRagSources(slugId),
    retry: false,
  });
  const sources = sourceData?.sources ?? [];
  const repoSources = sources.filter((source) => source.kind === "repo");
  const mutation = useMutation({
    mutationFn: () => reindexSessionRag(slugId),
    onSuccess: (result) => {
      if (result.success)
        toast.success(
          `Indexed ${result.indexed} chunks from ${repoSources.length || "repository"} source files.`,
        );
      else toast.error(result.reason ?? "Indexing did not complete.");
      void qc.invalidateQueries({ queryKey: ["session", slugId] });
      void qc.invalidateQueries({ queryKey: ["session-rag-sources", slugId] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Repository indexing request failed.",
      ),
  });
  const latestRepositoryMap = data.commandCenter?.repository;
  return (
    <>
      <PageTitle
        title="Knowledge"
        description="Repository retrieval, runbooks, and citations available to evidence-first investigations."
        actions={
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            <RefreshCw
              className={cn("size-4", mutation.isPending && "animate-spin")}
            />
            {mutation.isPending ? "Indexing…" : "Reindex repository"}
          </button>
        }
      />
      <div className="grid gap-4 p-6 lg:grid-cols-2">
        <Panel title="Index and repository map" icon={BookOpen}>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Index status</dt>
              <dd>{data.session.indexStatus ?? "not started"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Indexed source files</dt>
              <dd>{sourcesPending ? "Checking…" : repoSources.length}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Indexed chunks</dt>
              <dd>
                {sources.reduce(
                  (total, source) => total + (source.count ?? 0),
                  0,
                )}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">
                CI files in last investigation
              </dt>
              <dd>{latestRepositoryMap?.ci_files?.length ?? 0}</dd>
            </div>
          </dl>
          {data.session.indexError ? (
            <p className="mt-4 rounded-lg border border-amber-300/30 bg-amber-300/5 p-3 text-sm leading-6 text-amber-900 dark:text-amber-100">
              {data.session.indexError}
            </p>
          ) : null}
        </Panel>
        <Panel title="Retrieved sources" icon={FileCode2}>
          {sourcesError ? (
            <Empty>
              Unable to retrieve indexed sources. The server response above
              contains the actionable reason.
            </Empty>
          ) : sources.length ? (
            <div className="max-h-[28rem] space-y-2 overflow-auto">
              {sources.map((source) => (
                <div
                  key={`${source.kind}:${source.source}`}
                  className="rounded-lg border border-border bg-background p-3 text-sm"
                >
                  <p className="truncate font-mono text-xs">{source.source}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {source.count ?? 0} chunk{source.count === 1 ? "" : "s"} ·{" "}
                    {source.kind}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <Empty>
              {sourcesPending
                ? "Loading indexed sources…"
                : "No repository chunks are stored yet. Reindex will show a precise failure reason if Atlas or GitHub access is unavailable."}
            </Empty>
          )}
        </Panel>
      </div>
    </>
  );
}
function ReadOnlyCodeLegacy({ data }: { data: SingleSessionResponse }) {
  const slugId = data.session.slugId;
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const {
    data: tree,
    isPending: treePending,
    error: treeError,
  } = useQuery({
    queryKey: ["session-code-tree", slugId],
    queryFn: () => getSessionCodeFiles(slugId),
    retry: false,
  });
  const {
    data: file,
    isPending: filePending,
    error: fileError,
  } = useQuery({
    queryKey: ["session-code-file", slugId, selectedPath],
    queryFn: () => getSessionCodeFile(slugId, selectedPath ?? ""),
    enabled: Boolean(selectedPath),
    retry: false,
  });
  const files = tree?.files ?? [];
  return (
    <>
      <PageTitle
        title="Code"
        description="Browse the selected session's cloned repository through a bounded, tenant-scoped read-only API. Content is centrally redacted before display; edits remain proposal-only through the approval pipeline."
      />
      <div className="grid gap-4 p-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <Panel title="Repository files" icon={Code2}>
          {treePending ? (
            <p className="text-sm text-muted-foreground">
              Preparing read-only workspace…
            </p>
          ) : treeError ? (
            <Empty>
              Repository files are unavailable. Connect GitHub or reopen a
              session with repository access.
            </Empty>
          ) : files.length ? (
            <div className="max-h-[34rem] space-y-1 overflow-auto">
              {files.map((item) => (
                <button
                  type="button"
                  key={item.path}
                  onClick={() => setSelectedPath(item.path)}
                  className={cn(
                    "block w-full truncate rounded px-2 py-1.5 text-left font-mono text-xs hover:bg-muted",
                    selectedPath === item.path && "bg-muted text-foreground",
                  )}
                  title={item.path}
                >
                  {item.path}
                </button>
              ))}
            </div>
          ) : (
            <Empty>No supported source files were found.</Empty>
          )}
        </Panel>
        <Panel title="Read-only file view" icon={FileCode2}>
          {filePending ? (
            <p className="text-sm text-muted-foreground">
              Loading redacted file content…
            </p>
          ) : fileError ? (
            <Empty>The selected file could not be read safely.</Empty>
          ) : file ? (
            <>
              <div className="mb-3 flex flex-wrap justify-between gap-2 text-xs">
                <p className="font-mono">{file.path}</p>
                <span className="text-muted-foreground">
                  SHA {shortSha(file.headSha)}
                  {file.truncated ? " · truncated" : ""}
                </span>
              </div>
              <pre className="max-h-[34rem] overflow-auto rounded-lg bg-zinc-950 p-4 text-xs leading-6 text-zinc-100">
                {file.content}
              </pre>
              <p className="mt-3 text-xs text-muted-foreground">
                This is a redacted read-only view. Ask the assistant to create a
                reviewable proposal; no browser edit can bypass validation, risk
                classification, exact approval, or delivery controls.
              </p>
            </>
          ) : (
            <Empty>
              Select a file to inspect it. This page never writes directly to
              the repository.
            </Empty>
          )}
        </Panel>
      </div>
    </>
  );
}
function Changes({ data }: { data: SingleSessionResponse }) {
  const s = data.commandCenter;
  const p = s?.deliveryPlan;
  return (
    <>
      <PageTitle
        title="Changes"
        description="Every remediation remains a reviewable proposal until its exact plan is approved."
      />
      <div className="grid gap-4 p-6 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <Panel title="Delivery plan" icon={GitPullRequest}>
          {p ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-medium">{p.title}</p>
                <Badge variant="outline">{p.risk_level} risk</Badge>
              </div>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {p.rationale}
              </p>
              <div className="mt-4 space-y-3">
                {p.files.map((f) => (
                  <article
                    key={f.path}
                    className="rounded-lg border border-border p-3"
                  >
                    <p className="font-mono text-sm">{f.path}</p>
                    <pre className="mt-2 max-h-64 overflow-auto rounded bg-muted p-3 text-xs">
                      {f.unified_diff}
                    </pre>
                  </article>
                ))}
              </div>
            </>
          ) : (
            <Empty>
              No candidate change exists. An investigation may recommend a
              remediation without creating an executable patch.
            </Empty>
          )}
        </Panel>
        <Panel title="Approval state" icon={ShieldCheck}>
          {s?.approvals?.length ? (
            <div className="space-y-3">
              {s.approvals.map((a) => (
                <div key={a.id} className="rounded-lg border border-border p-3">
                  <p className="font-medium">{a.summary}</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {a.status ?? "pending"} · {a.riskLevel ?? a.risk}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <Empty>
              No approval record. A read-only CI investigation cannot authorize
              a mutation.
            </Empty>
          )}
        </Panel>
      </div>
    </>
  );
}
function History({ data }: { data: SingleSessionResponse }) {
  const s = data.commandCenter;
  return (
    <>
      <PageTitle
        title="History"
        description="Persisted run timeline and delivery outcomes. Replay is historical and never re-executes mutations."
      />
      <div className="grid gap-4 p-6 lg:grid-cols-2">
        <Panel title="Run timeline" icon={Clock3}>
          {s?.timeline?.length ? (
            <ol className="space-y-3 border-l border-border pl-4">
              {s.timeline.map((t) => (
                <li key={t.id}>
                  <p className="text-sm font-medium">{t.label ?? t.name}</p>
                  {t.detail ? (
                    <p className="text-xs text-muted-foreground">{t.detail}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : (
            <Empty>No persisted run timeline yet.</Empty>
          )}
        </Panel>
        <Panel title="Delivery outcome" icon={GitCommitHorizontal}>
          {s?.deliveryResult ? (
            <div className="space-y-2 text-sm">
              <p>Status: {s.deliveryResult.status}</p>
              {s.deliveryResult.commit_sha ? (
                <p className="font-mono">{s.deliveryResult.commit_sha}</p>
              ) : null}
              {s.deliveryResult.pull_request_url ? (
                <a
                  className="text-primary hover:underline"
                  href={s.deliveryResult.pull_request_url}
                >
                  Open draft pull request
                </a>
              ) : null}
            </div>
          ) : (
            <Empty>No delivery has been executed for this session.</Empty>
          )}
        </Panel>
      </div>
    </>
  );
}
function SessionPage() {
  const { slugid, section: rawSection } = useParams();
  const section: Section = sections.includes(rawSection as Section)
    ? (rawSection as Section)
    : "overview";
  const { data, isPending } = useQuery({
    queryKey: ["session", slugid],
    queryFn: () => getSessionBySlug(slugid ?? ""),
    enabled: Boolean(slugid),
    retry: false,
  });
  if (isPending)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Loading session workspace…
      </div>
    );
  if (!data) return <div className="p-6">Session not found.</div>;
  const props = { data };
  return (
    <main className="flex h-full min-h-0 w-full flex-col overflow-hidden">
      <ContextBar data={data} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {section === "overview" ? (
          <Overview {...props} />
        ) : section === "assistant" ? (
          <div className="h-full min-h-[calc(100dvh-7rem)]">
            <ChatInterface
              workspaceMode
              key={data.session.slugId}
              initialMessages={data.messages}
              sessionTitle={data.session.title ?? "Untitled Session"}
              slugId={data.session.slugId}
              repoUrl={data.session.repoUrl ?? ""}
              defaultBranch={data.session.defaultBranch ?? "main"}
              branchName={data.session.branchName}
              initialCommandCenter={data.commandCenter}
              initialIndexStatus={data.session.indexStatus}
              initialIndexError={data.session.indexError}
            />
          </div>
        ) : section === "pipelines" ? (
          <Pipelines {...props} />
        ) : section === "investigation" ? (
          <Investigation {...props} />
        ) : section === "knowledge" ? (
          <Knowledge {...props} slugId={data.session.slugId} />
        ) : section === "code" ? (
          <Code {...props} />
        ) : section === "changes" ? (
          <Changes {...props} />
        ) : (
          <History {...props} />
        )}
      </div>
    </main>
  );
}


// Kept temporarily for backwards-compatible deep links while the active Code
// route uses IDEWorkspace below.
void ReadOnlyCodeLegacy;

function ReadOnlyCode({ data }: { data: SingleSessionResponse }) {
  const slugId = data.session.slugId;
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [focusedCiRun, setFocusedCiRun] = useState<number | null>(null);
  const ci = data.commandCenter?.ci;
  const deliveryPlan = data.commandCenter?.deliveryPlan;
  const pendingApproval = data.commandCenter?.approvals?.find(
    (approval) => approval.status === "pending" && approval.deliveryPlanId,
  );
  const {
    data: tree,
    isPending: treePending,
    error: treeError,
  } = useQuery({
    queryKey: ["session-code-tree", slugId],
    queryFn: () => getSessionCodeFiles(slugId),
    retry: false,
  });
  const {
    data: file,
    isPending: filePending,
    error: fileError,
  } = useQuery({
    queryKey: ["session-code-file", slugId, selectedPath],
    queryFn: () => getSessionCodeFile(slugId, selectedPath ?? ""),
    enabled: Boolean(selectedPath),
    retry: false,
  });
  const { data: commitData, isPending: commitsPending } = useQuery({
    queryKey: ["session-code-commits", slugId],
    queryFn: () => getSessionRecentCommits(slugId),
    retry: false,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  // Opening Code should present source immediately. A file picker remains
  // available, but requiring an extra click made a working editor look empty.
  useEffect(() => {
    // General code browsing can open the first source. A CI investigation may
    // not: it must wait for a concrete evidence-backed path rather than show
    // an arbitrary OpenAPI or metadata file as though it caused the failure.
    if (!ci && !selectedPath && tree?.files.length)
      setSelectedPath(tree.files[0].path);
  }, [ci, selectedPath, tree?.files]);
  useEffect(() => {
    if (!ci || !tree?.files.length || focusedCiRun === ci.run_id) return;
    const relevant = (ci.relevant_paths ?? []).find((path) =>
      tree.files.some((fileItem) => fileItem.path === path),
    );
    if (relevant) setSelectedPath(relevant);
    else setSelectedPath(null);
    setFocusedCiRun(ci.run_id);
  }, [ci, focusedCiRun, tree?.files]);
  useEffect(() => {
    // A generated plan is the authoritative file target for the review split.
    // Its selected source must never remain on a previously opened unrelated file.
    const plannedPath = deliveryPlan?.files[0]?.path;
    if (
      plannedPath &&
      tree?.files.some((item) => item.path === plannedPath) &&
      selectedPath !== plannedPath
    ) {
      setSelectedPath(plannedPath);
    }
  }, [deliveryPlan?.id, deliveryPlan?.files, selectedPath, tree?.files]);
  useEffect(() => {
    if (file) {
      setDraft(file.content);
      setCommitMessage(`Update ${file.path}`);
    }
  }, [file]);
  useEffect(() => {
    if (deliveryPlan) setCommitMessage(deliveryPlan.title);
  }, [deliveryPlan?.id, deliveryPlan?.title]);
  const propose = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("Select a file before creating a proposal.");
      return proposeSessionCodeEdit(slugId, {
        path: file.path,
        expectedOriginalHash: file.contentHash,
        proposedContent: draft,
        commitMessage: commitMessage.trim() || undefined,
      });
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["session", slugId] });
      toast.success(
        `Exact change is ready for approval (${result.approval.riskLevel} risk).`,
      );
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Unable to create a reviewable proposal.",
      ),
  });
  const proposeCiFix = useMutation({
    mutationFn: async () => {
      // Collect CI log excerpts from retained evidence.
      const ciLog = (data.commandCenter?.evidence ?? [])
        .filter((item) => item.sourceType === "ci_log")
        .map((item) => item.excerpt)
        .join("\n\n")
        .slice(0, 100_000);
      if (!ciLog)
        throw new Error(
          "No CI failure evidence is retained for this session. Use Pipelines → Analyze latest failed CI to collect evidence first, then return here.",
        );
      // Use the currently selected file as a hint if it is a known CI-relevant
      // path. Otherwise let the backend pick the best target from evidence.
      const targetPath =
        file && ci?.relevant_paths?.includes(file.path) ? file.path : undefined;
      await streamDiagnostic(
        {
          sessionSlugId: slugId,
          failedLog: ciLog,
          gitDiff: "",
          filePath: targetPath,
          originalContent: targetPath && file ? file.content : undefined,
          generateFix: true,
        },
        (event, eventData) => {
          if (event === "error") {
            const msg = String(eventData.message ?? "");
            // Surface actionable messages; suppress noisy infrastructure errors.
            if (msg.toLowerCase().includes("api configuration") || msg.toLowerCase().includes("openai")) {
              throw new Error(
                "The AI model is not configured. Ask your workspace administrator to set OPENAI_API_KEY on the backend, then retry.",
              );
            }
            throw new Error(msg || "CI proposal generation failed. Check API configuration and try again.");
          }
        },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["session", slugId] });
      toast.success(
        "CI remediation proposal ready. Review the exact diff and apply changes.",
      );
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Unable to create a CI remediation proposal.",
      ),
  });
  const deliver = useMutation({
    mutationFn: async () => {
      if (!pendingApproval)
        throw new Error("No exact pending approval is available.");
      if (
        deliveryPlan &&
        commitMessage.trim() &&
        commitMessage.trim() !== deliveryPlan.title
      ) {
        await updateSessionDeliveryCommitMessage(
          slugId,
          deliveryPlan.id,
          commitMessage.trim(),
        );
        return { approvalUpdated: true };
      }
      return decideSessionApproval(slugId, pendingApproval.id, {
        decision: "approve",
        note: "Approved from the Code workspace",
      });
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["session", slugId] });
      void queryClient.invalidateQueries({
        queryKey: ["session-code-commits", slugId],
      });
      if ("approvalUpdated" in result && result.approvalUpdated) {
        toast.success(
          "Commit message updated. Review the replacement approval, then apply and push the exact plan.",
        );
      } else {
        toast.success(
          "Approved delivery completed. GitHub Actions will report the new commit status when the run finishes.",
        );
      }
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Approved delivery could not be completed.",
      ),
  });
  useEffect(() => {
    const state = location.state as { generateCiProposal?: boolean } | null;
    if (
      !state?.generateCiProposal ||
      !ci ||
      deliveryPlan ||
      proposeCiFix.isPending
    )
      return;
    navigate(location.pathname, { replace: true, state: null });
    proposeCiFix.mutate();
  }, [
    ci,
    deliveryPlan,
    location.pathname,
    location.state,
    navigate,
    proposeCiFix,
  ]);
  const files = tree?.files ?? [];
  const changed = Boolean(file && draft !== file.content);
  const retryWorkspace = () => {
    void queryClient.invalidateQueries({
      queryKey: ["session-code-tree", slugId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["session-dependency-graph", slugId],
    });
  };
  if (!treePending && !treeError)
    return (
      <div className="px-6 py-5">
        <IDEWorkspace
          data={data}
          files={files}
          selectedPath={selectedPath}
          onSelectPath={setSelectedPath}
          file={file}
          filePending={filePending}
          fileError={fileError}
          draft={draft}
          onDraftChange={setDraft}
          commitMessage={commitMessage}
          onCommitMessageChange={setCommitMessage}
          changed={changed}
          onPropose={() => propose.mutate()}
          proposalPending={propose.isPending}
          commits={commitData?.commits ?? []}
          commitsPending={commitsPending}
          ci={ci}
          onGenerateCiProposal={() => proposeCiFix.mutate()}
          ciProposalPending={proposeCiFix.isPending}
          ciProposalAvailable={Boolean(
            file && ci?.relevant_paths?.includes(file.path),
          )}
          deliveryPlan={deliveryPlan}
          pendingApproval={pendingApproval}
          onDeliver={() => deliver.mutate()}
          deliveryPending={deliver.isPending}
        />
      </div>
    );
  if (treeError) {
    const detail =
      treeError instanceof Error
        ? treeError.message
        : "Repository workspace is unavailable.";
    return (
      <>
        <PageTitle
          title="Code"
          description="Repository access is required before files can be edited or mapped."
        />
        <div className="p-6">
          <Panel title="Repository workspace unavailable" icon={AlertTriangle}>
            <p className="text-sm leading-6 text-muted-foreground">{detail}</p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Base64 recreates an incomplete local workspace automatically. If
              GitHub denies access, reconnect GitHub and ensure this repository
              is included in the OAuth grant.
            </p>
            <button
              type="button"
              onClick={retryWorkspace}
              className="mt-4 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
            >
              Retry repository access
            </button>
          </Panel>
        </div>
      </>
    );
  }
  return (
    <>
      <PageTitle
        title="Code"
        description="Browse redacted repository files, draft a focused change, then create an exact diff for validation and approval. Browser edits never write directly to GitHub."
      />
      <div className="grid gap-4 p-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <Panel title="Repository files" icon={Code2}>
          {treePending ? (
            <p className="text-sm text-muted-foreground">
              Preparing repository workspace…
            </p>
          ) : treeError ? (
            <Empty>
              Repository files are unavailable. Reconnect GitHub or reopen this
              repository session.
            </Empty>
          ) : files.length ? (
            <div className="max-h-[38rem] space-y-1 overflow-auto">
              {files.map((item) => (
                <button
                  type="button"
                  key={item.path}
                  onClick={() => setSelectedPath(item.path)}
                  className={cn(
                    "block w-full truncate rounded px-2 py-1.5 text-left font-mono text-xs hover:bg-muted",
                    selectedPath === item.path && "bg-muted text-foreground",
                  )}
                  title={item.path}
                >
                  {item.path}
                </button>
              ))}
            </div>
          ) : (
            <Empty>No supported source files were found.</Empty>
          )}
        </Panel>
        <Panel title="Reviewable code draft" icon={FileCode2}>
          {filePending ? (
            <p className="text-sm text-muted-foreground">
              Loading redacted file content…
            </p>
          ) : fileError ? (
            <Empty>The selected file could not be read safely.</Empty>
          ) : file ? (
            <div className="space-y-3">
              <div className="flex flex-wrap justify-between gap-2 text-xs">
                <p className="font-mono">{file.path}</p>
                <span className="text-muted-foreground">
                  SHA {shortSha(file.headSha)}
                  {file.truncated ? " · truncated" : ""}
                </span>
              </div>
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                disabled={file.truncated}
                className="min-h-[30rem] resize-y font-mono text-xs leading-6"
                aria-label="Editable code draft"
              />
              <label className="block text-sm font-medium">
                Commit message
                <input
                  value={commitMessage}
                  onChange={(event) => setCommitMessage(event.target.value)}
                  maxLength={120}
                  className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                  placeholder={`Update ${file.path}`}
                />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => propose.mutate()}
                  disabled={!changed || propose.isPending || file.truncated}
                  className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
                >
                  {propose.isPending
                    ? "Validating draft…"
                    : "Create reviewable change"}
                </button>
                {changed ? (
                  <p className="text-xs text-muted-foreground">
                    This creates an exact diff, runs eligible validation,
                    classifies risk, and requests approval. It does not push
                    yet.
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Edit the file to enable a reviewable change.
                  </p>
                )}
              </div>
              {file.truncated ? (
                <Empty>
                  This file is too large for safe browser editing. Use the
                  assistant to investigate it.
                </Empty>
              ) : null}
            </div>
          ) : (
            <Empty>Select a file to inspect and draft a change.</Empty>
          )}
        </Panel>
      </div>
    </>
  );
}

type DependencyGraphData = {
  nodes: string[];
  edges: Array<{ from: string; to: string }>;
  mermaid: string;
  truncated: boolean;
};

function DependencyGraphExplorer({
  graph,
  focusedPath,
  onFocus,
}: {
  graph: DependencyGraphData;
  focusedPath: string | null;
  onFocus: (path: string) => void;
}) {
  const groups = useMemo(
    () =>
      graph.nodes.reduce<Record<string, string[]>>((result, path) => {
        const group = path.includes("/") ? path.split("/")[0] : "root";
        (result[group] ??= []).push(path);
        return result;
      }, {}),
    [graph.nodes],
  );
  const active =
    focusedPath && graph.nodes.includes(focusedPath)
      ? focusedPath
      : graph.nodes[0];
  const related = graph.edges.filter(
    (edge) => edge.from === active || edge.to === active,
  );
  const outward = related
    .filter((edge) => edge.from === active)
    .map((edge) => edge.to);
  const inbound = related
    .filter((edge) => edge.to === active)
    .map((edge) => edge.from);
  const initials = (path: string) =>
    path.split("/").at(-1)?.slice(0, 2).toUpperCase() ?? "??";
  return (
    <section className="px-6 pb-6">
      <section className="dark overflow-hidden rounded-2xl border border-cyan-400/20 bg-[#0c1222] shadow-[0_24px_80px_-45px_rgba(6,182,212,0.65)]">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-cyan-300/10 bg-gradient-to-r from-cyan-500/10 via-blue-500/5 to-transparent px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <GitBranch className="size-4 text-cyan-300" />
              <h2 className="text-sm font-semibold text-slate-100">
                Repository dependency map
              </h2>
            </div>
            <p className="mt-1 text-sm text-slate-400">
              Browse file relationships without executing repository code.
            </p>
          </div>
          <div className="flex gap-2">
            <Badge
              variant="outline"
              className="border-cyan-300/25 bg-cyan-300/5 text-cyan-100"
            >
              {graph.nodes.length} files
            </Badge>
            <Badge
              variant="outline"
              className="border-violet-300/25 bg-violet-300/5 text-violet-100"
            >
              {graph.edges.length} imports
            </Badge>
          </div>
        </header>
        <div className="grid gap-4 p-5 xl:grid-cols-[minmax(16rem,0.9fr)_minmax(18rem,1.1fr)]">
          <div className="rounded-xl border border-white/10 bg-[#101827] p-3">
            <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              File groups
            </p>
            <div className="space-y-3">
              {Object.entries(groups)
                .sort(([left], [right]) => left.localeCompare(right))
                .map(([group, paths], index) => (
                  <div
                    key={group}
                    className="rounded-lg border border-white/8 bg-white/[0.025] p-2"
                  >
                    <div className="mb-2 flex items-center justify-between text-xs">
                      <span className="font-medium text-slate-200">
                        {group}
                      </span>
                      <span className="text-slate-500">{paths.length}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {paths.slice(0, 16).map((path) => (
                        <button
                          type="button"
                          key={path}
                          onClick={() => onFocus(path)}
                          className={cn(
                            "rounded-md border px-2 py-1 font-mono text-[10px] transition",
                            active === path
                              ? "border-cyan-300/70 bg-cyan-300/15 text-cyan-100 shadow-[0_0_16px_rgba(34,211,238,0.18)]"
                              : index % 2
                                ? "border-violet-300/15 bg-violet-300/5 text-violet-100 hover:border-violet-300/40"
                                : "border-blue-300/15 bg-blue-300/5 text-blue-100 hover:border-blue-300/40",
                          )}
                        >
                          {path.split("/").at(-1)}
                        </button>
                      ))}
                      {paths.length > 16 ? (
                        <span className="px-1 py-1 text-[10px] text-slate-500">
                          +{paths.length - 16} more
                        </span>
                      ) : null}
                    </div>
                  </div>
                ))}
            </div>
          </div>
          <div className="relative overflow-hidden rounded-xl border border-cyan-300/15 bg-[radial-gradient(circle_at_center,rgba(8,145,178,0.15),transparent_55%)] p-4">
            <div className="absolute inset-0 opacity-30 [background-size:28px_28px] [background-image:linear-gradient(rgba(148,163,184,.1)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,.1)_1px,transparent_1px)]" />
            <div className="relative">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                Dependency spotlight
              </p>
              {active ? (
                <>
                  <div className="my-5 flex items-center justify-center">
                    <div className="max-w-xs rounded-xl border border-cyan-300/50 bg-[#10243b] px-5 py-4 text-center shadow-[0_0_38px_rgba(34,211,238,0.2)]">
                      <span className="mx-auto mb-2 flex size-8 items-center justify-center rounded-lg bg-cyan-300/15 font-mono text-xs text-cyan-100">
                        {initials(active)}
                      </span>
                      <p className="break-all font-mono text-xs text-slate-100">
                        {active}
                      </p>
                      <p className="mt-1 text-[11px] text-cyan-200">
                        {related.length} local relationship
                        {related.length === 1 ? "" : "s"}
                      </p>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg border border-violet-300/15 bg-violet-300/5 p-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-200">
                        Imports
                      </p>
                      {outward.length ? (
                        <div className="mt-2 space-y-1.5">
                          {outward.map((path) => (
                            <button
                              type="button"
                              key={path}
                              onClick={() => onFocus(path)}
                              className="block w-full truncate rounded bg-black/20 px-2 py-1 text-left font-mono text-[11px] text-slate-200 hover:bg-violet-300/15"
                            >
                              → {path}
                            </button>
                          ))}
                        </div>
                      ) : (
                        <p className="mt-2 text-xs text-slate-500">
                          No local imports detected.
                        </p>
                      )}
                    </div>
                    <div className="rounded-lg border border-blue-300/15 bg-blue-300/5 p-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-blue-200">
                        Imported by
                      </p>
                      {inbound.length ? (
                        <div className="mt-2 space-y-1.5">
                          {inbound.map((path) => (
                            <button
                              type="button"
                              key={path}
                              onClick={() => onFocus(path)}
                              className="block w-full truncate rounded bg-black/20 px-2 py-1 text-left font-mono text-[11px] text-slate-200 hover:bg-blue-300/15"
                            >
                              ← {path}
                            </button>
                          ))}
                        </div>
                      ) : (
                        <p className="mt-2 text-xs text-slate-500">
                          No local dependents detected.
                        </p>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <p className="mt-5 text-sm text-slate-400">
                  No supported source files were found.
                </p>
              )}
            </div>
          </div>
        </div>
        {graph.edges.length ? (
          <details className="border-t border-white/10 bg-black/10 px-5 py-3">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-sm text-slate-300">
              <ChevronDown className="size-4 text-cyan-300" />
              Open full Mermaid graph{" "}
              <span className="text-xs text-slate-500">
                (large graphs are intentionally collapsed)
              </span>
            </summary>
            <div className="mt-4 max-h-[34rem] overflow-auto rounded-xl border border-white/10 bg-[#0b1020] p-4">
              <Streamdown
                plugins={{ mermaid }}
              >{`\`\`\`mermaid\n${graph.mermaid}\n\`\`\``}</Streamdown>
            </div>
          </details>
        ) : null}
        <footer className="flex flex-wrap justify-between gap-2 border-t border-white/10 px-5 py-3 text-xs text-slate-500">
          <span>
            Static import analysis only; package scripts and source code are
            never executed.
          </span>
          <span>
            {graph.truncated
              ? "Graph edges capped for readability"
              : "Complete within configured display bounds"}
          </span>
        </footer>
      </section>
    </section>
  );
}

function DependencyGraph({ slugId }: { slugId: string }) {
  const [focusedPath, setFocusedPath] = useState<string | null>(null);
  const {
    data: queryData,
    isPending,
    error,
  } = useQuery({
    queryKey: ["session-dependency-graph", slugId],
    queryFn: () => getSessionDependencyGraph(slugId),
    retry: false,
  });
  if (!isPending && !error && queryData)
    return (
      <DependencyGraphExplorer
        graph={queryData}
        focusedPath={focusedPath}
        onFocus={setFocusedPath}
      />
    );
  // Retain a typed fallback for the transient query state while React Query
  // replaces cached graph data.
  const fallbackGraph = queryData as DependencyGraphData | undefined;
  const data = fallbackGraph;
  return (
    <section className="px-6 pb-6">
      <Panel title="Repository dependency graph" icon={GitBranch}>
        {isPending ? (
          <p className="text-sm text-muted-foreground">
            Mapping local file imports…
          </p>
        ) : error ? (
          <Empty>
            The dependency graph could not be created from this repository.
          </Empty>
        ) : fallbackGraph?.edges.length ? (
          <>
            <p className="mb-3 text-sm text-muted-foreground">
              Static local-import relationships only. It does not execute source
              code or package scripts.
            </p>
            <div className="max-h-[42rem] overflow-auto rounded-lg border border-border bg-background p-4">
              <Streamdown
                plugins={{ mermaid }}
              >{`\`\`\`mermaid\n${fallbackGraph.mermaid}\n\`\`\``}</Streamdown>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {data!.nodes.length} files · {data!.edges.length} relationships
              {data!.truncated ? " · graph capped for readability" : ""}
            </p>
          </>
        ) : (
          <Empty>
            No local import relationships were found among supported Python,
            TypeScript, JavaScript, JSX, or TSX files.
          </Empty>
        )}
      </Panel>
    </section>
  );
}

function Code({ data }: { data: SingleSessionResponse }) {
  return (
    <>
      <ReadOnlyCode data={data} />
      <DependencyGraph slugId={data.session.slugId} />
    </>
  );
}

export default SessionPage;
