import type { AgentChatStatus, ApprovalRequest } from "@/types/agent.type";
import { connectGithub } from "@/lib/api";
import githubLogo from "@/assets/github.svg";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { Spinner } from "../ui/spinner";
import {
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
} from "../ai-elements/prompt-input";
import { GitBranch, ShieldCheck, Square, Terminal, Zap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

type SelectedRepo = {
  value: string;
  defaultBranch: string;
  label?: string;
};

type ChatInputProps = {
  status: AgentChatStatus;
  repo: SelectedRepo | null;
  branchName: string | null;
  approvals: ApprovalRequest[];
  isGithubConnected: boolean;
  isFetchingRepos: boolean;
  repoLoadError?: string | null;
  repoOptions: Array<{ value: string; label: string; defaultBranch: string }>;
  onRefreshRepos: () => void;
  onSubmit: (message: string) => void;
  onStop: () => void;
  onRepoChange: (value: string) => void;
  onApprovalDecision: (
    approval: ApprovalRequest,
    decision: "approve" | "edit" | "reject",
  ) => void;
  onReindex: () => void;
  isReindexing: boolean;
  isSessionReady: boolean;
  indexStatusMessage?: string | null;
  isDemo?: boolean;
};

const ChatInput = ({
  status,
  repo,
  branchName,
  approvals,
  isGithubConnected,
  isFetchingRepos,
  repoLoadError,
  repoOptions,
  onRefreshRepos,
  onSubmit,
  onStop,
  onRepoChange,
  onApprovalDecision,
  onReindex,
  isReindexing,
  isSessionReady,
  indexStatusMessage,
  isDemo = false,
}: ChatInputProps) => {
  const [value, setValue] = useState("");
  const [isConnectingGithub, setIsConnectingGithub] = useState(false);
  const isWorking = status === "submitted" || status === "streaming";
  const pendingApproval = approvals.find((approval) => approval.status === "pending" || !approval.status);

  const handleConnect = async () => {
    setIsConnectingGithub(true);
    try {
      const response = await connectGithub(window.location.href);
      window.location.assign(response.url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Unable to start GitHub connection");
      setIsConnectingGithub(false);
    }
  };

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      toast.error("Tell the agent what to inspect or build first");
      return;
    }
    onSubmit(trimmed);
    setValue("");
  };

  return (
    <div className="border-t border-border bg-background/85 px-4 py-4 backdrop-blur-xl">
      {pendingApproval ? (
        <div className="mx-auto mb-3 max-w-5xl rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-amber-950 shadow-2xl shadow-amber-950/20 dark:text-amber-50">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <ShieldCheck className="size-4" />
                Approval required: {pendingApproval.action}
              </div>
              <p className="text-sm text-amber-900/80 dark:text-amber-100/80">{pendingApproval.summary}</p>
              <p className="text-xs text-amber-900/70 dark:text-amber-100/60">
                Blast radius: {pendingApproval.blastRadius ?? "Repository or workflow side effect"}
              </p>
              {pendingApproval.baseSha ? <p className="text-xs text-amber-900/70 dark:text-amber-100/60">Base: {pendingApproval.baseBranch} @ {pendingApproval.baseSha.slice(0, 7)}</p> : null}
              {pendingApproval.validation?.map((item) => <p key={item.step_id} className="text-xs text-amber-900/80 dark:text-amber-100/70">{item.status === "passed" ? "✓" : "•"} {item.summary}</p>)}
              {pendingApproval.files?.map((file) => <details key={file.path} className="rounded-lg border border-amber-300/40 bg-amber-950/5 p-2 text-xs dark:border-amber-200/15 dark:bg-black/10"><summary className="cursor-pointer font-medium">Exact diff · {file.path}</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-amber-950/85 dark:text-amber-50/80">{file.unified_diff}</pre></details>)}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button size="sm" variant="secondary" onClick={() => onApprovalDecision(pendingApproval, "approve")} disabled={isDemo} aria-label={pendingApproval.deliveryPlanId ? "Approve the exact plan and create a draft pull request" : "Approve this action"}>
                {isDemo ? "Demo approval only" : pendingApproval.deliveryPlanId ? "Approve & Create Draft PR" : "Approve"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => onApprovalDecision(pendingApproval, "reject")} disabled={isDemo} aria-label="Reject this proposed action">
                Reject
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="mx-auto flex max-w-5xl flex-col gap-3 rounded-3xl border border-border bg-card/80 p-3 shadow-2xl shadow-black/30">
        {indexStatusMessage ? <p className="rounded-xl border border-amber-300/40 bg-amber-300/10 px-3 py-2 text-xs text-amber-900 dark:border-amber-300/20 dark:bg-amber-300/5 dark:text-amber-100">{indexStatusMessage}</p> : null}
        <Textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Ask for a repo diagnosis, Docker check, CI review, RAG-grounded answer, or approved change..."
          className="min-h-24 resize-none border-0 bg-transparent text-base text-foreground shadow-none outline-none focus-visible:ring-0"
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              handleSubmit();
            }
          }}
        />

        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <div className="flex flex-wrap items-center gap-2">
            {isGithubConnected ? (
              <>
              <PromptInputSelect value={repo?.value ?? ""} onValueChange={onRepoChange} disabled={isFetchingRepos || repoOptions.length === 0}>
                <PromptInputSelectTrigger className="h-10 min-w-72 border-border bg-background text-foreground hover:bg-muted">
                  <img src={githubLogo} alt="" className="mr-2 size-4" />
                  <PromptInputSelectValue placeholder={isFetchingRepos ? "Loading repositories…" : repoOptions.length ? "Select repository" : "No repositories available"} />
                </PromptInputSelectTrigger>
                <PromptInputSelectContent className="border-border bg-popover text-popover-foreground">
                  {repoOptions.map((option) => (
                    <PromptInputSelectItem key={option.value} value={option.value}>
                      {option.label}
                    </PromptInputSelectItem>
                  ))}
                </PromptInputSelectContent>
              </PromptInputSelect>
              <Button type="button" variant="ghost" size="sm" onClick={onRefreshRepos} disabled={isFetchingRepos} className="text-muted-foreground hover:text-foreground">
                {isFetchingRepos ? <Spinner className="size-4" /> : "Refresh"}
              </Button>
              </>
            ) : (
              <Button variant="outline" onClick={handleConnect} disabled={isConnectingGithub}>
                {isConnectingGithub ? <Spinner className="size-4" /> : <img src={githubLogo} alt="" className="size-4" />}
                {isConnectingGithub ? "Connecting..." : "Connect GitHub"}
              </Button>
            )}

            {isGithubConnected && repoLoadError ? <p className="basis-full text-xs text-destructive">Could not load repositories: {repoLoadError}. Reconnect GitHub if access has expired.</p> : null}
            {isGithubConnected && !isFetchingRepos && !repoLoadError && repoOptions.length === 0 ? <p className="basis-full text-xs text-muted-foreground">GitHub returned no accessible repositories. Check the connected account and repository permissions, then refresh.</p> : null}

            <Button
              type="button"
              variant="outline"
              className="border-border bg-background text-foreground hover:bg-muted"
              onClick={onReindex}
              disabled={isDemo || !repo || !isSessionReady || isReindexing}
              title={!isSessionReady ? "Run the agent once to create this session before indexing it." : undefined}
            >
              {isReindexing ? <Spinner className="size-4" /> : <Zap className="size-4" />}
              Reindex RAG
            </Button>

            {branchName ? (
              <span className="inline-flex items-center gap-2 rounded-full border border-white/10 px-3 py-2 text-xs text-zinc-300">
                <GitBranch className="size-3.5" />
                {branchName}
              </span>
            ) : null}
          </div>

          <div className="ml-auto flex items-center gap-2">
            {isWorking ? (
              <Button variant="outline" onClick={onStop}>
                <Square className="size-4" />
                Stop
              </Button>
            ) : null}
            <Button onClick={handleSubmit} disabled={isDemo || !repo || isWorking} className="min-w-32" aria-label="Run repository investigation">
              {isWorking ? <Spinner className="size-4" /> : <Terminal className="size-4" />}
              Run Agent
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatInput;
