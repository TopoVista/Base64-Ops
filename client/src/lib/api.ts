import type { AuthResponse } from "@/types/auth.type";
import API from "./axios-client";
import { getAccessToken } from "./axios-client";
import { BASE_API_URL } from "./env";
import type {
  ApprovalRequest,
  AgentMessage,
  RagSource,
} from "@/types/agent.type";
import type {
  SessionRunHistoryResponse,
  SessionsResponse,
  SingleSessionResponse,
} from "@/types/session.type";
import type {
  GithubConnectResponse,
  GithubReposResponse,
} from "@/types/github.type";
import type {
  IncidentRecord,
  IncidentFeedbackInput,
  IncidentsResponse,
  FlakyTestObservation,
  PreventionRule,
  PreventionRuleGovernance,
  FleetIntelligence,
  CandidateVerificationRun,
  CandidateVerificationSettings,
  CandidateVerificationSettingsInput,
  RepositoryAutomationSettings,
  RepositoryAutomationSettingsInput,
} from "@/types/incident.type";

export type ProductAssistantResponse = {
  conversationId: string;
  answer: string;
  suggestedPrompts: string[];
};

export type BackendHealth = {
  message: string;
  status: "healthy" | "degraded";
  database: "connected" | "unavailable";
  stack: string;
};

export type UsageEntitlement = {
  tier: "free" | "pro" | "team";
  monthly_agent_run_limit: number;
  period_key: string;
  consumed_agent_runs: number;
  remaining_agent_runs: number;
  monthly_model_token_limit: number;
  consumed_model_tokens: number;
  remaining_model_tokens: number;
};

export const getUsageEntitlement = async (): Promise<UsageEntitlement> => {
  const response = await API.get<UsageEntitlement>("/billing/entitlement");
  return response.data;
};

export const startSubscriptionCheckout = async (tier: "pro" | "team") => {
  const response = await API.post<{ checkoutUrl: string }>(
    "/billing/checkout",
    { tier },
  );
  return response.data;
};

export const getBackendHealth = async (): Promise<BackendHealth> => {
  const response = await API.get<BackendHealth>("/health");
  return response.data;
};

export const getIncidents = async (): Promise<IncidentsResponse> => {
  const response = await API.get<IncidentsResponse>("/incidents", {
    params: { limit: 100 },
  });
  return response.data;
};

export const getIncident = async (incidentId: string): Promise<IncidentRecord> => {
  const response = await API.get<IncidentRecord>(`/incidents/${encodeURIComponent(incidentId)}`);
  return response.data;
};

export const getFlakyTestObservations = async (): Promise<FlakyTestObservation[]> => {
  const response = await API.get<FlakyTestObservation[]>("/incidents/flaky-tests", {
    params: { limit: 100 },
  });
  return response.data;
};

export const getPreventionRules = async (
  slugId: string,
): Promise<PreventionRule[]> => {
  const response = await API.get<PreventionRule[]>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/prevention-rules`,
    { params: { limit: 30 } },
  );
  return response.data;
};

/** Records an explicit human policy decision for an advisory-only rule. */
export const governPreventionRule = async (
  slugId: string,
  ruleId: string,
  action: "approve" | "rollback",
): Promise<PreventionRuleGovernance> => {
  const response = await API.post<PreventionRuleGovernance>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/prevention-rules/${encodeURIComponent(ruleId)}/${action}`,
  );
  return response.data;
};

/** Returns bounded tenant-scoped correlations, never causal conclusions. */
export const getFleetIntelligence = async (): Promise<FleetIntelligence> => {
  const response = await API.get<FleetIntelligence>("/incidents/fleet-intelligence", {
    params: { limit: 50 },
  });
  return response.data;
};

export const submitIncidentFeedback = async (
  incidentId: string,
  feedback: IncidentFeedbackInput,
): Promise<IncidentRecord> => {
  const response = await API.put<IncidentRecord>(
    `/incidents/${encodeURIComponent(incidentId)}/feedback`,
    feedback,
  );
  return response.data;
};

