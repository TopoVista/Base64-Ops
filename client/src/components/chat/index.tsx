import { useAgentSession } from "@/hooks/use-agent-session";
import { useBackendUser } from "@/hooks/use-user";
import { getBackendHealth, getGithubRepos, reindexSessionRag } from "@/lib/api";
import { cn, generateSlugId } from "@/lib/utils";
import type { AgentMessage, ApprovalRequest, CIInvestigationSummary, CommandCenterSnapshot, DeliveryPlanSummary, DeliveryResult, EvidenceItem, InvestigationSummary, RagSource, RelatedMemory, TimelineEvent, ToolResult } from "@/types/agent.type";
import type { GithubRepo } from "@/types/github.type";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { MessageResponse } from "../ai-elements/message";
import { DiffViewer } from "../diff-viewer";
import { Badge } from "../ui/badge";
import { ScrollArea } from "../ui/scroll-area";
import ChatInput from "./chat-input";
import {
  Activity,
  BookOpen,
  Bot,
  Boxes,
  CheckCircle2,
  Code2,
  ExternalLink,
  GitPullRequest,
  RadioTower,
  Search,
  ShieldAlert,
  Sparkles,
  Terminal,
  User,
  XCircle,
} from "lucide-react";

type ChatInterfaceProps = {
  className?: string;
  initialMessages?: AgentMessage[];
  sessionTitle?: string;
  slugId?: string;
  repoUrl?: string;
  defaultBranch?: string;
  branchName?: string | null;
  isSingleSession?: boolean;
  initialCommandCenter?: CommandCenterSnapshot | null;
  initialIndexStatus?: "indexing" | "ready" | "empty" | "failed" | null;
  initialIndexError?: string | null;
  isDemo?: boolean;
  workspaceMode?: boolean;
};

type SelectedRepo = {
  value: string;
  defaultBranch: string;
  label?: string;
};

// Keep the default reference stable. A new array on each render makes the
// initial-message sync effect below continuously update component state.
const EMPTY_MESSAGES: AgentMessage[] = [];

const EmptyState = () => (
  <div className="mx-auto flex max-w-3xl flex-1 flex-col items-center justify-center px-6 py-16 text-center">
    <div className="mb-6 rounded-full border border-cyan-300/30 bg-cyan-300/10 p-4 text-cyan-200 shadow-2xl shadow-cyan-950/40">
      <RadioTower className="size-8" />
    </div>
    <h1 className="bg-gradient-to-br from-white via-zinc-200 to-cyan-200 bg-clip-text text-4xl font-semibold tracking-tight text-transparent md:text-6xl">
      DevOps command center for your codebase
    </h1>
    <p className="mt-5 max-w-2xl text-base leading-7 text-zinc-400">
      Select a GitHub repo, index the code and runbooks, then ask the LangGraph agent to inspect,
      diagnose, or prepare approved changes.
    </p>
    <div className="mt-6 grid w-full gap-3 text-left md:grid-cols-3">
      {[
        ["Evidence first", "Answers cite repository files, CI output, and runbooks."],
        ["Approval gated", "Risky writes, pushes, and ops wait for you."],
        ["CI aware", "Separates failed-run evidence from the current branch."],
      ].map(([title, body]) => (
        <div key={title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <p className="text-sm font-semibold text-zinc-100">{title}</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">{body}</p>
        </div>
      ))}
    </div>
  </div>
);

const MessageBubble = ({ message }: { message: AgentMessage }) => {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex gap-3", isUser && "justify-end")}>
      {!isUser ? (
        <div className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-2xl bg-cyan-400/10 text-cyan-200">
          <Bot className="size-4" />
        </div>
      ) : null}
      <div
        className={cn(
          "max-w-[82%] rounded-3xl border px-4 py-3 text-sm leading-6 shadow-xl",
          isUser
            ? "border-cyan-300/20 bg-cyan-300/10 text-cyan-50"
            : "border-white/10 bg-zinc-950/80 text-zinc-100",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <MessageResponse shikiTheme={["dracula", "dracula"]}>{message.content}</MessageResponse>
        )}
      </div>
      {isUser ? (
        <div className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-2xl bg-white/10 text-zinc-200">
          <User className="size-4" />
        </div>
      ) : null}
    </div>
  );
};

