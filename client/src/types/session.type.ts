import type { AgentMessage, CommandCenterSnapshot } from "./agent.type";

export type SessionRecord = {
  _id: string;
  userId: string;
  slugId: string;
  title: string | null;
  boxId: string | null;
  repoUrl: string;
  repoName: string;
  defaultBranch: string | null;
  branchName: string | null;
  repoInitializedAt?: string | null;
  indexStatus?: "indexing" | "ready" | "empty" | "failed" | null;
  indexError?: string | null;
  // True means the server intentionally retained a bounded, usable subset of
  // a large repository rather than claiming complete retrieval coverage.
  indexTruncated?: boolean;
  status: "active" | "completed" | "failed";
  createdAt: string;
  updatedAt: string;
};

export type SessionsResponse = {
  sessions: SessionRecord[];
  pagination?: {
    pageSize: number;
    pageNumber: number;
    totalCount: number;
    totalPages: number;
    skip: number;
  };
  
};

export type SingleSessionResponse = {
  session: SessionRecord;
  messages: AgentMessage[];
  commandCenter?: CommandCenterSnapshot | null;
};

export type HistoricalRun = {
  run_id: string;
  trace_id?: string | null;
  created_at?: string | null;
  status: string;
  title: string;
  kind: "ci" | "delivery" | "investigation";
  evidence_count: number;
  has_trace: boolean;
  ci_run_id?: number | null;
  delivery_status?: string | null;
  has_change_impact: boolean;
};

export type SessionRunHistoryResponse = {
  runs: HistoricalRun[];
  limit: number;
};