export const getRepositoryAutomationSettings = async (
  slugId: string,
): Promise<RepositoryAutomationSettings | null> => {
  const response = await API.get<RepositoryAutomationSettings | null>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/automation`,
  );
  return response.data;
};

export const updateRepositoryAutomationSettings = async (
  slugId: string,
  settings: RepositoryAutomationSettingsInput,
): Promise<RepositoryAutomationSettings> => {
  const response = await API.put<RepositoryAutomationSettings>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/automation`,
    settings,
  );
  return response.data;
};

export const getCandidateVerificationSettings = async (
  slugId: string,
): Promise<CandidateVerificationSettings | null> => {
  const response = await API.get<CandidateVerificationSettings | null>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/candidate-verification`,
  );
  return response.data;
};

/** Saves a reviewed GitHub App installation/workflow binding; it does not run it. */
export const updateCandidateVerificationSettings = async (
  slugId: string,
  settings: CandidateVerificationSettingsInput,
): Promise<CandidateVerificationSettings> => {
  const response = await API.put<CandidateVerificationSettings>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/candidate-verification`,
    settings,
  );
  return response.data;
};

/** Creates an immutable approval only; dispatch occurs only after approval. */
export const requestSessionCandidateVerificationApproval = async (
  slugId: string,
  planId: string,
): Promise<{ approval: ApprovalRequest; reused: boolean }> => {
  const response = await API.post(
    `/session/${encodeURIComponent(slugId)}/delivery-plan/${encodeURIComponent(planId)}/candidate-verification/approval`,
  );
  return response.data as { approval: ApprovalRequest; reused: boolean };
};

export const getCandidateVerificationRun = async (
  verificationRunId: string,
): Promise<CandidateVerificationRun> => {
  const response = await API.get<CandidateVerificationRun>(
    `/incidents/verification-runs/${encodeURIComponent(verificationRunId)}`,
  );
  return response.data;
};

export const getLatestCandidateVerificationRun = async (
  slugId: string,
): Promise<CandidateVerificationRun | null> => {
  const response = await API.get<CandidateVerificationRun | null>(
    `/incidents/sessions/${encodeURIComponent(slugId)}/candidate-verification/latest-run`,
  );
  return response.data;
};

export const refreshCandidateVerificationRun = async (
  verificationRunId: string,
): Promise<CandidateVerificationRun> => {
  const response = await API.post<CandidateVerificationRun>(
    `/incidents/verification-runs/${encodeURIComponent(verificationRunId)}/refresh`,
  );
  return response.data;
};

