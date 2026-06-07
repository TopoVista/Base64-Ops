"use client";

import { DiffViewer } from "@/components/diff-viewer";
import {
  Commit,
  CommitActions,
  CommitCopyButton,
  CommitHash,
  CommitHeader,
  CommitInfo,
  CommitMessage,
  CommitMetadata,
  CommitSeparator,
  CommitTimestamp,
} from "@/components/ai-elements/commit";
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtSearchResult,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { cn } from "@/lib/utils";
import { DotIcon, SearchIcon } from "lucide-react";
import type { UIMessage } from "ai";
import type { ComponentProps, HTMLAttributes, ReactNode } from "react";


type ToolPart = Extract<UIMessage["parts"][number], { type: `tool-${string}` }>;

const ToolCall = ({
  title,
  subtitle,
  isRunning,
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  title: ReactNode;
  subtitle?: ReactNode;
  isRunning: boolean;
  children?: ReactNode;
}) => (
  <div className={cn("flex flex-col gap-1 mb-1 -mt-1", className)} {...props}>
    <div className={cn("flex flex-wrap items-center gap-x-2", isRunning && "animate-pulse opacity-50")}>
      <DotIcon className="-ml-3 size-8 text-foreground" />
      <span className="text-[15px] font-semibold -ml-3">{title}</span>
      {subtitle && (
        <span className="break-all text-sm text-muted-foreground">{subtitle}</span>
      )}
    </div>
    {!isRunning && children && (
      <div className="flex flex-col gap-2 pl-5">{children}</div>
    )}
  </div>
);


const ToolCallStep = ({
  command,
  detail,
}: {
  command?: ReactNode;
  detail?: ReactNode;
}) => (
  <div className="flex items-start gap-2">
    <span className="mt-1 text-muted-foreground">└</span>
    <div className="min-w-0">
     {command && <div className="break-all text-sm leading-6 text-foreground/85">{command}</div>}
      {detail && <div className="text-xs leading-5 text-muted-foreground">{detail}</div>}
    </div>
  </div>
);


const ToolCallDiff = ({
  className,
  ...props
}: ComponentProps<typeof DiffViewer>) => (
  <DiffViewer className={cn("mt-1 font-sans", className)} {...props} />
);

const FileGrid = ({ children }: { children: ReactNode }) => (
  <div className="flex flex-col gap-1 rounded-lg border bg-muted/30 px-3 py-2">
    {children}
  </div>
);


const ListContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const files: any[] = Array.isArray(output?.files) ? output.files : [];
  if (!files.length) return <ToolCallStep command="No files found" />;
  return (
    <FileGrid>
      {files.map((file: any) => {
        const name = file?.name
        const path = file?.path ?? "";
        const isDir = !!file?.is_dir;
        return (
          <div key={path || name} className="flex min-w-0 flex-col gap-0.5">
            <span className="truncate text-sm text-foreground/85">
              {name}{isDir ? "/" : ""}
            </span>
            {path && path !== name && (
              <span className="truncate text-[12.5px] text-muted-foreground">{path}</span>
            )}
          </div>
        );
      })}
    </FileGrid>
  );
};

const GrepContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const lines: string[] = Array.isArray(output?.lines) ? output.lines : [];
  const count = output?.matchCount ?? lines.length;
  return (
    <>
      <ToolCallStep command={`${count} match${count === 1 ? "" : "es"}`} />
      {lines.length > 0 && (
        <FileGrid>
          {lines.map((line: string, i: number) => (
            <span key={i} className="truncate text-[12.5px] text-muted-foreground">{line}</span>
          ))}
        </FileGrid>
      )}
    </>
  );
};

const ReadContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  return <ToolCallStep command={`${output?.lineCount ?? 0} lines`} />;
};

const WriteContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  return <ToolCallStep command={`Created · ${output?.lineCount ?? 0} lines`} />;
};

const EditContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const patch = typeof output?.patch === "string" ? output.patch : "";
  return patch ? <ToolCallDiff patch={patch} viewMode="unified" /> : <ToolCallStep command="Modified" />;
};


const BashContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const lines: string[] = Array.isArray(output?.lines) ? output.lines : [];
  const success = output?.success;
  if (!lines.length) {
    return <ToolCallStep command={success ? "Done" : `Exited ${output?.exitCode}`} />;
  }
  return (
    <FileGrid>
      {lines.slice(-20).map((line: string, i: number) => (
        <span key={i} className="truncate text-[12.5px] text-muted-foreground">{line}</span>
      ))}
    </FileGrid>
  );
};

const GitStatusContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const status = output?.status;
  if (typeof status === "string") {
    return <FileGrid>
        <pre className="whitespace-pre-wrap break-words text-[12.5px] text-muted-foreground">
        {status}
        </pre>
        </FileGrid>;
  }
  return null;
};

const CommitToolContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const {
    sha,
    shortSha,
    message,
    branch,
    timestamp,
    additions = 0,
    deletions = 0,
  } = output ?? {};
  return (
    <Commit>
      <CommitHeader>
        <CommitInfo>
          <CommitMessage>{message}</CommitMessage>
          <CommitMetadata>
            {shortSha && <CommitHash>{shortSha}</CommitHash>}
            {shortSha && branch && <CommitSeparator />}
            {branch && <span className="text-xs text-muted-foreground">{branch}</span>}
            {(additions || deletions) && (
              <>
                <CommitSeparator />
                <span className="text-xs">
                  <span className="text-green-600">+{additions}</span>{" "}
                  <span className="text-red-600">-{deletions}</span>
                </span>
              </>
            )}
            {timestamp && (
              <>
                <CommitSeparator />
                <CommitTimestamp date={new Date(timestamp)} />
              </>
            )}
          </CommitMetadata>
        </CommitInfo>
        <CommitActions>
          {sha && <CommitCopyButton hash={sha} />}
        </CommitActions>
      </CommitHeader>
    </Commit>
  );
};

const PushContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  return (
    <ToolCallStep
      command={`Pushed to ${output?.branch ?? "remote"}`}
      detail={
        output?.compareUrl ? (
          <a href={String(output.compareUrl)} target="_blank" rel="noreferrer" className="underline text-primary underline-offset-2">
            View changes →
          </a>
        ) : undefined
      }
    />
  );
};

// const PullRequestContent = ({ part }: { part: ToolPart }) => {
//   const output = (part as any).output;
//   return (
//     <ToolCallStep
//       command="PR created"
//       detail={
//         output?.url ? (
//           <a href={String(output.url)} target="_blank" rel="noreferrer" className="underline underline-offset-2">
//             {String(output.url)}
//           </a>
//         ) : undefined
//       }
//     />
//   );
// };

const WebSearchContent = ({ part }: { part: ToolPart }) => {
  const output = (part as any).output;
  const results: any[] = Array.isArray(output?.results) ? output.results : [];

  return (
    <ToolCallStep
      detail={
          <ChainOfThought defaultOpen={false} className="pl-1">
      <ChainOfThoughtHeader>
        {results.length
          ? `${results.length} source${results.length === 1 ? "" : "s"} visited`
          : "No sources found"}
      </ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        <ChainOfThoughtStep
          icon={SearchIcon}
          label="Searched the web"
          status="complete"
        >
          {results.length > 0 ? (
            <ChainOfThoughtSearchResults>
              {results.slice(0, 8).map((result: any, i: number) => (
                <ChainOfThoughtSearchResult key={result?.url ?? i} asChild>
                  <a href={result?.url} target="_blank" rel="noreferrer">
                    {result?.title ?? result?.url}
                  </a>
                </ChainOfThoughtSearchResult>
              ))}
            </ChainOfThoughtSearchResults>
          ) : null}
        </ChainOfThoughtStep>
      </ChainOfThoughtContent>
    </ChainOfThought>
      }
    />
  
  );
};


const normalizeCommitSubject = (value: unknown) => {
  const text = String(value ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean)
    ?.replace(/^[-*•\s]+/, "")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return "Commit changes";
  return text.length > 72 ? `${text.slice(0, 69).trim()}...` : text;
};


const EditSubtitle = ({ part }: { part: ToolPart }) => {
  const input = (part as any).input;
  const output = (part as any).output;
  const additions = output.additions;
  const deletions = output.deletions;

  return (
    <span className="flex flex-wrap items-center gap-2">
      <span>{input?.path}</span>
      <span className="text-green-600">+{additions}</span>
      <span className="text-red-600">-{deletions}</span>
    </span>
  );
};

const getTitleSubtitle = (part: ToolPart) => {
  const input = (part as any).input;
  const output = (part as any).output;
  const hasPatch = typeof output?.patch === "string" && output.patch.length > 0;
  switch (part.type) {
    case "tool-list":       return { title: "List",         subtitle: input?.path };
    case "tool-grep":       return { title: "Search",       subtitle: input?.query };
    case "tool-read":       return { title: "Read",         subtitle: input?.path };
    case "tool-write":      return { title: "Write",        subtitle: input?.path };
    case "tool-edit":       return { title: "Edit",         subtitle: hasPatch 
        ? <EditSubtitle part={part} /> : input?.path };
    case "tool-bash":       return { title: "Ran",          subtitle: input?.command };
    case "tool-git_status": return { title: "Status",       subtitle: "Checked repository status" };
    case "tool-commit":     return { title: "Commit",       
        subtitle: normalizeCommitSubject(input?.message) };
    case "tool-git_push":   return { title: "Push",         subtitle: undefined };
    case "tool-create_pr":  return { title: "Pull Request", subtitle: input?.title };
    case "tool-web_search": return { title: "Search Web",   subtitle: input?.query };
    default:                return { title: part.type,      subtitle: undefined };
    
  }
};


const CONTENT_MAP: Record<string, (props: { part: ToolPart }) => ReactNode> = {
  "tool-list":       ({ part }) => <ListContent part={part} />,
  "tool-grep":       ({ part }) => <GrepContent part={part} />,
  "tool-read":       ({ part }) => <ReadContent part={part} />,
  "tool-write":      ({ part }) => <WriteContent part={part} />,
  "tool-edit":       ({ part }) => <EditContent part={part} />,
  "tool-bash":       ({ part }) => <BashContent part={part} />,
  "tool-git_status": ({ part }) => <GitStatusContent part={part} />,
  "tool-commit":     ({ part }) => <CommitToolContent part={part} />,
  "tool-git_push":   ({ part }) => <PushContent part={part} />,
  //"tool-create_pr":  ({ part }) => <PullRequestContent part={part} />,
  "tool-web_search": ({ part }) => <WebSearchContent part={part} />,
};



export const renderToolPart = (
    messageId:string,
    part: ToolPart,
    partIndex:number
) => {
    const Content = CONTENT_MAP[part.type]
    if(!Content) return null

    const isRunning = part.state === "input-streaming" || part.state === "input-available";
    const isDone = part.state === "output-available";
    const isError = part.state === "output-error";

    const {title, subtitle} = getTitleSubtitle(part)

    return (
        <ToolCall 
          key={`${messageId}-${partIndex}-${part.toolCallId}`}
          title={title}
          subtitle={subtitle}
          isRunning={isRunning}
        >
          {isDone && <Content part={part} />}
          {isError && <ToolCallStep command={part.errorText ?? "An error coccured"}  />}
        </ToolCall>
    )
}

