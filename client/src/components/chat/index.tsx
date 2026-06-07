import { useAgentSession } from "@/hooks/use-agent-session";
import { useUser } from "@/hooks/use-user";
import { createSessionPullRequest, getGithubRepos, reindexSessionRag } from "@/lib/api";
import { cn, generateSlugId } from "@/lib/utils";
import type { AgentMessage, ApprovalRequest, RagSource, TimelineEvent } from "@/types/agent.type";
import type { GithubRepo } from "@/types/github.type";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { MessageResponse } from "../ai-elements/message";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { Spinner } from "../ui/spinner";
import ChatInput from "./chat-input";
import {
  Activity,
  BookOpen,
  Bot,
  Boxes,
  CheckCircle2,
  Code2,
  ExternalLink,
  GitPullRequest,
  RadioTower,
  ShieldAlert,
  Sparkles,
  User,
} from "lucide-react";

type ChatInterfaceProps = {
  className?: string;
  initialMessages?: AgentMessage[];
  sessionTitle?: string;
  slugId?: string;
  repoUrl?: string;
  defaultBranch?: string;
  branchName?: string | null;
  isSingleSession?: boolean;
};

type SelectedRepo = {
  value: string;
  defaultBranch: string;
  label?: string;
};

const EmptyState = () => (
  <div className="mx-auto flex max-w-3xl flex-1 flex-col items-center justify-center px-6 py-16 text-center">
    <div className="mb-6 rounded-full border border-cyan-300/30 bg-cyan-300/10 p-4 text-cyan-200 shadow-2xl shadow-cyan-950/40">
      <RadioTower className="size-8" />
    </div>
    <h1 className="bg-gradient-to-br from-white via-zinc-200 to-cyan-200 bg-clip-text text-4xl font-semibold tracking-tight text-transparent md:text-6xl">
      DevOps command center for your codebase
    </h1>
    <p className="mt-5 max-w-2xl text-base leading-7 text-zinc-400">
      Select a GitHub repo, index the code and runbooks, then ask the LangGraph agent to inspect,
      diagnose, or prepare approved changes.
    </p>
    <div className="mt-6 grid w-full gap-3 text-left md:grid-cols-3">
      {[
        ["RAG grounded", "Answers cite repo files and runbooks."],
        ["Approval gated", "Risky writes, pushes, and ops wait for you."],
        ["Docker aware", "Checks Dockerfiles and compose configs first."],
      ].map(([title, body]) => (
        <div key={title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <p className="text-sm font-semibold text-zinc-100">{title}</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">{body}</p>
        </div>
      ))}
    </div>
  </div>
);

const MessageBubble = ({ message }: { message: AgentMessage }) => {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex gap-3", isUser && "justify-end")}>
      {!isUser ? (
        <div className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-2xl bg-cyan-400/10 text-cyan-200">
          <Bot className="size-4" />
        </div>
      ) : null}
      <div
        className={cn(
          "max-w-[82%] rounded-3xl border px-4 py-3 text-sm leading-6 shadow-xl",
          isUser
            ? "border-cyan-300/20 bg-cyan-300/10 text-cyan-50"
            : "border-white/10 bg-zinc-950/80 text-zinc-100",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <MessageResponse shikiTheme={["dracula", "dracula"]}>{message.content}</MessageResponse>
        )}
      </div>
      {isUser ? (
        <div className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-2xl bg-white/10 text-zinc-200">
          <User className="size-4" />
        </div>
      ) : null}
    </div>
  );
};