export const downloadIncidentExport = async (
  incidentId: string,
  format: "json" | "markdown",
): Promise<void> => {
  const response = await API.get<Blob>(`/incidents/${encodeURIComponent(incidentId)}/export`, {
    params: { format },
    responseType: "blob",
  });
  const objectUrl = window.URL.createObjectURL(response.data);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `base64-incident-${incidentId}.${format === "markdown" ? "md" : "json"}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
};

/** Historical run replay is read-only: the server never reruns agent tools or mutations. */
export const getDevRunReplay = async <T>(runId: string): Promise<T> => {
  const response = await API.get<T>(`/dev/runs/${encodeURIComponent(runId)}`);
  return response.data;
};

export const getSystemCapabilities = async <T>(): Promise<T> => {
  const response = await API.get<T>("/system/capabilities");
  return response.data;
};

/**
 * Upserts the Clerk-authenticated user in the backend DB and returns
 * the backend profile (githubConnected, _id, etc.).
 * The Clerk Bearer token is attached automatically by the axios interceptor.
 */
export const syncClerkUser = async (): Promise<AuthResponse> => {
  const response = await API.post<AuthResponse>("/auth/me");
  return response.data;
};

export const getCurrentUser = async (): Promise<AuthResponse> => {
  const response = await API.get<AuthResponse>("/auth/me");
  return response.data;
};

export const getUserSessions = async (): Promise<SessionsResponse> => {
  const response = await API.get<SessionsResponse>("/session/all");
  return response.data;
};

export const getGithubRepos = async (): Promise<GithubReposResponse> => {
  const response = await API.get<GithubReposResponse>("/github/repos");
  return response.data;
};

export const connectGithub = async (
  redirectTo?: string,
): Promise<GithubConnectResponse> => {
  const response = await API.get<GithubConnectResponse>("/github/connect", {
    params: redirectTo ? { redirectTo } : undefined,
  });
  return response.data;
};

export const decideSessionApproval = async (
  slugId: string,
  approvalId: string,
  data: {
    decision: "approve" | "edit" | "reject";
    editedArgs?: Record<string, unknown>;
    note?: string;
  },
): Promise<{
  approval: ApprovalRequest;
  message?: AgentMessage;
  result?: unknown;
}> => {
  const response = await API.post(
    `/session/${slugId}/approval/${approvalId}`,
    data,
  );
  return response.data;
};

export const reindexSessionRag = async (
  slugId: string,
  repository?: { repoUrl: string; defaultBranch?: string },
): Promise<{
  success: boolean;
  indexed: number;
  status?: "queued" | "indexing" | "ready" | "empty" | "failed";
  reason?: string;
  storage?: "lexical" | "vector";
  vectorWarning?: string;
  truncated?: boolean;
}> => {
  const response = await API.post(`/session/${slugId}/rag/reindex`, repository);
  return response.data;
};

export const getSessionRagSources = async (
  slugId: string,
): Promise<{ sources: RagSource[] }> => {
  const response = await API.get(`/session/${slugId}/rag/sources`);
  return response.data;
};

export const refreshLatestSessionCi = async (slugId: string) => {
  const response = await API.post<{
    status: "found" | "no_failed_runs";
    runId?: number;
    workflowName?: string;
    failedJobs?: number;
    incomplete?: boolean;
    limitations?: string[];
    message?: string;
  }>(`/session/${slugId}/ci/refresh`);
  return response.data;
};

export const createRunbook = async (data: {
  title: string;
  content: string;
  tags?: string[];
}) => {
  const response = await API.post("/session/runbooks", data);
  return response.data;
};

export const getRunbooks = async () => {
  const response = await API.get("/session/runbooks");
  return response.data;
};

export const getSessionBySlug = async (
  slugId: string,
): Promise<SingleSessionResponse> => {
  const response = await API.get<SingleSessionResponse>(`/session/${slugId}`);
  return response.data;
};

export const getSessionRunHistory = async (
  slugId: string,
): Promise<SessionRunHistoryResponse> => {
  const response = await API.get<SessionRunHistoryResponse>(
    `/session/${slugId}/runs`,
  );
  return response.data;
};

export const getSessionCodeFiles = async (
  slugId: string,
): Promise<{
  headSha?: string;
  files: Array<{ path: string; bytes: number }>;
}> => {
  const response = await API.get(`/session/${slugId}/code/files`);
  return response.data;
};

export const getSessionRecentCommits = async (
  slugId: string,
): Promise<{
  headSha?: string;
  commits: Array<{
    sha: string;
    shortSha: string;
    author: string;
    date: string;
    subject: string;
  }>;
}> => {
  const response = await API.get(`/session/${slugId}/code/commits`);
  return response.data;
};

export const getSessionDependencyGraph = async (
  slugId: string,
): Promise<{
  headSha?: string;
  nodes: string[];
  edges: Array<{ from: string; to: string }>;
  mermaid: string;
  truncated: boolean;
}> => {
  const response = await API.get(`/session/${slugId}/code/dependency-graph`);
  return response.data;
};

export type EngineeringGraphStatus = {
  available: boolean;
  snapshot_id?: string;
  commit_sha?: string;
  branch?: string;
  built_at?: string;
  node_count: number;
  edge_count: number;
  coverage?: {
    scanned_files: number;
    parsed_files: number;
    skipped_files: number;
    unsupported_files: number;
    truncated: boolean;
    limitations: string[];
  };
  limitations: string[];
};

export type ChangeImpactTarget = {
  node_id: string;
  node_type: string;
  name: string;
  path?: string | null;
  qualified_name?: string | null;
  distance: number;
  explanation: string;
};

export type ChangeImpactAnalysis = {
  id: string;
  delivery_plan_id: string;
  plan_base_sha: string;
  graph_commit_sha?: string | null;
  status: "available" | "unavailable";
  changed_paths: string[];
  unmapped_paths: string[];
  directly_affected: ChangeImpactTarget[];
  indirectly_affected: ChangeImpactTarget[];
  related_tests: ChangeImpactTarget[];
  related_workflows: ChangeImpactTarget[];
  truncated: boolean;
  limitations: string[];
  created_at: string;
};

/** Read-only historical CI context for the exact files in a DeliveryPlan. */
export type RegressionPreventionReport = {
  delivery_plan_id: string;
  plan_base_sha: string;
  status: "available" | "insufficient_history";
  changed_paths: string[];
  advisories: Array<{
    path: string;
    failure_category: string;
    historical_incident_ids: string[];
    historical_run_shas: string[];
    occurrence_count: number;
    prevention_rule_id?: string | null;
    promotion_state: "persisted_rule" | "live_pattern";
    message: string;
    recommendation: string;
  }>;
  threshold_description: string;
  limitations: string[];
  analyzed_at: string;
};

/** Bounded evidence linking an exact candidate verification to CI capsules. */
export type CounterfactualVerificationReport = {
  id: string;
  deliveryPlanId: string;
  patchDigest: string;
  status: "targeted_evidence_available" | "inconclusive";
  verification: {
    verificationRunId?: string | null;
    verificationLevel?: string | null;
    workflowPath?: string | null;
    githubRunId?: number | null;
  };
  matrix: Array<{
    incidentId: string;
    capsuleManifestHash: string;
    baseSha: string;
    baseline: string;
    patched: string;
    replayed: boolean;
    scope: string;
  }>;
  ledgerReceiptId: string;
  limitations: string[];
  createdAt: string;
};

export const getSessionEngineeringGraphStatus = async (
  slugId: string,
): Promise<EngineeringGraphStatus> => {
  const response = await API.get(
    `/session/${slugId}/intelligence/graph/status`,
  );
  return response.data;
};

/** Runs a graph-only impact traversal for an existing, exact delivery plan. */
export const analyzeSessionDeliveryImpact = async (
  slugId: string,
  planId: string,
): Promise<ChangeImpactAnalysis> => {
  const response = await API.post<ChangeImpactAnalysis>(
    `/session/${slugId}/delivery-plan/${planId}/impact`,
  );
  return response.data;
};

/**
 * Checks only tenant-scoped, supported historical incident summaries. It
 * creates no approval, patch, command, or GitHub mutation.
 */
export const analyzeSessionRegressionPrevention = async (
  slugId: string,
  planId: string,
): Promise<RegressionPreventionReport> => {
  const response = await API.post<RegressionPreventionReport>(
    `/session/${slugId}/delivery-plan/${planId}/regression-prevention`,
  );
  return response.data;
};

/** Records existing candidate evidence against exact incident capsules only. */
export const analyzeSessionCounterfactualVerification = async (
  slugId: string,
  planId: string,
): Promise<CounterfactualVerificationReport> => {
  const response = await API.post<CounterfactualVerificationReport>(
    `/session/${encodeURIComponent(slugId)}/delivery-plan/${encodeURIComponent(planId)}/counterfactual-verification`,
  );
  return response.data;
};

export const getSessionCodeFile = async (
  slugId: string,
  path: string,
): Promise<{
  path: string;
  headSha?: string;
  contentHash: string;
  content: string;
  truncated: boolean;
  containsRedactions: boolean;
  editable: boolean;
}> => {
  const response = await API.get(`/session/${slugId}/code/file`, {
    params: { path },
  });
  // A redacted representation is safe to inspect but must be locked in the
  // browser as well as enforced by the server-side proposal endpoint.
  return {
    ...response.data,
    truncated: Boolean(response.data.truncated || !response.data.editable),
  };
};

export const getSessionGitStatus = async (
  slugId: string,
): Promise<{
  headSha?: string;
  branch?: string;
  output: string;
  success: boolean;
}> => {
  const response = await API.get(`/session/${slugId}/code/git-status`);
  return response.data;
};

export const getSessionGitDiff = async (
  slugId: string,
): Promise<{
  headSha?: string;
  branch?: string;
  diff: string;
  truncated: boolean;
  success: boolean;
}> => {
  const response = await API.get(`/session/${slugId}/code/git-diff`);
  return response.data;
};

export const proposeSessionCodeEdit = async (
  slugId: string,
  payload: {
    path: string;
    expectedOriginalHash: string;
    proposedContent: string;
    commitMessage?: string;
  },
) => {
  const response = await API.post(`/session/${slugId}/code/propose`, payload);
  return response.data as {
    approval: { id: string; riskLevel: string };
    deliveryPlan: { id: string };
  };
};

export const updateSessionDeliveryCommitMessage = async (
  slugId: string,
  planId: string,
  commitMessage: string,
) => {
  const response = await API.patch(
    `/session/${slugId}/delivery-plan/${planId}/commit-message`,
    { commitMessage },
  );
  return response.data as {
    deliveryPlan: { id: string; title: string };
    approval: { id: string } | null;
  };
};

/** Requests an exact approval record only; it cannot apply or push code. */
export const requestSessionDeliveryApproval = async (
  slugId: string,
  planId: string,
) => {
  const response = await API.post(
    `/session/${slugId}/delivery-plan/${planId}/approval`,
  );
  return response.data as { approval: ApprovalRequest; reused: boolean };
};

export const getUserSessionsWithSearch = async (params?: {
  search?: string;
  pageSize?: number;
  pageNumber?: number;
}): Promise<SessionsResponse> => {
  const response = await API.get<SessionsResponse>("/session/all", { params });
  return response.data;
};

export const askProductAssistant = async (
  message: string,
  conversationId?: string,
  clientRequestId?: string,
): Promise<ProductAssistantResponse> => {
  const response = await API.post<ProductAssistantResponse>("/assistant/chat", {
    message,
    conversationId,
    clientRequestId,
  });
  return response.data;
};

export const applyApprovedPatch = async (
  slugId: string,
  approvalId: string,
) => {
  const response = await API.post("/apply-patch", { slugId, approvalId });
  return response.data as { result?: { pull_request_url?: string } };
};

export const streamDiagnostic = async (
  payload: {
    sessionSlugId: string;
    failedLog: string;
    gitDiff: string;
    filePath?: string;
    originalContent?: string;
    generateFix?: boolean;
    clientRequestId?: string;
  },
  onEvent: (event: string, data: Record<string, unknown>) => void,
) => {
  const request = async (refreshToken = false) => {
    const token = await getAccessToken(
      refreshToken ? { skipCache: true } : undefined,
    );
    return fetch(`${BASE_API_URL}stream-diagnostic`, {
      method: "POST",
      // Diagnostic input is idempotency-bound on the server. A single retry
      // after a Clerk 401 is therefore safe: the first request did not enter
      // the authenticated agent pipeline.
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
    });
  };
  let response = await request();
  if (response.status === 401) response = await request(true);
  if (response.status === 401) {
    throw new Error(
      "Your Base64 sign-in session could not be refreshed. Reload the page and sign in again.",
    );
  }
  if (!response.ok || !response.body) {
    // FastAPI returns structured details for an idempotency collision.  This
    // can happen when an SSE connection completes after the browser has lost
    // its response, so a bare HTTP status is not useful to the operator.
    let message = `Diagnostic request failed (${response.status}).`;
    try {
      const body = (await response.json()) as {
        detail?: string | { code?: string; message?: string };
      };
      const detail = body.detail;
      if (typeof detail === "string" && detail.trim()) {
        message = detail;
      } else if (
        typeof detail === "object" &&
        detail?.code === "AGENT_RUN_REQUEST_ALREADY_ACCEPTED"
      ) {
        message =
          "That repair request was already accepted. Refreshing the session will show its proposal if it completed; otherwise retry Generate safe repair once.";
      } else if (
        typeof detail === "object" &&
        typeof detail?.message === "string" &&
        detail.message.trim()
      ) {
        message = detail.message;
      }
    } catch {
      // Some proxies replace JSON error bodies with HTML. The safe status
      // fallback above still tells the user the request did not start.
    }
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = frame.match(/^event:\s*(.+)$/m)?.[1]?.trim();
      const raw = frame.match(/^data:\s*(.+)$/m)?.[1];
      if (event && raw) {
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          continue;
        }
        onEvent(event, data);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
};
