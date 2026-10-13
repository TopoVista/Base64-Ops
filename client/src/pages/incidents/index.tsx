import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  downloadIncidentExport,
  getCandidateVerificationSettings,
  getIncident,
  getIncidents,
  getRepositoryAutomationSettings,
  getSystemCapabilities,
  getUserSessions,
  submitIncidentFeedback,
  updateCandidateVerificationSettings,
  updateRepositoryAutomationSettings,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  CandidateVerificationSettingsInput,
  IncidentRecord,
  RepositoryAutomationSettingsInput,
} from "@/types/incident.type";
import type { SessionRecord } from "@/types/session.type";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Download,
  ExternalLink,
  FileWarning,
  Filter,
  GitBranch,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";

const prettify = (value: string) => value.replaceAll("_", " ");
const shortSha = (sha: string) => sha.slice(0, 12);
const EMPTY_INCIDENTS: IncidentRecord[] = [];
const EMPTY_SESSIONS: SessionRecord[] = [];
const dateTime = (value: string) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Time unavailable" : parsed.toLocaleString();
};
const listValue = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const listText = (value: string[]) => value.join(", ");
const DEFAULT_AUTOMATION: RepositoryAutomationSettingsInput = {
  enabled: false,
  workflow_names: [],
  include_branches: [],
  exclude_branches: [],
  daily_investigation_limit: 5,
  github_check_reporting_enabled: false,
};
const DEFAULT_CANDIDATE_VERIFICATION: CandidateVerificationSettingsInput = {
  enabled: false,
  installation_id: "",
  workflow_path: ".github/workflows/base64-candidate-verify.yml",
};

function StatusBadge({ incident }: { incident: IncidentRecord }) {
  const failing = incident.status === "failed" || incident.status === "needs_attention";
  const completed = incident.status === "evidence_collected";
  return (
    <Badge
      variant="outline"
      className={cn(
        "capitalize",
        failing && "border-destructive/40 bg-destructive/10 text-destructive",
        completed && "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
      )}
    >
      {failing ? <CircleAlert className="mr-1 size-3" /> : completed ? <CheckCircle2 className="mr-1 size-3" /> : <Clock3 className="mr-1 size-3" />}
      {prettify(incident.status)}
    </Badge>
  );
}

function IncidentListItem({ incident }: { incident: IncidentRecord }) {
  return (
    <Link
      to={`/incidents/${incident.id}`}
      className="group block rounded-xl border border-border bg-card p-4 shadow-sm transition-colors hover:border-primary/50 hover:bg-muted/35"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold">
            <FileWarning className="size-4 shrink-0 text-destructive" />
            <span className="truncate">{incident.workflowName ?? "GitHub Actions workflow"}</span>
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {incident.repositoryName} <span aria-hidden="true">·</span> run #{incident.workflowRunId}
          </p>
        </div>
        <StatusBadge incident={incident} />
      </div>
      <p className="mt-3 line-clamp-2 text-sm leading-6 text-foreground/85">
        {incident.diagnosisSummary ?? "The incident is awaiting evidence collection."}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1"><GitBranch className="size-3" />{incident.branch ?? "branch unavailable"}</span>
        <span className="font-mono">{shortSha(incident.headSha)}</span>
        <span>{incident.failureCategories.length ? incident.failureCategories.map(prettify).join(", ") : "classification pending"}</span>
        {incident.groupRunIds && incident.groupRunIds.length > 1 ? <span>{incident.groupRunIds.length} related runs</span> : null}
        <span className="ml-auto inline-flex items-center gap-1 text-primary">View incident <ChevronRight className="size-3" /></span>
      </div>
    </Link>
  );
}

