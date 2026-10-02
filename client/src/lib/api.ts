import type { AuthResponse } from "@/types/auth.type";
import API from "./axios-client";
import { getAccessToken } from "./axios-client";
import { BASE_API_URL } from "./env";
import type { ApprovalRequest, AgentMessage, RagSource } from "@/types/agent.type";
import type { CreatePullRequestResponse, SessionsResponse, SingleSessionResponse } from "@/types/session.type";
import type { GithubConnectResponse, GithubReposResponse } from "@/types/github.type";

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

export const getBackendHealth = async (): Promise<BackendHealth> => {
  const response = await API.get<BackendHealth>("/health");
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

export const connectGithub = async (redirectTo?: string): Promise<GithubConnectResponse> => {
  const response = await API.get<GithubConnectResponse>("/github/connect", {
    params: redirectTo ? { redirectTo } : undefined,
  });
  return response.data;
};

export const createSessionPullRequest = async (
  slugId: string,
  data: { title?: string; body?: string }
): Promise<CreatePullRequestResponse> => {
  const response = await API.post<CreatePullRequestResponse>(`/session/${slugId}/pr`, data);
  return response.data;
};

export const decideSessionApproval = async (
  slugId: string,
  approvalId: string,
  data: { decision: "approve" | "edit" | "reject"; editedArgs?: Record<string, unknown>; note?: string }
): Promise<{ approval: ApprovalRequest; message?: AgentMessage; result?: unknown }> => {
  const response = await API.post(`/session/${slugId}/approval/${approvalId}`, data);
  return response.data;
};

export const reindexSessionRag = async (
  slugId: string,
  repository?: { repoUrl: string; defaultBranch?: string },
): Promise<{ success: boolean; indexed: number; status?: "ready" | "empty" | "failed"; reason?: string; storage?: "lexical" | "vector"; vectorWarning?: string }> => {
  const response = await API.post(`/session/${slugId}/rag/reindex`, repository);
  return response.data;
};

export const getSessionRagSources = async (
  slugId: string
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

export const createRunbook = async (
  data: { title: string; content: string; tags?: string[] }
) => {
  const response = await API.post("/session/runbooks", data);
  return response.data;
};

export const getRunbooks = async () => {
  const response = await API.get("/session/runbooks");
  return response.data;
};

export const getSessionBySlug = async (slugId: string): Promise<SingleSessionResponse> => {
  const response = await API.get<SingleSessionResponse>(`/session/${slugId}`);
  return response.data;
};

export const getSessionCodeFiles = async (slugId: string): Promise<{ headSha?: string; files: Array<{ path: string; bytes: number }> }> => {
  const response = await API.get(`/session/${slugId}/code/files`);
  return response.data;
};

export const getSessionRecentCommits = async (slugId: string): Promise<{
  headSha?: string;
  commits: Array<{ sha: string; shortSha: string; author: string; date: string; subject: string }>;
}> => {
  const response = await API.get(`/session/${slugId}/code/commits`);
  return response.data;
};

export const getSessionDependencyGraph = async (slugId: string): Promise<{ headSha?: string; nodes: string[]; edges: Array<{ from: string; to: string }>; mermaid: string; truncated: boolean }> => {
  const response = await API.get(`/session/${slugId}/code/dependency-graph`);
  return response.data;
};

export const getSessionCodeFile = async (slugId: string, path: string): Promise<{ path: string; headSha?: string; contentHash: string; content: string; truncated: boolean; containsRedactions: boolean; editable: boolean }> => {
  const response = await API.get(`/session/${slugId}/code/file`, { params: { path } });
  // A redacted representation is safe to inspect but must be locked in the
  // browser as well as enforced by the server-side proposal endpoint.
  return { ...response.data, truncated: Boolean(response.data.truncated || !response.data.editable) };
};

export const getSessionGitStatus = async (slugId: string): Promise<{
  headSha?: string; branch?: string; output: string; success: boolean;
}> => {
  const response = await API.get(`/session/${slugId}/code/git-status`);
  return response.data;
};

export const getSessionGitDiff = async (slugId: string): Promise<{
  headSha?: string; branch?: string; diff: string; truncated: boolean; success: boolean;
}> => {
  const response = await API.get(`/session/${slugId}/code/git-diff`);
  return response.data;
};

export const proposeSessionCodeEdit = async (
  slugId: string,
  payload: { path: string; expectedOriginalHash: string; proposedContent: string; commitMessage?: string },
) => {
  const response = await API.post(`/session/${slugId}/code/propose`, payload);
  return response.data as { approval: { id: string; riskLevel: string }; deliveryPlan: { id: string } };
};

export const updateSessionDeliveryCommitMessage = async (
  slugId: string,
  planId: string,
  commitMessage: string,
) => {
  const response = await API.patch(`/session/${slugId}/delivery-plan/${planId}/commit-message`, { commitMessage });
  return response.data as { deliveryPlan: { id: string; title: string }; approval: { id: string } | null };
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
): Promise<ProductAssistantResponse> => {
  const response = await API.post<ProductAssistantResponse>("/assistant/chat", {
    message,
    conversationId,
  });
  return response.data;
};

export const applyApprovedPatch = async (slugId: string, approvalId: string) => {
  const response = await API.post("/apply-patch", { slugId, approvalId });
  return response.data as { result?: { pull_request_url?: string } };
};

export const streamDiagnostic = async (
  payload: { sessionSlugId: string; failedLog: string; gitDiff: string; filePath?: string; originalContent?: string; generateFix?: boolean },
  onEvent: (event: string, data: Record<string, unknown>) => void,
) => {
  const token = await getAccessToken();
  const response = await fetch(`${BASE_API_URL}stream-diagnostic`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) throw new Error(`Diagnostic request failed (${response.status})`);
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
