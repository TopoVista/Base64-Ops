import ChatInterface from "@/components/chat";
import type { CommandCenterSnapshot } from "@/types/agent.type";
import { useState } from "react";

const demoSteps = [
  ["1", "CI signal", "Inspect the failed job and bounded redacted log excerpt."],
  ["2", "Evidence", "Compare the CI symptom with the workflow and repository files."],
  ["3", "Proposal", "Review the exact workflow diff and its HIGH CI/CD risk."],
  ["4", "Approval", "See why a fixture can demonstrate approval but never create a real pull request."],
] as const;

const snapshot: CommandCenterSnapshot = {
  runId: "demo-ci-stale-working-directory",
  repository: {
    name: "fixture/actions-path",
    branch: "main",
    commit_sha: "abc1234567890def",
    file_count: 14,
    ci_files: [".github/workflows/ci.yml"],
    manifests: ["client/package.json"],
  },
  timeline: [
    { id: "demo-workspace", label: "Workspace ready", detail: "Fixture workspace on main", status: "completed" },
    { id: "demo-map", label: "Repository map loaded", detail: "14 files; 1 CI file", status: "completed" },
    { id: "demo-ci", label: "GitHub Actions investigated", detail: "Run #842; 1 failed job; 3 evidence items", status: "completed" },
    { id: "demo-plan", label: "Exact patch proposed", detail: "1 file changed", status: "completed" },
  ],
  evidence: [
    { id: "evd_log", sourceType: "ci_log", title: "frontend-build log", path: "Backend CI / frontend-build", excerpt: "npm ERR! enoent ENOENT: no such file or directory, open '/repo/server/package.json'", commitSha: "abc1234567890def", metadata: { workflow_run_id: 842, workflow_name: "Backend CI", job_name: "frontend-build", failed_step: "npm run build", categories: ["missing_file"], state: "failed_run" } },
    { id: "evd_workflow", sourceType: "repo_file", title: ".github/workflows/ci.yml", path: ".github/workflows/ci.yml", excerpt: "defaults:\n  run:\n    working-directory: ./server", commitSha: "abc1234567890def" },
    { id: "evd_package", sourceType: "repo_file", title: "client/package.json", path: "client/package.json", excerpt: '{ "name": "frontend" }', commitSha: "abc1234567890def" },
  ],
  investigation: {
    run_id: "demo-ci-stale-working-directory",
    status: "awaiting_approval",
    summary: "The workflow executes frontend commands from ./server, while the package manifest is under ./client.",
    evidence_ids: ["evd_log", "evd_workflow", "evd_package"],
    hypotheses: [{ id: "hyp_path", title: "Workflow working-directory is stale", explanation: "The CI symptom, workflow configuration, and repository layout support this diagnosis. Changed-file correlation is evidence, not proof by itself.", evidence_ids: ["evd_log", "evd_workflow", "evd_package"], confidence: 0.85, status: "supported" }],
    limitations: [],
  },
  memory: [{ id: "mem_ci", kind: "incident_outcome", key: "frontend working directory", value: "A prior CI incident involved a repository move; current workflow evidence remains the source of truth.", freshness: "current", source_commit_sha: "9876543210abcdef" }],
  ci: { run_id: 842, workflow_name: "Backend CI", head_sha: "abc1234567890def", current_head_sha: "abc1234567890def", applicability: "current", applicability_reason: "The same ./server path remains in the current workflow.", failed_jobs: [{ name: "frontend-build", failed_step: "npm run build" }], limitations: [] },
  deliveryPlan: {
    id: "dpl_demo", title: "Update CI working directory", rationale: "Point the workflow at the frontend package directory supported by current repository evidence.", base_branch: "main", base_sha: "abc1234567890def", risk_level: "high", risk_reasons: ["CI/CD behavior change"], evidence_ids: ["evd_log", "evd_workflow", "evd_package"], validation_steps: [{ id: "validate-workflow", kind: "config", description: "Validate workflow syntax and repository path" }], files: [{ path: ".github/workflows/ci.yml", change_type: "modify", unified_diff: "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1,3 +1,3 @@\n defaults:\n   run:\n-    working-directory: ./server\n+    working-directory: ./client\n" }],
  },
  deliveryResult: {
    status: "partial",
    branch_name: "agent/fix-ci-working-directory",
    commit_sha: "abc1234567890def",
    reason: "Deterministic fixture only: no GitHub branch, commit, or pull request was created.",
    validation_results: [{ step_id: "validate-workflow", status: "passed", summary: "Workflow path is valid in the fixture." }],
  },
  toolResult: {
    success: true,
    output: "Read-only repository map loaded: client/package.json and .github/workflows/ci.yml were inspected.",
  },
  approvals: [{ id: "apr_demo", action: "Create reviewable fix", summary: "Update the stale CI working-directory.", args: {}, risk: "approval_required", riskLevel: "high", deliveryPlanId: "dpl_demo", baseBranch: "main", baseSha: "abc1234567890def", diffHash: "demo-diff-hash", approvalHash: "demo-approval-binding-hash", evidenceIds: ["evd_log", "evd_workflow", "evd_package"], validation: [{ step_id: "validate-workflow", status: "passed", summary: "Workflow path is valid in the fixture." }], files: [{ path: ".github/workflows/ci.yml", unified_diff: "-    working-directory: ./server\n+    working-directory: ./client\n" }], status: "pending" }],
};

export default function DemoPage() {
  const [activeStep, setActiveStep] = useState(0);
  const step = demoSteps[activeStep];
  return <div className="flex h-dvh min-h-0 flex-col"><section className="border-b border-border bg-card px-4 py-3 sm:px-6"><div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3"><p className="mr-auto text-sm font-medium">Interactive safe-CI walkthrough</p>{demoSteps.map(([number, title], index) => <button type="button" key={number} onClick={() => setActiveStep(index)} aria-pressed={activeStep === index} className={`rounded-full border px-3 py-1 text-xs font-medium transition ${activeStep === index ? "border-primary bg-primary text-primary-foreground" : "border-border hover:border-primary/50"}`}>{number}. {title}</button>)}</div><div className="mx-auto mt-2 max-w-7xl text-sm text-muted-foreground" aria-live="polite"><span className="font-medium text-foreground">{step[1]}:</span> {step[2]} Then use the workspace panels below to inspect it.</div></section><div className="min-h-0 flex-1"><ChatInterface sessionTitle="Deterministic CI fixture" slugId="demo-ci-stale-working-directory" repoUrl="https://github.com/base64-fixtures/actions-path" defaultBranch="main" initialIndexStatus="ready" initialMessages={[{ id: "demo-message", role: "assistant", content: "This read-only fixture demonstrates the full evidence-to-approval workflow, including the shape of a completed delivery result. It does not access GitHub or create a pull request." }]} initialCommandCenter={snapshot} isDemo /></div></div>;
}
