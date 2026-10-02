import Logo from "@/components/logo";
import { ModeToggle } from "@/components/mode-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PROTECTED_ROUTES, PUBLIC_ROUTES } from "@/routes/route";
import {
  ArrowRight,
  BookOpen,
  Bot,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Database,
  Container,
  Copy,
  FileSearch,
  GitPullRequest,
  GitBranch,
  History,
  PanelsTopLeft,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  TestTube2,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useState } from "react";

type GuideStep = {
  icon: typeof GitBranch;
  number: string;
  title: string;
  description: string;
  details: string[];
};

const workflow: GuideStep[] = [
  {
    icon: GitBranch,
    number: "01",
    title: "Connect a repository",
    description: "Start a new operation, connect GitHub, then choose the repository and branch you want to investigate.",
    details: ["Use only repositories available to your GitHub connection.", "Select the branch that reflects the problem you are investigating."],
  },
  {
    icon: SearchCheck,
    number: "02",
    title: "Ask for an investigation",
    description: "Describe the symptom in plain language: a 502, failing PR checks, a Docker issue, a risky change, or a question about the codebase.",
    details: ["Base64 maps the repository and retrieves source, configuration, CI, and runbook evidence.", "It marks repository text, workflows, and CI output as untrusted evidence—not instructions."],
  },
  {
    icon: FileSearch,
    number: "03",
    title: "Review evidence before conclusions",
    description: "Use the timeline, evidence cards, and structured hypotheses to see what was inspected and why the diagnosis is supported.",
    details: ["A failed Actions run is tied to its original commit SHA, not silently to current HEAD.", "When evidence is missing, the result says so instead of inventing a root cause."],
  },
  {
    icon: ClipboardCheck,
    number: "04",
    title: "Approve only an exact plan",
    description: "For an eligible remediation, inspect the exact diff, validation steps, risk level, and delivery plan before approving it.",
    details: ["Read-only diagnostics run without approval.", "File changes, commits, pushes, and draft pull requests remain approval-gated."],
  },
  {
    icon: GitPullRequest,
    number: "05",
    title: "Track delivery and replay runs",
    description: "After approval, Base64 validates the exact approved change and can deliver it on a safe branch as a draft PR.",
    details: ["Approval becomes invalid if the branch, SHA, diff, or arguments change.", "The developer run inspector replays the evidence and decisions without rerunning mutations."],
  },
];

const features = [
  { icon: FileSearch, title: "Evidence-first investigations", text: "Diagnoses cite repository files, configuration, CI excerpts, and other scoped evidence." },
  { icon: Database, title: "Repository map and RAG", text: "Index code, runbooks, Docker, CI, and configuration for lexical-first retrieval with provenance." },
  { icon: TerminalSquare, title: "GitHub Actions intelligence", text: "Read-only CI runs, failed jobs, bounded redacted logs, and workflow-at-SHA context." },
  { icon: Container, title: "Docker and Compose checks", text: "Investigate build, runtime, binding, dependency, and compose-configuration problems with repository evidence." },
  { icon: TestTube2, title: "Sandboxed validation", text: "Eligible validation uses the command registry and execution policy—not arbitrary commands copied from a workflow." },
  { icon: Wrench, title: "Safe remediation", text: "Exact unified diffs, candidate workspaces, deterministic risk checks, and no broad autonomous edits." },
  { icon: ShieldCheck, title: "Approval controls", text: "Every mutation is bound to the reviewed plan and invalidated when its inputs change." },
  { icon: GitPullRequest, title: "Draft-PR delivery", text: "After approval, Base64 can validate, commit on a safe branch, push, and prepare a reviewable draft pull request." },
  { icon: History, title: "Replay and observability", text: "Inspect evidence, tools, policy decisions, timing, and delivery history without hidden reasoning." },
  { icon: ClipboardCheck, title: "Operational memory", text: "Related historical incidents can inform an investigation, while current evidence remains the source of truth." },
  { icon: Bot, title: "Base64 Guide", text: "Use the in-app guide button for setup help and questions about any product feature." },
];

