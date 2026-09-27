import { askProductAssistant } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Bot, CircleHelp, LoaderCircle, Send, Sparkles, User, X } from "lucide-react";
import { Streamdown } from "streamdown";
import { useState } from "react";
import { toast } from "sonner";

type GuideMessage = { role: "user" | "assistant"; content: string };

const starterPrompts = [
  "How do I connect GitHub?",
  "How does approval work?",
  "How do I deploy this app?",
];

const ProductGuide = () => {
  const [conversationId, setConversationId] = useState<string>();
  const [messages, setMessages] = useState<GuideMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  const ask = async (question: string) => {
    const message = question.trim();
    if (!message || isSending) return;
    setInput("");
    setMessages((current) => [...current, { role: "user", content: message }]);
    setIsSending(true);
    try {
      const response = await askProductAssistant(message, conversationId);
      setConversationId(response.conversationId);
      setMessages((current) => [...current, { role: "assistant", content: response.answer }]);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Base64 Guide is unavailable");
    } finally {
      setIsSending(false);
    }
  };

  return (
    <>
      {isOpen ? (
        <aside
          aria-label="Base64 Guide"
          className="fixed inset-y-4 right-4 z-50 flex w-[min(25rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-2xl"
        >
          <header className="flex items-start gap-3 border-b border-border bg-card px-4 py-4">
            <span className="rounded-xl bg-primary/10 p-2 text-primary"><Sparkles className="size-4" /></span>
            <div className="min-w-0 flex-1">
              <h2 className="font-semibold tracking-tight">Base64 Guide</h2>
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                Clear answers about setup, features, and the safe DevOps workflow.
              </p>
            </div>
            <Button variant="ghost" size="icon-sm" onClick={() => setIsOpen(false)} aria-label="Close Base64 Guide">
              <X className="size-4" />
            </Button>
          </header>

          <ScrollArea className="min-h-0 flex-1 px-4">
            <div className="space-y-4 py-4">
              {messages.length === 0 ? (
                <section className="rounded-xl border border-border bg-muted/45 p-4 text-sm text-muted-foreground">
                  <p className="font-medium text-foreground">How can I help?</p>
                  <p className="mt-1.5 leading-6">
                    Ask about connecting GitHub, indexing a repository, reviewing evidence, approvals, pull requests, or deployment.
                  </p>
                </section>
              ) : null}

              {messages.map((message, index) => (
                <article
                  key={`${message.role}-${index}`}
                  className={message.role === "user" ? "ml-8 rounded-2xl bg-primary px-3.5 py-3 text-sm text-primary-foreground" : "mr-3 rounded-2xl border border-border bg-card px-3.5 py-3 text-sm text-card-foreground shadow-sm"}
                >
                  <div className="mb-2 flex items-center gap-1.5 text-xs font-medium opacity-70">
                    {message.role === "user" ? <User className="size-3" /> : <Bot className="size-3" />}
                    {message.role === "user" ? "You" : "Base64 Guide"}
                  </div>
                  {message.role === "assistant" ? (
                    <Streamdown className="guide-markdown leading-6 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_li]:my-1 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_strong]:font-semibold [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
                      {message.content}
                    </Streamdown>
                  ) : (
                    <p className="whitespace-pre-wrap leading-6">{message.content}</p>
                  )}
                </article>
              ))}
              {isSending ? (
                <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" /> Thinking…</div>
              ) : null}
            </div>
          </ScrollArea>

          <footer className="border-t border-border bg-card p-3">
            {messages.length === 0 ? (
              <div className="mb-3 flex flex-wrap gap-2">
                {starterPrompts.map((prompt) => (
                  <Button key={prompt} variant="outline" size="sm" className="h-auto whitespace-normal text-left" onClick={() => ask(prompt)}>
                    {prompt}
                  </Button>
                ))}
              </div>
            ) : null}
            <div className="flex items-end gap-2">
              <Textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") ask(input);
                }}
                placeholder="Ask about Base64 Ops…"
                className="min-h-11 max-h-32 resize-none bg-background"
              />
              <Button size="icon-lg" onClick={() => ask(input)} disabled={!input.trim() || isSending} aria-label="Send question">
                <Send className="size-4" />
              </Button>
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">Press Ctrl + Enter to send</p>
          </footer>
        </aside>
      ) : (
        <Button
          className="fixed right-5 bottom-5 z-40 h-11 gap-2 rounded-full px-4 shadow-lg"
          size="lg"
          onClick={() => setIsOpen(true)}
          aria-label="Ask Base64 Guide"
        >
          <CircleHelp className="size-4" />
          Ask Base64 Guide
        </Button>
      )}
    </>
  );
};

export default ProductGuide;
