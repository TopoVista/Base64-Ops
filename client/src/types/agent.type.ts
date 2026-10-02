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
  evidenceId?: string;
  source: string;
  kind?: string;
  excerpt?: string;
  count?: number;
};

export type EvidenceItem = {
  id: string;
  sourceType: string;
  title: string;
  excerpt: string;
  path?: string;
  branch?: string;
  commitSha?: string;
  lineStart?: number;
  lineEnd?: number;
  retrievalScore?: number;
  metadata?: {
    workflow_run_id?: number;
    workflow_name?: string;
    workflow_path?: string;
    job_id?: number;
    job_name?: string;
    failed_step?: string;
    categories?: string[];
    truncated?: boolean;
    state?: "failed_run" | "current" | string;
    html_url?: string;
  };
};

export type TimelineEvent = {
  id: string;
  label?: string;
  name?: string;
  detail?: string;
  status?: "started" | "completed" | "failed" | "partial";
  risk?: "safe" | "approval_required";
};

export type CIInvestigationSummary = {
  run_id: number;
  workflow_name?: string;
  head_sha: string;
  current_head_sha?: string;
  applicability?: "current" | "historical_fixed" | "historical_needs_verification";
  applicability_reason?: string;
  failed_jobs: Array<{ name: string; failed_step?: string }>;
  /** Ordered navigation targets derived from bounded CI evidence. */
  relevant_paths?: string[];
  limitations: string[];
};

export type RepositoryContext = {
  name?: string;
  branch?: string;
  commit_sha?: string;
  file_count?: number;
  ci_files?: string[];
  manifests?: string[];
  capabilities?: Array<{ name?: string; available?: boolean }>;
};

export type InvestigationSummary = {
  run_id: string;
  status: "complete" | "incomplete" | "awaiting_approval" | "failed";
  summary: string;
  evidence_ids: string[];
  hypotheses: Array<{
    id: string;
    title: string;
    explanation: string;
    evidence_ids: string[];
    confidence: number;
    status: "candidate" | "supported" | "weakened" | "rejected" | "confirmed";
  }>;
  limitations: string[];
};

export type RelatedMemory = {
  id?: string;
  memory_id?: string;
  kind: string;
  key: string;
  value: string;
  tags?: string[];
  freshness?: "current" | "needs_revalidation" | "stale";
  verification_status?: string;
  source_commit_sha?: string;
  affected_paths?: string[];
  _label?: string;
};

export type DeliveryPlanSummary = {
  id: string;
  title: string;
  rationale: string;
  base_branch: string;
  base_sha: string;
  risk_level: "low" | "medium" | "high" | "critical";
  risk_reasons: string[];
  evidence_ids: string[];
  files: Array<{ path: string; change_type: string; unified_diff: string }>;
  validation_steps?: Array<{ id: string; kind: string; description: string }>;
};

export type DeliveryResult = {
  status: string;
  reason?: string;
  failure_stage?: string;
  branch_name?: string;
  commit_sha?: string;
  pull_request_number?: number;
  pull_request_url?: string;
  validation_results?: Array<{ step_id: string; status: string; summary: string; duration_ms?: number }>;
};

export type ToolResult = {
  success: boolean;
  output: string;
};

export type CommandCenterSnapshot = {
  runId?: string;
  repository?: RepositoryContext;
  investigation?: InvestigationSummary | null;
  memory?: RelatedMemory[];
  ci?: CIInvestigationSummary | null;
  timeline?: TimelineEvent[];
  deliveryPlan?: DeliveryPlanSummary | null;
  deliveryResult?: DeliveryResult | null;
  toolResult?: ToolResult | null;
  approvals?: ApprovalRequest[];
  evidence?: EvidenceItem[];
};

export type ApprovalRequest = {
  id: string;
  action: string;
  summary: string;
  args: Record<string, unknown>;
  risk: "approval_required";
  blastRadius?: string;
  rollback?: string;
  riskLevel?: "low" | "medium" | "high" | "critical";
  deliveryPlanId?: string;
  baseBranch?: string;
  baseSha?: string;
  diffHash?: string;
  approvalHash?: string;
  evidenceIds?: string[];
  validation?: Array<{ step_id: string; status: string; summary: string }>;
  files?: Array<{ path: string; unified_diff: string }>;
  status?: "pending" | "approved" | "rejected" | "approve" | "edit" | "reject" | "invalidated" | "executed" | "failed";
};

export type AgentSessionEvent =
  | { type: "session.updated"; data: { session: unknown } }
  | { type: "tool.started"; data: TimelineEvent }
  | { type: "tool.completed"; data: TimelineEvent }
  | { type: "rag.sources"; data: { sources: RagSource[] } }
  | { type: "evidence.items"; data: { evidence: EvidenceItem[] } }
  | { type: "ci.summary"; data: { ci: CIInvestigationSummary } }
  | { type: "repository.context"; data: { repository: RepositoryContext } }
  | { type: "investigation.result"; data: { investigation: InvestigationSummary } }
  | { type: "memory.related"; data: { items: RelatedMemory[] } }
  | { type: "delivery.plan"; data: { plan: DeliveryPlanSummary } }
  | { type: "tool.result"; data: { result: ToolResult } }
  | { type: "approval.requested"; data: { approval: ApprovalRequest } }
  | { type: "message.delta"; data: { id: string; delta: string } }
  | { type: "message.completed"; data: { message: AgentMessage } }
  | { type: "error"; data: { message: string } }
  | { type: string; data: unknown };

export type AgentChatStatus = "idle" | "submitted" | "streaming" | "error";
