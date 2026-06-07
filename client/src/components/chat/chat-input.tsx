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
  repoOptions: Array<{ value: string; label: string; defaultBranch: string }>;
  onSubmit: (message: string) => void;
  onStop: () => void;
  onRepoChange: (value: string) => void;
  onApprovalDecision: (
    approval: ApprovalRequest,
    decision: "approve" | "edit" | "reject",
  ) => void;
  onReindex: () => void;
  isReindexing: boolean;
};

const ChatInput = ({
  status,
  repo,
  branchName,
  approvals,
  isGithubConnected,
  isFetchingRepos,
  repoOptions,
  onSubmit,
  onStop,
  onRepoChange,
  onApprovalDecision,
  onReindex,
  isReindexing,
}: ChatInputProps) => {
  const [value, setValue] = useState("");
  const isWorking = status === "submitted" || status === "streaming";
  const pendingApproval = approvals.find((approval) => approval.status === "pending" || !approval.status);

  const handleConnect = async () => {
    const response = await connectGithub(window.location.href);
    window.location.href = response.url;
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
    <div className="border-t border-white/10 bg-background/85 px-4 py-4 backdrop-blur-xl">
      {pendingApproval ? (
        <div className="mx-auto mb-3 max-w-5xl rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-amber-50 shadow-2xl shadow-amber-950/20">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <ShieldCheck className="size-4" />
                Approval required: {pendingApproval.action}
              </div>
              <p className="text-sm text-amber-100/80">{pendingApproval.summary}</p>
              <p className="text-xs text-amber-100/60">
                Blast radius: {pendingApproval.blastRadius ?? "Repository or workflow side effect"}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button size="sm" variant="secondary" onClick={() => onApprovalDecision(pendingApproval, "approve")}>
                Approve
              </Button>
              <Button size="sm" variant="outline" onClick={() => onApprovalDecision(pendingApproval, "reject")}>
                Reject
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="mx-auto flex max-w-5xl flex-col gap-3 rounded-3xl border border-white/10 bg-zinc-950/80 p-3 shadow-2xl shadow-black/30">
        <Textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Ask for a repo diagnosis, Docker check, CI review, RAG-grounded answer, or approved change..."
          className="min-h-24 resize-none border-0 bg-transparent text-base text-zinc-100 shadow-none outline-none focus-visible:ring-0"
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              handleSubmit();
            }
          }}
        />

        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <div className="flex flex-wrap items-center gap-2">
            {isGithubConnected ? (
              <PromptInputSelect value={repo?.value ?? ""} onValueChange={onRepoChange}>
                <PromptInputSelectTrigger className="h-10 min-w-72 border-white/10 bg-white/5 text-zinc-100">
                  <img src={githubLogo} alt="" className="mr-2 size-4" />
                  <PromptInputSelectValue placeholder={isFetchingRepos ? "Loading repos..." : "Select repository"} />
                </PromptInputSelectTrigger>
                <PromptInputSelectContent>
                  {repoOptions.map((option) => (
                    <PromptInputSelectItem key={option.value} value={option.value}>
                      {option.label}
                    </PromptInputSelectItem>
                  ))}
                </PromptInputSelectContent>
              </PromptInputSelect>
            ) : (
              <Button variant="outline" onClick={handleConnect}>
                <img src={githubLogo} alt="" className="size-4" />
                Connect GitHub
              </Button>
            )}

            <Button
              type="button"
              variant="outline"
              className="border-white/10 bg-white/5 text-zinc-100"
              onClick={onReindex}
              disabled={!repo || isReindexing}
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
            <Button onClick={handleSubmit} disabled={!repo || isWorking} className="min-w-32">
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
