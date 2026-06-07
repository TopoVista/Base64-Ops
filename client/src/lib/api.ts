import type { AuthResponse, LoginType, RegisterType } from "@/types/auth.type";
import API from "./axios-client";
import type { ApprovalRequest, AgentMessage, RagSource } from "@/types/agent.type";
import type { CreatePullRequestResponse, SessionsResponse, SingleSessionResponse } from "@/types/session.type";
import type { GithubConnectResponse, GithubReposResponse } from "@/types/github.type";


export const loginMutationFn = async (data:LoginType):Promise<AuthResponse> => {
    const response = await API.post<AuthResponse>("/auth/login", data);
    return response.data
}


export const registerMutationFn = async (data: RegisterType): Promise<AuthResponse> => {
    const response = await API.post<AuthResponse>("/auth/register", data);
    return response.data;
}

export const getCurrentUser = async (): Promise<AuthResponse> => {
    const response = await API.get<AuthResponse>("/auth/me");
    return response.data;
}

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
): Promise<{ success: boolean; indexed: number }> => {
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

export const getUserSessionsWithSearch = async (params?: {
    search?: string;
    pageSize?: number;
    pageNumber?: number;
}): Promise<SessionsResponse> => {
    const response = await API.get<SessionsResponse>("/session/all", {
        params,
    });
    return response.data;
};

export const logoutMutationFn = async (): Promise<{ message: string }> => {
    const response = await API.post<{ message: string }>("/auth/logout");
    return response.data;
};
