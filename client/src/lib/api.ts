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
  slugId: string
): Promise<{ success: boolean; indexed: number; status?: "ready" | "empty" | "failed"; reason?: string; storage?: "lexical" | "vector"; vectorWarning?: string }> => {
  const response = await API.post(`/session/${slugId}/rag/reindex`);
  return response.data;
};

export const getSessionRagSources = async (
  slugId: string
): Promise<{ sources: RagSource[] }> => {
  const response = await API.get(`/session/${slugId}/rag/sources`);
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

export const getSessionCodeFile = async (slugId: string, path: string): Promise<{ path: string; headSha?: string; content: string; truncated: boolean }> => {
  const response = await API.get(`/session/${slugId}/code/file`, { params: { path } });
  return response.data;
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
  payload: { sessionSlugId: string; failedLog: string; gitDiff: string; originalContent?: string; generateFix?: boolean },
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
