export type IncidentRecord = {
  id: string;
  repositoryId: string;
  repositoryName: string;
  workflowRunId: number;
  workflowName?: string | null;
  workflowPath?: string | null;
  branch?: string | null;
  headSha: string;
  currentHeadSha?: string | null;
  runIsHistorical?: boolean;
  status: string;
  failureCategories: string[];
  evidenceStrength: "insufficient" | "observed" | "correlated" | "supported";
  hypothesisStatus?: "insufficient" | "candidate" | "supported";
  diagnosisSummary?: string | null;
  diagnosis?: {
    schema_version: "incident-diagnosis-v1";
    status: "insufficient" | "candidate" | "supported";
    summary: string;
    recommended_next_action: string;
  } | null;
  evidenceIds: string[];
  historyEvidenceIds?: string[];
  workflowEvidenceIds: string[];
  changedFileEvidenceIds: string[];
  relevantPaths: string[];
  applicability?: string | null;
  applicabilityReason?: string | null;
  history?: {
    compared_run_ids: number[];
    last_successful_run_id?: number | null;
    last_successful_head_sha?: string | null;
    first_observed_failure_run_id?: number | null;
    same_sha_failure_count: number;
    same_sha_success_count: number;
    assessment: string;
    limitation?: string | null;
  } | null;
  incomplete: boolean;
  limitations: string[];
  groupKey?: string;
  groupRootId?: string;
  groupRunIds?: number[];
  groupTimeline?: Array<{
    workflowRunId: number;
    headSha: string;
    observedAt: string;
    failureCategories: string[];
    failedJobNames: string[];
  }>;
  firstObservedAt?: string;
  lastObservedAt?: string;
  createdAt: string;
  updatedAt: string;
};

export type IncidentsResponse = { incidents: IncidentRecord[] };

export type RepositoryAutomationSettings = {
  id: string;
  session_id: string;
  repository_id: string;
  enabled: boolean;
  workflow_names: string[];
  include_branches: string[];
  exclude_branches: string[];
  daily_investigation_limit: number;
  updated_at: string;
};

export type RepositoryAutomationSettingsInput = Omit<
  RepositoryAutomationSettings,
  "id" | "session_id" | "repository_id" | "updated_at"
>;