const TimelinePanel = ({ timeline }: { timeline: TimelineEvent[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<Activity className="size-4" />} title="LangGraph Timeline" />
    <div className="space-y-2">
      {timeline.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Agent execution steps appear here.
        </p>
      ) : (
        timeline.map((item) => (
          <div key={item.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
            <div className="flex items-center gap-2">
              {item.status === "failed" ? (
                <XCircle className="size-4 text-rose-300" />
              ) : item.status === "partial" ? (
                <Activity className="size-4 text-amber-300" />
              ) : item.status === "started" ? (
                <Activity className="size-4 text-cyan-300" />
              ) : (
                <CheckCircle2 className="size-4 text-emerald-300" />
              )}
              <p className="text-sm font-medium text-zinc-100">{item.label ?? item.name}</p>
              {item.risk ? <Badge variant="outline">{item.risk}</Badge> : null}
            </div>
            {item.detail ? <p className="mt-1 text-xs leading-5 text-zinc-500">{item.detail}</p> : null}
          </div>
        ))
      )}
    </div>
  </div>
);

const SourcesPanel = ({ sources }: { sources: RagSource[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<BookOpen className="size-4" />} title="RAG Sources" />
    <div className="space-y-2">
      {sources.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Retrieved code, CI config, Docker files, and runbooks appear here.
        </p>
      ) : (
        sources.map((source, index) => (
          <div key={`${source.source}-${index}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-sm font-medium text-zinc-100">{source.source}</p>
              <Badge variant="secondary">{source.kind ?? "repo"}</Badge>
            </div>
            {source.excerpt ? <p className="mt-2 line-clamp-4 text-xs leading-5 text-zinc-500">{source.excerpt}</p> : null}
          </div>
        ))
      )}
    </div>
  </div>
);

const EvidencePanel = ({ evidence, ciSummary }: { evidence: EvidenceItem[]; ciSummary: CIInvestigationSummary | null }) => (
  <div className="space-y-3">
    <PanelTitle icon={<Search className="size-4" />} title="Evidence" />
    {ciSummary ? (
      <article className="rounded-2xl border border-cyan-300/20 bg-cyan-300/[0.04] p-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-cyan-200">GitHub Actions failure</p>
        <p className="mt-1 text-sm font-medium text-zinc-100">{ciSummary.workflow_name ?? "Workflow"} · run #{ciSummary.run_id}</p>
        <p className="mt-1 text-xs text-zinc-500">Run SHA {ciSummary.head_sha.slice(0, 7)} · Current HEAD {ciSummary.current_head_sha?.slice(0, 7) ?? "Unavailable"}</p>
        <p className="mt-2 text-xs text-cyan-100">{formatCiApplicability(ciSummary.applicability)}</p>
        {ciSummary.applicability_reason ? <p className="mt-1 text-xs text-zinc-400">{ciSummary.applicability_reason}</p> : null}
        {ciSummary.limitations.length ? <p className="mt-2 text-xs text-amber-200">Limited CI visibility: {ciSummary.limitations.join(" ")}</p> : null}
      </article>
    ) : null}
    {!ciSummary && evidence.find((item) => item.sourceType === "ci_log") ? (() => {
      const ci = evidence.find((item) => item.sourceType === "ci_log")!;
      const current = evidence.find((item) => item.metadata?.state === "current");
      return (
        <article className="rounded-2xl border border-cyan-300/20 bg-cyan-300/[0.04] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-cyan-200">GitHub Actions failure</p>
          <p className="mt-1 text-sm font-medium text-zinc-100">{ci.metadata?.workflow_name ?? "Workflow"} · run #{ci.metadata?.workflow_run_id ?? "?"}</p>
          <p className="mt-1 text-xs text-zinc-400">Failed job: {ci.metadata?.job_name ?? "unknown"}{ci.metadata?.failed_step ? ` · ${ci.metadata.failed_step}` : ""}</p>
          <p className="mt-1 text-xs text-zinc-500">Run SHA {ci.commitSha?.slice(0, 7) ?? "unknown"}{current?.commitSha ? ` · Current HEAD ${current.commitSha.slice(0, 7)}` : ""}</p>
          {ci.metadata?.state === "failed_run" && current ? <p className="mt-2 text-xs text-amber-200">Historical CI evidence; compare the workflow evidence before preparing a change.</p> : null}
        </article>
      );
    })() : null}
    <div className="space-y-2">
      {evidence.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Evidence appears here as the investigation runs.
        </p>
      ) : evidence.slice(0, 6).map((item) => (
        <article key={item.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
          <div className="flex justify-between gap-2 text-[10px] font-semibold uppercase tracking-wide text-cyan-200">
            <span>{item.sourceType === "ci_log" ? "GitHub Actions" : item.sourceType.replace("_", " ")}</span><span>{item.id}</span>
          </div>
          <p className="mt-2 truncate text-sm font-medium text-zinc-100">{item.path ?? item.title}</p>
          {item.sourceType === "ci_log" && item.metadata ? (
            <div className="mt-1 text-[11px] text-cyan-100/70">
              <p>{item.metadata.workflow_name ?? "Workflow"} · {item.metadata.job_name ?? "job"}</p>
              {item.metadata.failed_step ? <p>Failed step: {item.metadata.failed_step}</p> : null}
              {item.metadata.categories?.length ? <p>Category: {item.metadata.categories.map(formatCiCategory).join(", ")}</p> : null}
              {item.metadata.truncated ? <p>Excerpt truncated for safety.</p> : null}
            </div>
          ) : null}
          {(item.lineStart || item.commitSha) ? <p className="mt-1 text-[11px] text-zinc-500">{item.lineStart ? `lines ${item.lineStart}–${item.lineEnd ?? item.lineStart}` : ""}{item.lineStart && item.commitSha ? " · " : ""}{item.commitSha ? `commit ${item.commitSha.slice(0, 7)}` : ""}</p> : null}
          {item.metadata?.state === "failed_run" && item.commitSha ? (
            <p className="mt-1 text-[11px] text-amber-200">Historical CI evidence · run SHA {item.commitSha.slice(0, 7)}</p>
          ) : null}
          <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-zinc-500">{item.excerpt}</p>
          {item.sourceType === "ci_log" && item.metadata?.html_url ? (
            <a className="mt-2 inline-block text-xs text-cyan-200 hover:underline" href={item.metadata.html_url} target="_blank" rel="noreferrer">Open in GitHub</a>
          ) : null}
        </article>
      ))}
    </div>
  </div>
);

const InvestigationPanel = ({ investigation }: { investigation: InvestigationSummary | null }) => (
  <div className="space-y-3">
    <PanelTitle icon={<Bot className="size-4" />} title="Investigation" />
    {!investigation ? (
      <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">Diagnosis and evidence-linked hypotheses appear here after the investigation starts.</p>
    ) : (
      <article className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
        <div className="flex items-center justify-between gap-2"><p className="text-sm font-medium text-zinc-100">{investigation.status.replaceAll("_", " ")}</p><Badge variant="outline">{Math.round((investigation.hypotheses[0]?.confidence ?? 0) * 100)}% grounded</Badge></div>
        <p className="mt-2 text-xs leading-5 text-zinc-300">{investigation.summary}</p>
        {investigation.hypotheses.map((hypothesis) => (
          <div key={hypothesis.id} className="mt-3 rounded-xl border border-white/10 bg-black/10 p-3">
            <p className="text-xs font-semibold text-cyan-100">{hypothesis.title}</p>
            <p className="mt-1 text-xs leading-5 text-zinc-400">{hypothesis.explanation}</p>
            <p className="mt-2 text-[11px] text-zinc-500">{hypothesis.status} / evidence {hypothesis.evidence_ids.length}</p>
          </div>
        ))}
        {investigation.limitations.length ? <p className="mt-3 text-xs text-amber-200">Limitations: {investigation.limitations.join(" ")}</p> : null}
        <Link className="mt-3 inline-flex text-xs text-cyan-200 hover:underline" to={`/dev/runs/${investigation.run_id}`}>Open replay and trace</Link>
      </article>
    )}
  </div>
);

const RelatedHistoryPanel = ({ memory }: { memory: RelatedMemory[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<BookOpen className="size-4" />} title="Related History" />
    {memory.length === 0 ? (
      <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">Verified operational history appears here when it is relevant. It never authorizes a change.</p>
    ) : memory.map((item, index) => (
      <article key={item.id ?? item.memory_id ?? `${item.key}-${index}`} className="rounded-2xl border border-violet-300/15 bg-violet-300/[0.04] p-3">
        <div className="flex items-center justify-between gap-2"><p className="truncate text-sm font-medium text-zinc-100">{item.key}</p><Badge variant="outline">{item.freshness ?? "historical"}</Badge></div>
        <p className="mt-2 text-xs leading-5 text-zinc-400">{item.value}</p>
        <p className="mt-2 text-[11px] text-zinc-500">{item.kind.replaceAll("_", " ")}{item.source_commit_sha ? ` / source ${item.source_commit_sha.slice(0, 7)}` : ""}</p>
      </article>
    ))}
  </div>
);

const DeliveryPlanPanel = ({ plan, result, expandDiffs = false }: { plan: DeliveryPlanSummary | null; result: DeliveryResult | null; expandDiffs?: boolean }) => (
  <div className="space-y-3">
    <PanelTitle icon={<ShieldAlert className="size-4" />} title="Delivery Plan" />
    {!plan ? (
      <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">A constrained patch, validation plan, deterministic risk, and exact approval binding appear here only when remediation is safe to propose.</p>
    ) : (
      <article className="rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-3">
        <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-medium text-zinc-100">{plan.title}</p><p className="mt-1 text-xs text-zinc-400">{plan.rationale}</p></div><Badge variant="outline">{plan.risk_level.toUpperCase()}</Badge></div>
        <p className="mt-2 text-[11px] text-zinc-500">Base {plan.base_branch} / {plan.base_sha.slice(0, 7)} / evidence {plan.evidence_ids.length}</p>
        {plan.risk_reasons.length ? <p className="mt-2 text-xs text-amber-100">Risk: {plan.risk_reasons.join("; ")}</p> : null}
        {plan.validation_steps?.length ? <div className="mt-3 space-y-1 text-xs text-zinc-400"><p className="font-medium text-zinc-200">Validators</p>{plan.validation_steps.map((step) => <p key={step.id}>• {step.description}</p>)}</div> : null}
        {plan.files.map((file) => <details key={file.path} open={expandDiffs} className="mt-3 rounded-xl border border-white/10 bg-black/20 p-2"><summary className="cursor-pointer text-xs font-medium text-zinc-100">Exact diff / {file.path}</summary><div className="mt-2 overflow-auto"><DiffViewer patch={file.unified_diff} /></div></details>)}
      </article>
    )}
    {result ? <article className={`rounded-xl border p-3 text-xs ${result.status === "success" ? "border-emerald-300/25 text-emerald-100" : result.status === "partial" ? "border-amber-300/25 text-amber-100" : "border-rose-300/25 text-rose-100"}`}><p>Delivery {result.status}{result.reason ? `: ${result.reason}` : ""}{result.pull_request_url ? <a className="ml-2 underline" href={result.pull_request_url} target="_blank" rel="noreferrer">Open draft PR</a> : null}</p>{result.branch_name ? <p className="mt-2">Branch: {result.branch_name}{result.commit_sha ? ` / commit ${result.commit_sha.slice(0, 12)}` : ""}</p> : null}{result.validation_results?.length ? <div className="mt-2 space-y-1"><p className="font-medium">Delivery validators</p>{result.validation_results.map((step) => <p key={step.step_id}>{step.status === "passed" ? "✓" : "•"} {step.summary}</p>)}</div> : null}</article> : null}
  </div>
);

const ToolResultPanel = ({ result }: { result: ToolResult | null }) => (
  <div className="space-y-3">
    <PanelTitle icon={<Terminal className="size-4" />} title="Read-only Diagnostic" />
    {!result ? <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">Safe diagnostic output appears here when a read-only tool runs.</p> : <article className={`rounded-2xl border p-3 ${result.success ? "border-emerald-300/20 bg-emerald-300/[0.04]" : "border-rose-300/20 bg-rose-300/[0.04]"}`}><p className="text-xs font-semibold text-zinc-100">{result.success ? "Completed" : "Did not complete"}</p><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">{result.output}</pre></article>}
  </div>
);

const ApprovalPanel = ({ approvals }: { approvals: ApprovalRequest[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<ShieldAlert className="size-4" />} title="Approvals" />
    <div className="space-y-2">
      {approvals.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Risky operations pause here before they run.
        </p>
      ) : (
        approvals.map((approval) => (
          <div key={approval.id} className="rounded-2xl border border-amber-300/20 bg-amber-300/10 p-3">
            <p className="text-sm font-medium text-amber-100">{approval.action}</p>
            <p className="mt-1 text-xs leading-5 text-amber-100/70">{approval.summary}</p>
            <Badge className="mt-2" variant="outline">
              {approval.status ?? "pending"}
            </Badge>
            {approval.deliveryPlanId ? <div className="mt-3 space-y-1 rounded-xl border border-amber-200/15 bg-black/10 p-2 font-mono text-[11px] text-amber-100/70"><p>Plan: {approval.deliveryPlanId}</p>{approval.baseSha ? <p>Base: {approval.baseBranch ?? "branch"} @ {approval.baseSha.slice(0, 12)}</p> : null}{approval.diffHash ? <p>Exact diff: {approval.diffHash.slice(0, 16)}…</p> : null}{approval.approvalHash ? <p>Approval binding: {approval.approvalHash.slice(0, 16)}…</p> : null}{approval.evidenceIds?.length ? <p>Evidence: {approval.evidenceIds.length} item(s)</p> : null}</div> : null}
          </div>
        ))
      )}
    </div>
  </div>
);

const PanelTitle = ({ icon, title }: { icon: ReactNode; title: string }) => (
  <div className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
    <span className="text-cyan-200">{icon}</span>
    {title}
  </div>
);

const formatCiCategory = (category: string) => ({
  test_failure: "Test failure",
  build_failure: "Build failure",
  typecheck_failure: "Type-check failure",
  lint_failure: "Lint failure",
  dependency_failure: "Dependency failure",
  configuration_failure: "Configuration failure",
  missing_file: "Missing file/path",
  missing_environment: "Missing environment",
  permission_failure: "Permission failure",
  network_failure: "Network failure",
  deployment_failure: "Deployment failure",
  timeout: "Timeout",
}[category] ?? category.replaceAll("_", " "));

const formatCiApplicability = (applicability?: CIInvestigationSummary["applicability"]) => {
  const labels: Record<string, string> = {
    current: "Current status: failure condition still present.",
    historical_fixed: "Historical CI failure: the identified condition is already corrected. No new patch was prepared.",
    historical_needs_verification: "Historical failure: current applicability could not be verified. No remediation was prepared.",
  };
  return labels[applicability ?? ""] ?? "CI applicability is still being determined.";
};

const ChatInterface = ({
  className,
  initialMessages = EMPTY_MESSAGES,
  sessionTitle: sessionTitleProp,
  slugId: slugIdProp,
  repoUrl,
  defaultBranch,
  branchName: branchNameProp,
  initialCommandCenter,
  initialIndexStatus = null,
  initialIndexError = null,
  isDemo = false,
  workspaceMode = false,
}: ChatInterfaceProps) => {
  const [repo, setRepo] = useState<SelectedRepo | null>(
    repoUrl
      ? {
          value: repoUrl,
          defaultBranch: defaultBranch ?? "main",
        }
      : null,
  );
  const [sessionTitle, setSessionTitle] = useState(sessionTitleProp ?? "New Operation");
  const [branchName] = useState<string | null>(branchNameProp ?? null);
  const [slugId] = useState(() => slugIdProp || generateSlugId());
  const [isSessionReady, setIsSessionReady] = useState(Boolean(slugIdProp));
  const [indexStatus, setIndexStatus] = useState<"indexing" | "ready" | "empty" | "failed" | null>(initialIndexStatus);
  const [indexStatusMessage, setIndexStatusMessage] = useState<string | null>(initialIndexError);
  const queryClient = useQueryClient();
  const { data: currentUser } = useBackendUser();
  const isGithubConnected = Boolean(currentUser?.user?.githubConnected);
  const { data: backendHealth, isError: isBackendHealthError } = useQuery({
    queryKey: ["backend-health"],
    queryFn: getBackendHealth,
    enabled: !isDemo,
    retry: false,
    staleTime: 30_000,
  });
  const backendIsDegraded = backendHealth?.status === "degraded" || isBackendHealthError;

  const {
    messages,
    setMessages,
    sources,
    evidence,
    timeline,
    ciSummary,
    repositoryContext,
    investigation,
    relatedMemory,
    deliveryPlan,
    deliveryResult,
    toolResult,
    approvals,
    status,
    sendMessage,
    stop,
    decideApproval,
    hydrate,
  } = useAgentSession(initialMessages, {
    onSessionUpdated: useCallback(() => {
      setIsSessionReady(true);
      queryClient.invalidateQueries({ queryKey: ["user-sessions"] });
      if (slugIdProp) {
        queryClient.invalidateQueries({ queryKey: ["session", slugIdProp] });
      }
    }, [queryClient, slugIdProp]),
  });

  useEffect(() => {
    setMessages(initialMessages);
  }, [initialMessages, setMessages]);

  useEffect(() => {
    hydrate(initialCommandCenter);
  }, [hydrate, initialCommandCenter]);

  const { data: githubRepos, isPending: isGithubRepoPending } = useQuery({
    queryKey: ["github-repos"],
    queryFn: getGithubRepos,
    enabled: isGithubConnected,
    retry: false,
  });

  const repoOptions = useMemo(
    () =>
      githubRepos?.repos?.map((repoItem: GithubRepo) => ({
        label: repoItem.fullName,
        value: repoItem.cloneUrl,
        defaultBranch: repoItem.defaultBranch,
      })) ?? [],
    [githubRepos],
  );

  const reindexMutation = useMutation({
    mutationFn: () => reindexSessionRag(slugId),
    onSuccess: (data) => {
      const message = data.success
        ? `Index ready: ${data.indexed} repository chunks are available${data.storage ? ` through ${data.storage} retrieval` : ""}.${data.vectorWarning ? ` ${data.vectorWarning}` : ""}`
        : `Indexing did not complete: ${data.reason ?? "no supported repository files were found."}`;
      setIndexStatusMessage(message);
      setIndexStatus(data.status ?? (data.success ? "ready" : "failed"));
      data.success ? toast.success(message) : toast.error(message);
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "Unable to reindex RAG sources";
      setIndexStatusMessage(`Indexing failed: ${message}`);
      setIndexStatus("failed");
      toast.error(message);
    },
  });

  const startReindex = () => {
    setIndexStatus("indexing");
    setIndexStatusMessage("Indexing repository files…");
    reindexMutation.mutate();
  };

  const handleRepoChange = (value: string) => {
    const selected = repoOptions.find((option) => option.value === value);
    setRepo(selected ?? { value, defaultBranch: "main" });
  };

  const handleSubmit = (message: string) => {
    if (!repo) {
      toast.error("Select a repository first");
      return;
    }
    setSessionTitle(message.slice(0, 48));
    if (!slugIdProp) {
      window.history.pushState(null, "", `/session/${slugId}`);
    }
    sendMessage({
      slugId,
      repoUrl: repo.value,
      defaultBranch: repo.defaultBranch,
      message,
    });
    queryClient.invalidateQueries({ queryKey: ["user-sessions"] });
  };

  const handleApproval = async (approval: ApprovalRequest, decision: "approve" | "edit" | "reject") => {
    try {
      await decideApproval(slugId, approval, decision);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Unable to record the approval decision");
    }
  };

  return (
    <div className={cn("chat-interface flex min-h-0 w-full flex-col bg-background text-foreground", workspaceMode ? "h-full" : "h-[100dvh]", className)}>
      {!workspaceMode ? <div className="border-b border-border bg-background/90 px-4 py-3 backdrop-blur-xl">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-3">
            <div className="rounded-2xl bg-primary/10 p-2 text-primary">
              <Sparkles className="size-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">{sessionTitle}</h2>
              <p className="text-xs text-muted-foreground">LangGraph + RAG + approval-gated DevOps</p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {isDemo ? <Badge variant="outline" className="border-amber-300/30 text-amber-200">deterministic fixture / no mutations</Badge> : null}
            <Badge
              variant="outline"
              className={backendIsDegraded ? "border-amber-300/30 bg-amber-300/10 text-amber-200" : "border-primary/25 bg-primary/5 text-primary"}
              title={backendHealth?.message ?? (isBackendHealthError ? "Backend health check could not be reached." : undefined)}
            >
              {backendIsDegraded ? "backend degraded" : status === "idle" ? "ready" : status}
            </Badge>
            <Badge variant="outline" className="gap-1 border-border text-muted-foreground">
              <GitPullRequest className="size-3" /> PR after approved plan
            </Badge>
          </div>
        </div>
      </div> : null}

      <div className={cn("grid min-h-0 flex-1 overflow-hidden", workspaceMode ? "grid-cols-1" : "grid-cols-1 xl:grid-cols-[minmax(0,1fr)_24rem]")}>
        <div className="relative flex min-h-0 flex-col overflow-hidden">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(34,211,238,0.18),transparent_28%),radial-gradient(circle_at_80%_0%,rgba(99,102,241,0.14),transparent_22%)]" />
          <ScrollArea className="relative min-h-0 flex-1">
            <div className="mx-auto flex min-h-[calc(100dvh-13rem)] max-w-5xl flex-col gap-5 px-4 py-6">
              {messages.length === 0 ? <EmptyState /> : messages.map((message) => <MessageBubble key={message.id} message={message} />)}
            </div>
          </ScrollArea>

          <ChatInput
            status={status}
            repo={repo}
            branchName={branchName}
            approvals={approvals}
            isGithubConnected={isGithubConnected}
            isFetchingRepos={isGithubRepoPending}
            repoOptions={repoOptions}
            onSubmit={handleSubmit}
            onStop={stop}
            onRepoChange={handleRepoChange}
            onApprovalDecision={handleApproval}
            onReindex={startReindex}
            isReindexing={reindexMutation.isPending}
            isSessionReady={isSessionReady}
            indexStatusMessage={indexStatusMessage}
            isDemo={isDemo}
          />
        </div>

        {!workspaceMode ? <aside className="hidden min-h-0 border-l border-border bg-background/95 xl:block">
          <ScrollArea className="h-full">
            <div className="space-y-6 p-4">
              <div className="rounded-3xl border border-border bg-card p-4 shadow-sm">
                <PanelTitle icon={<Boxes className="size-4" />} title="Operation Context" />
                <div className="mt-4 space-y-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">Repository</span>
                    <span className="truncate text-right text-foreground">{repositoryContext?.name ?? repo?.label ?? repo?.value ?? "None"}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">Branch</span>
                    <span className="truncate text-right text-foreground">{repositoryContext?.branch ?? branchName ?? "Created on first run"}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-zinc-500">Stack</span>
                    <span className="text-zinc-200">FastAPI · LangGraph</span>
                  </div>
                  {repositoryContext?.commit_sha ? <div className="flex items-center justify-between gap-3"><span className="text-zinc-500">Current SHA</span><span className="font-mono text-xs text-zinc-200">{repositoryContext.commit_sha.slice(0, 12)}</span></div> : null}
                  {repositoryContext?.file_count ? <div className="flex items-center justify-between gap-3"><span className="text-zinc-500">Repository map</span><span className="text-zinc-200">{repositoryContext.file_count} files / {repositoryContext.ci_files?.length ?? 0} CI</span></div> : null}
                </div>
              </div>
              <TimelinePanel timeline={timeline} />
              <div className="rounded-3xl border border-border bg-card p-4 shadow-sm"><PanelTitle icon={<Search className="size-4" />} title="Repository Index" /><p className="mt-2 text-xs text-zinc-200">{indexStatus === "indexing" ? "Indexing in progress…" : indexStatus === "ready" ? "Index ready for retrieval." : indexStatus === "empty" ? "No supported indexable files were found; direct inspection remains available." : indexStatus === "failed" ? `Indexing failed: ${indexStatusMessage ?? "see recovery guidance below."}` : "Not indexed yet. The agent can use bounded direct read-only inspection."}</p></div>
              <SourcesPanel sources={sources} />
              <EvidencePanel evidence={evidence} ciSummary={ciSummary} />
              <InvestigationPanel investigation={investigation} />
              <RelatedHistoryPanel memory={relatedMemory} />
              <DeliveryPlanPanel plan={deliveryPlan} result={deliveryResult} expandDiffs={isDemo} />
              <ToolResultPanel result={toolResult} />
              <ApprovalPanel approvals={approvals} />
              <div className="rounded-3xl border border-border bg-card p-4 shadow-sm">
                <PanelTitle icon={<Code2 className="size-4" />} title="DevOps Surface" />
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  Read-only diagnostics run immediately. Commits, pushes, workflow dispatches, and deploy-like operations require approval.
                </p>
                <Link className="mt-3 inline-flex items-center gap-2 text-xs font-medium text-primary hover:underline" to="/new">
                  Start another operation <ExternalLink className="size-3" />
                </Link>
              </div>
            </div>
          </ScrollArea>
        </aside> : null}
      </div>
    </div>
  );
};

export default ChatInterface;
