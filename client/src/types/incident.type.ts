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
  evidenceIds: string[];
  workflowEvidenceIds: string[];
  changedFileEvidenceIds: string[];
  relevantPaths: string[];
  applicability?: string | null;
  applicabilityReason?: string | null;
  incomplete: boolean;
  limitations: string[];
  createdAt: string;
  updatedAt: string;
};

export type IncidentsResponse = { incidents: IncidentRecord[] };