function SectionTitle({ eyebrow, title, body }: { eyebrow: string; title: string; body: string }) {
  return (
    <div className="max-w-2xl">
      <p className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{title}</h2>
      <p className="mt-3 leading-7 text-muted-foreground">{body}</p>
    </div>
  );
}

export default function GuidePage() {
  const [selectedStep, setSelectedStep] = useState(workflow[0].number);
  const [selectedFeature, setSelectedFeature] = useState(features[0].title);
  const [copiedPrompt, setCopiedPrompt] = useState<string | null>(null);
  const activeStep = workflow.find((step) => step.number === selectedStep) ?? workflow[0];
  const activeFeature = features.find((feature) => feature.title === selectedFeature) ?? features[0];
  const copyPrompt = async (prompt: string) => {
    try {
      await navigator.clipboard?.writeText(prompt);
    } catch {
      // Clipboard access can be unavailable on an insecure local preview.
    }
    setCopiedPrompt(prompt);
  };
  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-10 border-b border-border/80 bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 sm:px-8">
          <div className="flex items-center gap-2">
            <Logo />
            <span className="hidden border-l border-border pl-3 text-sm text-muted-foreground sm:inline">Product guide</span>
          </div>
          <div className="flex items-center gap-2">
            <Button asChild className="hidden sm:inline-flex" size="sm">
              <Link to={PROTECTED_ROUTES.NEW}>Start an operation <ArrowRight /></Link>
            </Button>
            <ModeToggle />
          </div>
        </div>
      </header>

      <section className="border-b border-border bg-[radial-gradient(ellipse_at_top,var(--color-accent),transparent_62%)]">
        <div className="mx-auto max-w-6xl px-5 py-16 sm:px-8 sm:py-24">
          <Badge variant="secondary"><BookOpen /> Getting started</Badge>
          <h1 className="mt-5 max-w-3xl text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
            Investigate confidently. Change safely.
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-8 text-muted-foreground">
            Base64 Ops is a DevOps command center for evidence-backed repository investigations and controlled delivery. This guide explains every major surface and the safe workflow behind it.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild size="lg"><Link to={PROTECTED_ROUTES.NEW}>Start an operation <ArrowRight /></Link></Button>
            <Button asChild variant="outline" size="lg"><Link to={PUBLIC_ROUTES.DEMO}>View the safe CI demo <ChevronRight /></Link></Button>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-16 sm:px-8">
        <SectionTitle eyebrow="The workflow" title="From question to reviewable delivery" body="Base64 keeps diagnosis, proposed change, approval, and delivery visibly separate. It never turns repository or CI text into permission to act." />
        <ol className="mt-10 grid gap-4 lg:grid-cols-5" aria-label="Interactive workflow steps">
          {workflow.map(({ icon: Icon, number, title, description, details }) => (
            <li key={number}>
            <button type="button" onClick={() => setSelectedStep(number)} aria-pressed={selectedStep === number} className={`h-full w-full rounded-xl border bg-card p-5 text-left shadow-sm transition hover:border-primary/50 hover:bg-primary/5 ${selectedStep === number ? "border-primary ring-2 ring-primary/20" : "border-border"}`}>
              <div className="flex items-center justify-between"><Icon className="size-5 text-primary" /><span className="font-mono text-xs text-muted-foreground">{number}</span></div>
              <h3 className="mt-5 font-semibold text-card-foreground">{title}</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
              <ul className="mt-4 space-y-2 border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
                {details.map((detail) => <li className="flex gap-2" key={detail}><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-primary" />{detail}</li>)}
              </ul>
            </button></li>
          ))}
        </ol>
        <div className="mt-5 rounded-xl border border-primary/25 bg-primary/5 p-5" aria-live="polite">
          <p className="text-xs font-semibold tracking-[0.16em] text-primary uppercase">Selected step {activeStep.number}</p>
          <h3 className="mt-2 font-semibold">{activeStep.title}</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{activeStep.description} Select another step above to explore the workflow in any order.</p>
        </div>
      </section>

      <section className="border-y border-border bg-muted/45">
        <div className="mx-auto max-w-6xl px-5 py-16 sm:px-8">
          <SectionTitle eyebrow="Feature map" title="What each part of the app does" body="Use these surfaces together: the operation workspace for execution, evidence cards for verification, and the run inspector for debugging a completed run." />
          <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {features.map(({ icon: Icon, title, text }) => (
              <button type="button" onClick={() => setSelectedFeature(title)} aria-pressed={selectedFeature === title} className={`rounded-xl border bg-card p-5 text-left transition hover:border-primary/50 hover:bg-primary/5 ${selectedFeature === title ? "border-primary ring-2 ring-primary/20" : "border-border"}`} key={title}>
                <Icon className="size-5 text-primary" />
                <h3 className="mt-4 font-semibold text-card-foreground">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{text}</p>
              </button>
            ))}
          </div>
          <div className="mt-5 rounded-xl border border-border bg-card p-5" aria-live="polite">
            <p className="text-xs font-semibold tracking-[0.16em] text-primary uppercase">Feature detail</p>
            <h3 className="mt-2 font-semibold">{activeFeature.title}</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{activeFeature.text} Select a feature card to compare it with the rest of the command center.</p>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-10 px-5 py-16 sm:px-8 lg:grid-cols-[1.1fr_0.9fr]">
        <div>
          <SectionTitle eyebrow="Common tasks" title="Prompts that work well" body="Be concrete about the symptom, target repository, and whether you want investigation only or a proposed remediation." />
          <div className="mt-7 space-y-3">
            {["Why did GitHub Actions fail on this PR?", "Production started returning 502s. Investigate and propose a safe fix.", "Is MONGO_URI configured everywhere this service needs it?", "What changed in the latest commit?", "Explain this delivery plan before I approve it."].map((prompt) => (
              <button type="button" onClick={() => void copyPrompt(prompt)} className="flex w-full items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3 text-left font-mono text-sm text-card-foreground transition hover:border-primary/50 hover:bg-primary/5" key={prompt}><span>{prompt}</span><span className="shrink-0 text-xs font-sans text-primary">{copiedPrompt === prompt ? "Copied" : <Copy className="size-4" aria-label="Copy prompt" />}</span></button>
            ))}
          </div>
        </div>
        <aside className="rounded-2xl border border-primary/25 bg-primary/5 p-6">
          <Sparkles className="size-6 text-primary" />
          <h2 className="mt-4 text-xl font-semibold">Need help while working?</h2>
          <p className="mt-3 leading-7 text-muted-foreground">Open <strong className="font-medium text-foreground">Ask Base64 Guide</strong> in the lower-right corner of the workspace. It can explain setup, evidence, approval, CI investigation, and delivery controls in context.</p>
          <div className="mt-6 rounded-lg border border-border bg-background/70 p-4 text-sm leading-6 text-muted-foreground">
            <span className="font-medium text-foreground">If indexing cannot persist:</span> Base64 uses lexical repository search first. Check the backend MongoDB connection and retry indexing; secret values are never displayed in the product response.
          </div>
          <Button asChild className="mt-6" variant="outline"><Link to={PROTECTED_ROUTES.NEW}>Open workspace <PanelsTopLeft /></Link></Button>
        </aside>
      </section>

      <footer className="border-t border-border py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-5 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <span>Base64 Ops · evidence before execution</span>
          <Link className="font-medium text-primary hover:underline" to={PROTECTED_ROUTES.NEW}>Start a new operation</Link>
        </div>
      </footer>
    </main>
  );
}