const TimelinePanel = ({ timeline }: { timeline: TimelineEvent[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<Activity className="size-4" />} title="LangGraph Timeline" />
    <div className="space-y-2">
      {timeline.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Agent execution steps appear here.
        </p>
      ) : (
        timeline.map((item) => (
          <div key={item.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="size-4 text-emerald-300" />
              <p className="text-sm font-medium text-zinc-100">{item.label ?? item.name}</p>
              {item.risk ? <Badge variant="outline">{item.risk}</Badge> : null}
            </div>
            {item.detail ? <p className="mt-1 text-xs leading-5 text-zinc-500">{item.detail}</p> : null}
          </div>
        ))
      )}
    </div>
  </div>
);

const SourcesPanel = ({ sources }: { sources: RagSource[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<BookOpen className="size-4" />} title="RAG Sources" />
    <div className="space-y-2">
      {sources.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Retrieved code, CI config, Docker files, and runbooks appear here.
        </p>
      ) : (
        sources.map((source, index) => (
          <div key={`${source.source}-${index}`} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-sm font-medium text-zinc-100">{source.source}</p>
              <Badge variant="secondary">{source.kind ?? "repo"}</Badge>
            </div>
            {source.excerpt ? <p className="mt-2 line-clamp-4 text-xs leading-5 text-zinc-500">{source.excerpt}</p> : null}
          </div>
        ))
      )}
    </div>
  </div>
);

const ApprovalPanel = ({ approvals }: { approvals: ApprovalRequest[] }) => (
  <div className="space-y-3">
    <PanelTitle icon={<ShieldAlert className="size-4" />} title="Approvals" />
    <div className="space-y-2">
      {approvals.length === 0 ? (
        <p className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 text-sm text-zinc-500">
          Risky operations pause here before they run.
        </p>
      ) : (
        approvals.map((approval) => (
          <div key={approval.id} className="rounded-2xl border border-amber-300/20 bg-amber-300/10 p-3">
            <p className="text-sm font-medium text-amber-100">{approval.action}</p>
            <p className="mt-1 text-xs leading-5 text-amber-100/70">{approval.summary}</p>
            <Badge className="mt-2" variant="outline">
              {approval.status ?? "pending"}
            </Badge>
          </div>
        ))
      )}
    </div>
  </div>
);

const PanelTitle = ({ icon, title }: { icon: ReactNode; title: string }) => (
  <div className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
    <span className="text-cyan-200">{icon}</span>
    {title}
  </div>
);

const ChatInterface = ({
  className,
  initialMessages = [],
  sessionTitle: sessionTitleProp,
  slugId: slugIdProp,
  repoUrl,
  defaultBranch,
  branchName: branchNameProp,
}: ChatInterfaceProps) => {
  const [repo, setRepo] = useState<SelectedRepo | null>(
    repoUrl
      ? {
          value: repoUrl,
          defaultBranch: defaultBranch ?? "main",
        }
      : null,
  );
  const [sessionTitle, setSessionTitle] = useState(sessionTitleProp ?? "New Operation");
  const [branchName] = useState<string | null>(branchNameProp ?? null);
  const [slugId] = useState(() => slugIdProp || generateSlugId());
  const queryClient = useQueryClient();
  const { data: currentUser } = useUser();
  const isGithubConnected = Boolean(currentUser?.user?.githubConnected);

  const {
    messages,
    setMessages,
    sources,
    timeline,
    approvals,
    status,
    sendMessage,
    stop,
    decideApproval,
  } = useAgentSession(initialMessages);

  useEffect(() => {
    setMessages(initialMessages);
  }, [initialMessages, setMessages]);

  const { data: githubRepos, isPending: isGithubRepoPending } = useQuery({
    queryKey: ["github-repos"],
    queryFn: getGithubRepos,
    enabled: isGithubConnected,
    retry: false,
  });

  const repoOptions = useMemo(
    () =>
      githubRepos?.repos?.map((repoItem: GithubRepo) => ({
        label: repoItem.fullName,
        value: repoItem.cloneUrl,
        defaultBranch: repoItem.defaultBranch,
      })) ?? [],
    [githubRepos],
  );

  const reindexMutation = useMutation({
    mutationFn: () => reindexSessionRag(slugId),
    onSuccess: (data) => toast.success(`RAG index updated (${data.indexed} chunks)`),
    onError: () => toast.error("Unable to reindex RAG sources"),
  });

  const createPrMutation = useMutation({
    mutationFn: () =>
      createSessionPullRequest(slugId, {
        title: `Agent updates for ${repo?.label ?? "repository"}`,
        body: "Created by the LangGraph DevOps Agent.",
      }),
    onSuccess: (data) => {
      toast.success("Pull request created");
      window.open(data.url, "_blank", "noopener,noreferrer");
    },
    onError: () => toast.error("Failed to create pull request"),
  });

  const handleRepoChange = (value: string) => {
    const selected = repoOptions.find((option) => option.value === value);
    setRepo(selected ?? { value, defaultBranch: "main" });
  };

  const handleSubmit = (message: string) => {
    if (!repo) {
      toast.error("Select a repository first");
      return;
    }
    setSessionTitle(message.slice(0, 48));
    if (!slugIdProp) {
      window.history.pushState(null, "", `/session/${slugId}`);
    }
    sendMessage({
      slugId,
      repoUrl: repo.value,
      defaultBranch: repo.defaultBranch,
      message,
    });
    queryClient.invalidateQueries({ queryKey: ["user-sessions"] });
  };

  const handleApproval = (approval: ApprovalRequest, decision: "approve" | "edit" | "reject") => {
    decideApproval(slugId, approval, decision);
  };

  return (
    <div className={cn("flex h-[100dvh] min-h-0 w-full flex-col bg-zinc-950 text-zinc-100", className)}>
      <div className="border-b border-white/10 bg-zinc-950/90 px-4 py-3 backdrop-blur-xl">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-3">
            <div className="rounded-2xl bg-cyan-300/10 p-2 text-cyan-200">
              <Sparkles className="size-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold">{sessionTitle}</h2>
              <p className="text-xs text-zinc-500">LangGraph + RAG + approval-gated DevOps</p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Badge variant="outline" className="border-cyan-300/30 text-cyan-100">
              {status === "idle" ? "ready" : status}
            </Badge>
            <Button
              variant="outline"
              size="sm"
              className="border-white/10"
              onClick={() => createPrMutation.mutate()}
              disabled={!repo || createPrMutation.isPending}
            >
              {createPrMutation.isPending ? <Spinner className="size-4" /> : <GitPullRequest className="size-4" />}
              Create PR
            </Button>
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="relative flex min-h-0 flex-col overflow-hidden">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(34,211,238,0.18),transparent_28%),radial-gradient(circle_at_80%_0%,rgba(99,102,241,0.14),transparent_22%)]" />
          <ScrollArea className="relative min-h-0 flex-1">
            <div className="mx-auto flex min-h-[calc(100dvh-13rem)] max-w-5xl flex-col gap-5 px-4 py-6">
              {messages.length === 0 ? <EmptyState /> : messages.map((message) => <MessageBubble key={message.id} message={message} />)}
            </div>
          </ScrollArea>

          <ChatInput
            status={status}
            repo={repo}
            branchName={branchName}
            approvals={approvals}
            isGithubConnected={isGithubConnected}
            isFetchingRepos={isGithubRepoPending}
            repoOptions={repoOptions}
            onSubmit={handleSubmit}
            onStop={stop}
            onRepoChange={handleRepoChange}
            onApprovalDecision={handleApproval}
            onReindex={() => reindexMutation.mutate()}
            isReindexing={reindexMutation.isPending}
          />
        </div>

        <aside className="hidden min-h-0 border-l border-white/10 bg-zinc-950/95 xl:block">
          <ScrollArea className="h-full">
            <div className="space-y-6 p-4">
              <div className="rounded-3xl border border-white/10 bg-white/[0.03] p-4">
                <PanelTitle icon={<Boxes className="size-4" />} title="Operation Context" />
                <div className="mt-4 space-y-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-zinc-500">Repository</span>
                    <span className="truncate text-right text-zinc-200">{repo?.label ?? repo?.value ?? "None"}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-zinc-500">Branch</span>
                    <span className="truncate text-right text-zinc-200">{branchName ?? "Created on first run"}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-zinc-500">Stack</span>
                    <span className="text-zinc-200">FastAPI · LangGraph</span>
                  </div>
                </div>
              </div>
              <TimelinePanel timeline={timeline} />
              <SourcesPanel sources={sources} />
              <ApprovalPanel approvals={approvals} />
              <div className="rounded-3xl border border-white/10 bg-white/[0.03] p-4">
                <PanelTitle icon={<Code2 className="size-4" />} title="DevOps Surface" />
                <p className="mt-2 text-xs leading-5 text-zinc-500">
                  Read-only diagnostics run immediately. Commits, pushes, workflow dispatches, and deploy-like operations require approval.
                </p>
                <Link className="mt-3 inline-flex items-center gap-2 text-xs text-cyan-200" to="/new">
                  Start another operation <ExternalLink className="size-3" />
                </Link>
              </div>
            </div>
          </ScrollArea>
        </aside>
      </div>
    </div>
  );
};

export default ChatInterface;
