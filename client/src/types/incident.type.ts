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
  failedTestIdentifiers?: string[];
  flakySignal?: "suspected" | null;
  flakyObservations?: FlakyTestObservation[];
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
    workflow_changed_since_last_success?: boolean | null;
    workflow_comparison_note?: string | null;
    dependency_files_changed_since_last_success?: boolean | null;
    dependency_comparison_note?: string | null;
    declared_runner_changed_since_last_success?: boolean | null;
    runner_comparison_note?: string | null;
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
  sameShaSuccess?: {
    workflowRunId: number;
    headSha: string;
    observedAt: string;
  };
  memoryEntryCount?: number;
  memoryRecordedAt?: string;
  githubCheckReport?: {
    status: "published" | "unavailable" | "disabled" | string;
    checkRunId?: number;
    url?: string | null;
    message?: string;
    publishedAt?: string;
  };
  feedback?: {
    disposition: "helpful" | "incorrect" | "needs_more_evidence";
    correction?: string;
    updatedAt: string;
  };
  createdAt: string;
  updatedAt: string;
};

export type IncidentsResponse = { incidents: IncidentRecord[] };

export type IncidentFeedbackInput = {
  disposition: "helpful" | "incorrect" | "needs_more_evidence";
  correction?: string;
};

export type FlakyTestObservation = {
  id: string;
  repository_id: string;
  workflow_key: string;
  test_identifier: string;
  head_sha: string;
  assessment: "insufficient_evidence" | "suspected_flakiness" | "reproducible_regression";
  same_sha_failure_count: number;
  same_sha_success_count: number;
  limitations: string[];
  updated_at: string;
};

/**
 * A persisted, human-review-only recurrence observation. It cannot predict a
 * regression, run a command, alter risk, or authorize a GitHub mutation.
 */
export type PreventionRule = {
  id: string;
  repository_id: string;
  affected_path: string;
  failure_category: string;
  historical_incident_ids: string[];
  historical_run_shas: string[];
  occurrence_count: number;
  status: "active" | "needs_review";
  applicability_limitations: string[];
  invalidation_reason?: string | null;
  created_at: string;
  updated_at: string;
};

export type RepositoryAutomationSettings = {
  id: string;
  session_id: string;
  repository_id: string;
  enabled: boolean;
  workflow_names: string[];
  include_branches: string[];
  exclude_branches: string[];
  daily_investigation_limit: number;
  github_check_reporting_enabled: boolean;
  updated_at: string;
};

export type RepositoryAutomationSettingsInput = Omit<
  RepositoryAutomationSettings,
  "id" | "session_id" | "repository_id" | "updated_at"
>;

/**
 * A repository-scoped GitHub App binding for the optional candidate workflow.
 * It is deliberately separate from read-only incident automation: enabling it
 * only allows a user to request a separately approved verification dispatch.
 */
export type CandidateVerificationSettings = {
  id: string;
  session_id: string;
  repository_id: string;
  repository_full_name?: string | null;
  enabled: boolean;
  workflow_path?: string | null;
  configured: boolean;
  updated_at: string;
};

export type CandidateVerificationSettingsInput = {
  enabled: boolean;
  installation_id?: string | null;
  workflow_path?: string | null;
};

export type CandidateVerificationRun = {
  verificationRunId: string;
  status: "dispatching" | "dispatched" | "pending" | "running" | "succeeded" | "failed" | "stale" | "inconclusive" | string;
  verificationLevel: string;
  githubRunId?: number | null;
  githubUrl?: string | null;
  message?: string | null;
  failureEvidenceCount?: number;
  failureCategories?: string[];
  logsTruncated?: boolean;
  limitations: string[];
  createdAt?: string | null;
  updatedAt?: string | null;
};

