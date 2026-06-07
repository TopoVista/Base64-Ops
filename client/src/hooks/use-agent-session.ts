import { BASE_API_URL } from "@/lib/env";
import { decideSessionApproval } from "@/lib/api";
import type {
  AgentChatStatus,
  AgentMessage,
  AgentSessionEvent,
  ApprovalRequest,
  RagSource,
  TimelineEvent,
} from "@/types/agent.type";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

type SendMessageInput = {
  slugId: string;
  repoUrl: string;
  defaultBranch: string;
  message: string;
};

const parseSsePayloads = (buffer: string) => {
  const events: AgentSessionEvent[] = [];
  const blocks = buffer.split(/\n\n/);
  const rest = blocks.pop() ?? "";

  for (const block of blocks) {
    const eventLine = block
      .split("\n")
      .find((line) => line.startsWith("event:"));
    const dataLine = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.replace(/^data:\s?/, ""))
      .join("");
    if (!eventLine) continue;
    try {
      events.push({
        type: eventLine.replace(/^event:\s?/, ""),
        data: dataLine ? JSON.parse(dataLine) : {},
      } as AgentSessionEvent);
    } catch {
      events.push({
        type: "error",
        data: { message: "Unable to parse stream event" },
      });
    }
  }

  return { events, rest };
};

export const useAgentSession = (initialMessages: AgentMessage[] = []) => {
  const [messages, setMessages] = useState<AgentMessage[]>(initialMessages);
  const [sources, setSources] = useState<RagSource[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [status, setStatus] = useState<AgentChatStatus>("idle");
  const abortRef = useRef<AbortController | null>(null);

  const applyEvent = useCallback((event: AgentSessionEvent) => {
    switch (event.type) {
      case "tool.started": {
        const item = event.data as TimelineEvent;
        setTimeline((current) => [
          ...current,
          {
            id: item.id ?? crypto.randomUUID(),
            label: item.label ?? item.name ?? "Tool started",
            detail: item.detail,
            status: "started",
          },
        ]);
        break;
      }
      case "tool.completed": {
        const item = event.data as TimelineEvent;
        setTimeline((current) => [
          ...current,
          {
            id: item.id ?? crypto.randomUUID(),
            label: item.label ?? item.name ?? "Tool completed",
            detail: item.detail,
            status: item.status ?? "completed",
            risk: item.risk,
          },
        ]);
        break;
      }
      case "rag.sources": {
        const data = event.data as { sources?: RagSource[] };
        setSources(data.sources ?? []);
        break;
      }
      case "approval.requested": {
        const data = event.data as { approval: ApprovalRequest };
        setApprovals((current) => [
          data.approval,
          ...current.filter((approval) => approval.id !== data.approval.id),
        ]);
        toast.warning("Approval required before running a risky operation");
        break;
      }
      case "message.delta": {
        const data = event.data as { id: string; delta: string };
        setMessages((current) => {
          const existing = current.find((message) => message.id === data.id);
          if (existing) {
            return current.map((message) =>
              message.id === data.id
                ? { ...message, content: `${message.content}${data.delta}` }
                : message,
            );
          }
          return [
            ...current,
            {
              id: data.id,
              role: "assistant",
              content: data.delta,
              sources,
            },
          ];
        });
        break;
      }
      case "message.completed": {
        const data = event.data as { message: AgentMessage };
        setMessages((current) =>
          current.map((message) =>
            message.id === data.message.id ? data.message : message,
          ),
        );
        break;
      }
      case "error": {
        const data = event.data as { message: string };
        setStatus("error");
        toast.error(data.message);
        break;
      }
      default:
        break;
    }
  }, [sources]);

  const sendMessage = useCallback(
    async ({ slugId, repoUrl, defaultBranch, message }: SendMessageInput) => {
      abortRef.current?.abort();
      const abortController = new AbortController();
      abortRef.current = abortController;
      setStatus("submitted");
      setTimeline([]);
      setSources([]);
      setApprovals([]);
      const userMessage: AgentMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: message,
        createdAt: new Date().toISOString(),
      };
      setMessages((current) => [...current, userMessage]);

      try {
        const response = await fetch(`${BASE_API_URL}session/chat`, {
          method: "POST",
          credentials: "include",
          signal: abortController.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slugId, repoUrl, defaultBranch, message }),
        });

        if (!response.ok || !response.body) {
          throw new Error("Unable to start agent stream");
        }

        setStatus("streaming");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSsePayloads(buffer);
          buffer = parsed.rest;
          parsed.events.forEach(applyEvent);
        }

        setStatus("idle");
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") {
          setStatus("idle");
          return;
        }
        setStatus("error");
        toast.error(error instanceof Error ? error.message : "Agent stream failed");
      }
    },
    [applyEvent],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setStatus("idle");
  }, []);

  const decideApproval = useCallback(
    async (
      slugId: string,
      approval: ApprovalRequest,
      decision: "approve" | "edit" | "reject",
    ) => {
      const result = await decideSessionApproval(slugId, approval.id, {
        decision,
        editedArgs: decision === "edit" ? approval.args : undefined,
      });
      setApprovals((current) =>
        current.map((item) =>
          item.id === approval.id ? { ...item, status: decision } : item,
        ),
      );
      if (result.message) {
        setMessages((current) => [...current, result.message as AgentMessage]);
      }
      toast.success(decision === "reject" ? "Approval rejected" : "Approved operation resumed");
    },
    [],
  );

  return {
    messages,
    setMessages,
    sources,
    timeline,
    approvals,
    status,
    sendMessage,
    stop,
    decideApproval,
  };
};