function AutomationSettingsPanel() {
  const { data: sessionsData, isPending: sessionsPending } = useQuery({
    queryKey: ["sessions", "incident-automation"],
    queryFn: getUserSessions,
    staleTime: 30_000,
  });
  const sessions = sessionsData?.sessions ?? EMPTY_SESSIONS;
  const [selectedSlugId, setSelectedSlugId] = useState("");
  const selectedSession = sessions.find((session) => session.slugId === selectedSlugId);
  const automationQuery = useQuery({
    queryKey: ["incident-automation", selectedSlugId],
    queryFn: () => getRepositoryAutomationSettings(selectedSlugId),
    enabled: Boolean(selectedSlugId),
    staleTime: 20_000,
  });
  const candidateVerificationQuery = useQuery({
    queryKey: ["candidate-verification-settings", selectedSlugId],
    queryFn: () => getCandidateVerificationSettings(selectedSlugId),
    enabled: Boolean(selectedSlugId),
    staleTime: 20_000,
  });
  const capabilitiesQuery = useQuery({
    queryKey: ["system-capabilities"],
    queryFn: () => getSystemCapabilities<{
      github?: {
        candidate_verification?: { configured?: boolean; unavailable_reason?: string | null };
        incident_check_reporting?: { configured?: boolean; unavailable_reason?: string | null };
      };
    }>(),
    staleTime: 60_000,
  });
  const candidateVerificationConfigured = Boolean(
    capabilitiesQuery.data?.github?.candidate_verification?.configured,
  );
  const checkReportingConfigured = Boolean(
    capabilitiesQuery.data?.github?.incident_check_reporting?.configured,
  );
  const [draft, setDraft] = useState<RepositoryAutomationSettingsInput>(DEFAULT_AUTOMATION);
  const [candidateDraft, setCandidateDraft] = useState<CandidateVerificationSettingsInput>(
    DEFAULT_CANDIDATE_VERIFICATION,
  );

  useEffect(() => {
    if (!selectedSlugId && sessions.length) setSelectedSlugId(sessions[0].slugId);
  }, [selectedSlugId, sessions]);

  useEffect(() => {
    const saved = automationQuery.data;
    setDraft(saved ? {
      enabled: saved.enabled,
      workflow_names: saved.workflow_names,
      include_branches: saved.include_branches,
      exclude_branches: saved.exclude_branches,
      daily_investigation_limit: saved.daily_investigation_limit,
      github_check_reporting_enabled: saved.github_check_reporting_enabled,
    } : DEFAULT_AUTOMATION);
  }, [automationQuery.data, selectedSlugId]);

  useEffect(() => {
    const saved = candidateVerificationQuery.data;
    setCandidateDraft(saved
      ? {
        enabled: saved.enabled,
        installation_id: "",
        workflow_path: saved.workflow_path ?? "",
      }
      : DEFAULT_CANDIDATE_VERIFICATION);
  }, [candidateVerificationQuery.data, selectedSlugId]);

  const save = useMutation({
    mutationFn: () => updateRepositoryAutomationSettings(selectedSlugId, draft),
  });
  const saveCandidateVerification = useMutation({
    mutationFn: () => updateCandidateVerificationSettings(selectedSlugId, candidateDraft),
    onSuccess: () => {
      void candidateVerificationQuery.refetch();
    },
  });

  if (sessionsPending) return <Skeleton className="mt-6 h-52 w-full rounded-xl" />;
  if (!sessions.length) return null;

  return (
    <section className="mt-6 rounded-xl border border-border bg-card p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">Repository automation</h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
            Opt in to signed failed-run intake for one connected repository. This gathers read-only evidence only; it never applies a repair or authorizes a push.
          </p>
        </div>
        {automationQuery.data?.updated_at ? <span className="text-xs text-muted-foreground">Saved {dateTime(automationQuery.data.updated_at)}</span> : null}
      </div>
      <form
        className="mt-5 grid gap-4 lg:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (selectedSlugId) save.mutate();
        }}
      >
        <label className="grid gap-1.5 text-sm font-medium">
          Repository session
          <select
            value={selectedSlugId}
            onChange={(event) => setSelectedSlugId(event.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm font-normal"
          >
            {sessions.map((session) => <option key={session.slugId} value={session.slugId}>{session.repoName} {session.branchName ? `(${session.branchName})` : ""}</option>)}
          </select>
        </label>
        <label className="flex min-h-9 items-center gap-3 rounded-md border border-input bg-background px-3 text-sm">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
            className="size-4 accent-primary"
          />
          Investigate future failed workflow runs
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Workflow names <span className="font-normal text-muted-foreground">(optional, comma-separated)</span>
          <Input value={listText(draft.workflow_names)} onChange={(event) => setDraft((current) => ({ ...current, workflow_names: listValue(event.target.value) }))} placeholder="Backend CI, frontend-build" />
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Daily investigation limit
          <Input type="number" min={1} max={25} value={draft.daily_investigation_limit} onChange={(event) => setDraft((current) => ({ ...current, daily_investigation_limit: Math.min(25, Math.max(1, Number(event.target.value) || 1)) }))} />
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Include branches <span className="font-normal text-muted-foreground">(optional)</span>
          <Input value={listText(draft.include_branches)} onChange={(event) => setDraft((current) => ({ ...current, include_branches: listValue(event.target.value) }))} placeholder="main, release/*" />
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Exclude branches <span className="font-normal text-muted-foreground">(optional)</span>
          <Input value={listText(draft.exclude_branches)} onChange={(event) => setDraft((current) => ({ ...current, exclude_branches: listValue(event.target.value) }))} placeholder="dependabot/*" />
        </label>
        <label className="flex min-h-9 items-start gap-3 rounded-md border border-input bg-background px-3 py-2 text-sm lg:col-span-2">
          <input
            type="checkbox"
            checked={draft.github_check_reporting_enabled}
            disabled={!checkReportingConfigured}
            onChange={(event) => setDraft((current) => ({ ...current, github_check_reporting_enabled: event.target.checked }))}
            className="mt-0.5 size-4 accent-primary"
          />
          <span>
            <span className="block font-medium">Publish a neutral GitHub Check after evidence collection</span>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              {checkReportingConfigured
                ? "Requires this repository's verified App binding and Checks: write. The Check links to the incident and never claims a repair or CI pass."
                : (capabilitiesQuery.data?.github?.incident_check_reporting?.unavailable_reason ?? "GitHub Check reporting is not configured on this deployment.")}
            </span>
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
          <Button type="submit" disabled={!selectedSlugId || save.isPending}>
            {save.isPending ? "Saving…" : "Save automation settings"}
          </Button>
          {selectedSession ? <span className="text-xs text-muted-foreground">Settings are bound to {selectedSession.repoName}; repository identity is verified server-side.</span> : null}
          {save.isError ? <span className="text-sm text-destructive">Settings could not be saved. Reconnect GitHub if repository access changed.</span> : null}
          {save.isSuccess ? <span className="text-sm text-emerald-700 dark:text-emerald-300">Automation settings saved.</span> : null}
        </div>
      </form>
      <section className="mt-6 border-t border-border pt-5" aria-labelledby="candidate-verification-heading">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 id="candidate-verification-heading" className="font-semibold">Candidate verification</h3>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
              Optionally bind a GitHub App installation and one repository-owned workflow for a reviewed candidate branch. This is disabled by default. Saving this setting does not dispatch a workflow, write a patch, or authorize delivery.
            </p>
          </div>
          {candidateVerificationQuery.data?.enabled ? <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300">Opted in</Badge> : <Badge variant="outline">Disabled</Badge>}
        </div>
        {!candidateVerificationConfigured ? (
          <p className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm leading-6 text-amber-800 dark:text-amber-200">
            {capabilitiesQuery.data?.github?.candidate_verification?.unavailable_reason ?? "A server-side GitHub App is not configured, so targeted candidate verification is unavailable."} Read-only CI investigation and normal exact-diff approval remain available.
          </p>
        ) : null}
        <form
          className="mt-4 grid gap-4 lg:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (selectedSlugId) saveCandidateVerification.mutate();
          }}
        >
          <label className="flex min-h-9 items-center gap-3 rounded-md border border-input bg-background px-3 text-sm lg:col-span-2">
            <input
              type="checkbox"
              checked={candidateDraft.enabled}
              disabled={!candidateVerificationConfigured}
              onChange={(event) => setCandidateDraft((current) => ({ ...current, enabled: event.target.checked }))}
              className="size-4 accent-primary"
            />
            Enable separately approved candidate verification for this repository
          </label>
          <label className="grid gap-1.5 text-sm font-medium">
            GitHub App installation ID
            <Input
              value={candidateDraft.installation_id ?? ""}
              disabled={!candidateDraft.enabled || !candidateVerificationConfigured}
              onChange={(event) => setCandidateDraft((current) => ({ ...current, installation_id: event.target.value.trim() }))}
              placeholder={candidateVerificationQuery.data?.enabled ? "Saved installation remains server-side" : "For example: 12345678"}
              inputMode="numeric"
            />
            <span className="text-xs font-normal leading-5 text-muted-foreground">The installation must already be authorized for this exact repository. It is verified server-side and is never returned to the browser after saving.</span>
          </label>
          <label className="grid gap-1.5 text-sm font-medium">
            Reviewed workflow path
            <Input
              value={candidateDraft.workflow_path ?? ""}
              disabled={!candidateDraft.enabled || !candidateVerificationConfigured}
              onChange={(event) => setCandidateDraft((current) => ({ ...current, workflow_path: event.target.value.trim() }))}
              placeholder=".github/workflows/base64-candidate-verify.yml"
            />
            <span className="text-xs font-normal leading-5 text-muted-foreground">Only a repository-owned <code>.github/workflows/*.yml</code> file is accepted. Its <code>run:</code> fields are never used as Base64 commands.</span>
          </label>
          <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
            <Button type="submit" disabled={!selectedSlugId || saveCandidateVerification.isPending || !candidateVerificationConfigured}>
              {saveCandidateVerification.isPending ? "Saving…" : "Save candidate verification"}
            </Button>
            {candidateVerificationQuery.data?.enabled ? <span className="text-xs text-muted-foreground">Bound to {candidateVerificationQuery.data.repository_full_name ?? selectedSession?.repoName} · {candidateVerificationQuery.data.workflow_path}</span> : null}
            {saveCandidateVerification.isError ? <span className="text-sm text-destructive">Candidate verification could not be saved. Check the App installation and workflow path.</span> : null}
            {saveCandidateVerification.isSuccess ? <span className="text-sm text-emerald-700 dark:text-emerald-300">Candidate verification setting saved. Each run still requires a separate exact approval.</span> : null}
          </div>
        </form>
      </section>
    </section>
  );
}

function IncidentInbox() {
  const { data, isPending, isError, refetch, isFetching } = useQuery({
    queryKey: ["incidents"],
    queryFn: getIncidents,
    staleTime: 20_000,
  });
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const incidents = data?.incidents ?? EMPTY_INCIDENTS;
  const categories = useMemo(
    () => [...new Set(incidents.flatMap((incident) => incident.failureCategories))].sort(),
    [incidents],
  );
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return incidents.filter((incident) => {
      const matchesQuery = !normalized || [incident.repositoryName, incident.workflowName, incident.branch, incident.headSha]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalized));
      const matchesStatus = status === "all" || incident.status === status;
      const matchesCategory = category === "all" || incident.failureCategories.includes(category);
      const needsAttention = incident.status === "needs_attention" || incident.status === "failed" || incident.incomplete;
      return matchesQuery && matchesStatus && matchesCategory && (!attentionOnly || needsAttention);
    });
  }, [attentionOnly, category, incidents, query, status]);

  return (
    <main className="h-full overflow-y-auto bg-background">
      <header className="border-b border-border px-6 py-6 sm:px-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">CI recovery</p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight">Incident Inbox</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Failed GitHub Actions runs that your repositories explicitly opted into. Evidence collection is read-only; a repair still requires exact review and approval.
            </p>
          </div>
          <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
            <RefreshCw className={cn("mr-2 size-4", isFetching && "animate-spin")} /> Refresh
          </Button>
        </div>
      </header>
      <div className="mx-auto max-w-6xl px-6 py-6 sm:px-8">
        <AutomationSettingsPanel />
        <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
          <div className="grid gap-3 md:grid-cols-[1fr_180px_200px_auto]">
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter repository, workflow, branch, or SHA" aria-label="Filter incidents" />
            <select value={status} onChange={(event) => setStatus(event.target.value)} className="h-9 rounded-md border border-input bg-background px-3 text-sm">
              <option value="all">All statuses</option>
              {[...new Set(incidents.map((incident) => incident.status))].sort().map((value) => <option key={value} value={value}>{prettify(value)}</option>)}
            </select>
            <select value={category} onChange={(event) => setCategory(event.target.value)} className="h-9 rounded-md border border-input bg-background px-3 text-sm">
              <option value="all">All categories</option>
              {categories.map((value) => <option key={value} value={value}>{prettify(value)}</option>)}
            </select>
            <Button variant={attentionOnly ? "default" : "outline"} onClick={() => setAttentionOnly((current) => !current)}>
              <Filter className="mr-2 size-4" /> Needs attention
            </Button>
          </div>
        </section>

        <section className="mt-6 space-y-3" aria-live="polite">
          {isPending ? Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-36 w-full rounded-xl" />) : null}
          {isError ? (
            <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-5 text-sm">
              <p className="flex items-center gap-2 font-medium text-destructive"><AlertCircle className="size-4" /> Incidents could not be loaded.</p>
              <p className="mt-2 text-muted-foreground">Check that the backend is reachable, then retry. Existing incident data has not been changed.</p>
            </div>
          ) : null}
          {!isPending && !isError && visible.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-muted/20 p-8 text-center">
              <ShieldCheck className="mx-auto size-7 text-primary" />
              <h2 className="mt-3 font-semibold">No matching incidents</h2>
              <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted-foreground">Connect a repository, enable its automation setting, and configure the signed GitHub webhook to let Base64 Ops collect future failed-run evidence.</p>
            </div>
          ) : null}
          {visible.map((incident) => <IncidentListItem key={incident.id} incident={incident} />)}
        </section>
      </div>
    </main>
  );
}

function IncidentDetail() {
  const { incidentId = "" } = useParams();
  const queryClient = useQueryClient();
  const [feedbackDisposition, setFeedbackDisposition] = useState<"helpful" | "incorrect" | "needs_more_evidence">("helpful");
  const [feedbackCorrection, setFeedbackCorrection] = useState("");
  const { data: incident, isPending, isError } = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => getIncident(incidentId),
    enabled: Boolean(incidentId),
    staleTime: 20_000,
  });
  const exportMutation = useMutation({
    mutationFn: (format: "json" | "markdown") => downloadIncidentExport(incidentId, format),
  });
  const feedbackMutation = useMutation({
    mutationFn: () => submitIncidentFeedback(incidentId, {
      disposition: feedbackDisposition,
      correction: feedbackCorrection,
    }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["incident", incidentId], updated);
      void queryClient.invalidateQueries({ queryKey: ["incidents"] });
      setFeedbackCorrection("");
    },
  });
  if (isPending) return <main className="h-full overflow-y-auto p-8"><Skeleton className="mx-auto h-96 max-w-5xl rounded-xl" /></main>;
  if (isError || !incident) return <main className="h-full overflow-y-auto p-8"><div className="mx-auto max-w-5xl rounded-xl border border-destructive/40 bg-destructive/5 p-6"><AlertCircle className="size-5 text-destructive" /><h1 className="mt-3 font-semibold">Incident unavailable</h1><p className="mt-2 text-sm text-muted-foreground">It may have expired or you may not have access to it.</p><Button asChild variant="outline" className="mt-4"><Link to="/incidents"><ArrowLeft className="mr-2 size-4" />Back to inbox</Link></Button></div></main>;
  return (
    <main className="h-full overflow-y-auto bg-background">
      <div className="mx-auto max-w-5xl px-6 py-7 sm:px-8">
        <Link to="/incidents" className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="mr-2 size-4" />Incident Inbox</Link>
        <div className="mt-6 flex flex-wrap items-start justify-between gap-4">
          <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">GitHub Actions failure</p><h1 className="mt-2 text-2xl font-semibold tracking-tight">{incident.workflowName ?? "Workflow run"}</h1><p className="mt-2 text-sm text-muted-foreground">{incident.repositoryName} · run #{incident.workflowRunId} · observed {dateTime(incident.createdAt)}</p></div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge incident={incident} />
            <Button size="sm" variant="outline" onClick={() => exportMutation.mutate("markdown")} disabled={exportMutation.isPending}>
              <Download className="mr-2 size-3.5" /> Export Markdown
            </Button>
            <Button size="sm" variant="outline" onClick={() => exportMutation.mutate("json")} disabled={exportMutation.isPending}>
              <Download className="mr-2 size-3.5" /> JSON
            </Button>
          </div>
        </div>
        {exportMutation.isError ? <p className="mt-3 text-sm text-destructive">The redacted incident export could not be downloaded. Please retry.</p> : null}
        <section className="mt-7 grid gap-4 md:grid-cols-3"><div className="rounded-xl border border-border bg-card p-4"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Run commit</p><p className="mt-2 font-mono text-sm">{shortSha(incident.headSha)}</p></div><div className="rounded-xl border border-border bg-card p-4"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence strength</p><p className="mt-2 capitalize">{incident.evidenceStrength}</p></div><div className="rounded-xl border border-border bg-card p-4"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Current state</p><p className="mt-2 text-sm">{incident.runIsHistorical ? "Historical run compared separately" : "Same run/current context"}</p></div></section>
        <section className="mt-4 rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Evidence-backed assessment</h2><p className="mt-3 text-sm leading-6 text-muted-foreground">{incident.diagnosisSummary ?? "Diagnosis is pending evidence collection."}</p>{incident.failureCategories.length ? <div className="mt-4 flex flex-wrap gap-2">{incident.failureCategories.map((category) => <Badge key={category} variant="secondary">{prettify(category)}</Badge>)}</div> : null}</section>
        <section className="mt-4 rounded-xl border border-border bg-card p-5">
          <h2 className="font-semibold">Was this assessment useful?</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">Your note is redacted and saved as an untrusted review annotation. It cannot change evidence, approve a patch, or alter repository access.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {(["helpful", "incorrect", "needs_more_evidence"] as const).map((disposition) => <Button key={disposition} type="button" size="sm" variant={feedbackDisposition === disposition ? "default" : "outline"} onClick={() => setFeedbackDisposition(disposition)}>{prettify(disposition)}</Button>)}
          </div>
          <Textarea className="mt-3 min-h-20" value={feedbackCorrection} maxLength={2000} onChange={(event) => setFeedbackCorrection(event.target.value)} placeholder={feedbackDisposition === "incorrect" ? "What evidence or conclusion appears incorrect?" : "Optional note for a later review"} aria-label="Incident feedback note" />
          <div className="mt-3 flex flex-wrap items-center gap-3"><Button size="sm" type="button" onClick={() => feedbackMutation.mutate()} disabled={feedbackMutation.isPending || (feedbackDisposition === "incorrect" && feedbackCorrection.trim().length < 3)}>{feedbackMutation.isPending ? "Savingâ€¦" : "Save feedback"}</Button>{feedbackMutation.isError ? <span className="text-sm text-destructive">Feedback could not be saved. Please retry.</span> : null}{incident.feedback ? <span className="text-xs text-muted-foreground">Last feedback: {prettify(incident.feedback.disposition)} {incident.feedback.updatedAt ? `â€” ${dateTime(incident.feedback.updatedAt)}` : ""}</span> : null}</div>
        </section>
        {incident.flakySignal === "suspected" ? <section className="mt-4 rounded-xl border border-violet-500/35 bg-violet-500/5 p-5"><h2 className="font-semibold">Suspected flaky-test signal</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">The bounded workflow history includes both failed and successful executions on this same commit. {incident.failedTestIdentifiers?.length ? `The failed output identified ${incident.failedTestIdentifiers.join(", ")}. ` : "No stable test identifier was extracted. "}This is not a flaky-test verdict and Base64 Ops will not automatically rerun CI.</p></section> : null}
        {incident.flakyObservations?.length ? <section className="mt-4 rounded-xl border border-violet-500/35 bg-violet-500/5 p-5"><h2 className="font-semibold">Observed test-history signals</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">These are bounded observations from this failed log and comparable GitHub Actions runs. They never execute a retry, quarantine a test, or authorize a change.</p><div className="mt-4 space-y-3">{incident.flakyObservations.map((observation) => <div key={observation.id} className="rounded-lg border border-violet-500/20 bg-background/60 p-3 text-sm"><div className="flex flex-wrap items-center justify-between gap-2"><code className="break-all text-xs">{observation.test_identifier}</code><Badge variant="outline" className="capitalize">{prettify(observation.assessment)}</Badge></div><p className="mt-2 text-muted-foreground">Same commit: {observation.same_sha_failure_count} failed run(s), {observation.same_sha_success_count} successful run(s).</p>{observation.limitations.map((limitation) => <p key={limitation} className="mt-1 text-xs leading-5 text-muted-foreground">Limitation: {limitation}</p>)}</div>)}</div></section> : null}
        {incident.sameShaSuccess ? <section className="mt-4 rounded-xl border border-amber-500/35 bg-amber-500/5 p-5"><h2 className="font-semibold">Later success on the same commit</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Workflow run #{incident.sameShaSuccess.workflowRunId} succeeded on {shortSha(incident.sameShaSuccess.headSha)} at {dateTime(incident.sameShaSuccess.observedAt)}. This can indicate a transient condition, but it does not prove the earlier failure was flaky or close this incident automatically.</p></section> : null}
        <section className="mt-4 grid gap-4 md:grid-cols-2"><div className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Relevant files</h2>{incident.relevantPaths.length ? <ul className="mt-3 space-y-2 font-mono text-xs text-muted-foreground">{incident.relevantPaths.map((path) => <li key={path}>{path}</li>)}</ul> : <p className="mt-3 text-sm text-muted-foreground">No repository file was selected from the bounded evidence.</p>}</div><div className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Evidence and limitations</h2><p className="mt-3 text-sm text-muted-foreground">{incident.evidenceIds.length} CI evidence item(s) and {incident.workflowEvidenceIds.length} workflow item(s) are retained with source provenance.</p>{incident.limitations.length ? <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-muted-foreground">{incident.limitations.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="mt-3 text-sm text-emerald-700 dark:text-emerald-300">No collection limitation was recorded.</p>}</div></section>
        {incident.history ? <section className="mt-4 rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Comparable workflow history</h2><p className="mt-3 text-sm leading-6 text-muted-foreground">{prettify(incident.history.assessment)} across {incident.history.compared_run_ids.length} bounded comparable run(s). This is correlation evidence, not proof of causation or flakiness.</p><div className="mt-4 grid gap-3 text-sm sm:grid-cols-3"><p><span className="block text-xs uppercase tracking-wide text-muted-foreground">Prior success</span>{incident.history.last_successful_run_id ? `#${incident.history.last_successful_run_id}` : "Unavailable"}</p><p><span className="block text-xs uppercase tracking-wide text-muted-foreground">Same-SHA failures</span>{incident.history.same_sha_failure_count}</p><p><span className="block text-xs uppercase tracking-wide text-muted-foreground">Same-SHA successes</span>{incident.history.same_sha_success_count}</p></div>{incident.history.workflow_comparison_note ? <p className="mt-4 text-sm leading-6 text-muted-foreground">Workflow comparison: {incident.history.workflow_comparison_note}</p> : null}{incident.history.dependency_comparison_note ? <p className="mt-2 text-sm leading-6 text-muted-foreground">Dependency comparison: {incident.history.dependency_comparison_note}</p> : null}{incident.history.runner_comparison_note ? <p className="mt-2 text-sm leading-6 text-muted-foreground">Runner configuration: {incident.history.runner_comparison_note}</p> : null}{incident.history.limitation ? <p className="mt-4 text-sm text-muted-foreground">Limitation: {incident.history.limitation}</p> : null}</section> : null}
        {incident.memoryEntryCount ? <section className="mt-4 rounded-xl border border-sky-500/30 bg-sky-500/5 p-5"><h2 className="font-semibold">Historical incident intelligence recorded</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">This evidence-supported incident was added as partially verified historical context for future investigations. It cannot authorize a repair and remains lower priority than current repository and CI evidence.</p></section> : null}
        {incident.githubCheckReport ? <section className="mt-4 rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">GitHub Check reporting</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{incident.githubCheckReport.status === "published" ? "A neutral Check was published after the evidence-backed investigation. It reports analysis availability, not a repaired or passing workflow." : (incident.githubCheckReport.message ?? "GitHub Check reporting was unavailable; the incident remains available here.")}</p>{incident.githubCheckReport.url ? <a className="mt-3 inline-flex text-sm text-primary hover:underline" href={incident.githubCheckReport.url} target="_blank" rel="noreferrer">Open GitHub Check <ExternalLink className="ml-1 size-3.5" /></a> : null}</section> : null}
        {incident.groupTimeline?.length ? <section className="mt-4 rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Repeated-failure timeline</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">This bounded timeline records equivalent failed runs. Repetition is a triage signal, not proof that one file or change caused the failure.</p><ol className="mt-4 space-y-3 border-l border-border pl-4">{incident.groupTimeline.map((entry) => <li key={`${entry.workflowRunId}-${entry.observedAt}`} className="relative text-sm"><span className="absolute -left-[1.32rem] top-1.5 size-2 rounded-full bg-primary" /><p className="font-medium">Run #{entry.workflowRunId} <span className="font-mono text-xs text-muted-foreground">{shortSha(entry.headSha)}</span></p><p className="mt-1 text-xs text-muted-foreground">{dateTime(entry.observedAt)} · {entry.failedJobNames.length ? entry.failedJobNames.join(", ") : "failed job unavailable"}</p></li>)}</ol></section> : null}
        <section className="mt-4 rounded-xl border border-primary/30 bg-primary/5 p-5"><h2 className="font-semibold">Next safe action</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{incident.diagnosis?.recommended_next_action ?? "Open the related repository session to review the exact evidence and, if supported, create a constrained proposal."} An incident never authorizes a repository mutation by itself.</p><Button asChild className="mt-4" variant="outline"><Link to="/new">Open Base64 Ops <ExternalLink className="ml-2 size-4" /></Link></Button></section>
      </div>
    </main>
  );
}

export { IncidentInbox, IncidentDetail };
