export type AgentRole = "user" | "assistant" | "system";

export type AgentMessage = {
  _id?: string;
  id: string;
  sessionId?: string;
  role: AgentRole;
  content: string;
  createdAt?: string;
  updatedAt?: string;
  sources?: RagSource[];
};

export type RagSource = {
  source: string;
  kind?: string;
  excerpt?: string;
  count?: number;
};

export type TimelineEvent = {
  id: string;
  label?: string;
  name?: string;
  detail?: string;
  status?: "started" | "completed" | "failed";
  risk?: "safe" | "approval_required";
};

export type ApprovalRequest = {
  id: string;
  action: string;
  summary: string;
  args: Record<string, unknown>;
  risk: "approval_required";
  blastRadius?: string;
  rollback?: string;
  status?: "pending" | "approve" | "edit" | "reject";
};

export type AgentSessionEvent =
  | { type: "session.updated"; data: { session: unknown } }
  | { type: "tool.started"; data: TimelineEvent }
  | { type: "tool.completed"; data: TimelineEvent }
  | { type: "rag.sources"; data: { sources: RagSource[] } }
  | { type: "approval.requested"; data: { approval: ApprovalRequest } }
  | { type: "message.delta"; data: { id: string; delta: string } }
  | { type: "message.completed"; data: { message: AgentMessage } }
  | { type: "error"; data: { message: string } }
  | { type: string; data: unknown };

export type AgentChatStatus = "idle" | "submitted" | "streaming" | "error";
