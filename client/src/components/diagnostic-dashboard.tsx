import { useMemo, useState } from "react";
import { motion } from "motion/react";
import { Bot, LoaderCircle, Play, ShieldCheck, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { DiffViewer } from "@/components/diff-viewer";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { applyApprovedPatch, streamDiagnostic } from "@/lib/api";

type Props = {
  slugId: string;
  initialLog?: string;
  initialPatch?: string;
  approvalId?: string;
};

type DiagnosticStatus = "idle" | "streaming" | "complete" | "error";

export default function DiagnosticDashboard({ slugId, initialLog = "", initialPatch = "", approvalId }: Props) {
  const [failedLog, setFailedLog] = useState(initialLog);
  const [gitDiff, setGitDiff] = useState(initialPatch);
  const [analysis, setAnalysis] = useState("");
  const [proposalPatch, setProposalPatch] = useState<string | null>(initialPatch || null);
  const [status, setStatus] = useState<DiagnosticStatus>("idle");
  const [stateLabel, setStateLabel] = useState("Ready to inspect CI output");
  const [deploying, setDeploying] = useState(false);
  const activePatch = proposalPatch || initialPatch;
  const canDiagnose = failedLog.trim().length > 0 && status !== "streaming";
  const logLines = useMemo(() => failedLog.split("\n").slice(-80).join("\n"), [failedLog]);

  const runDiagnostic = async () => {
    setStatus("streaming");
    setAnalysis("");
    setProposalPatch(null);
    setStateLabel("Opening secure diagnostic stream...");
    try {
      await streamDiagnostic(
        { sessionSlugId: slugId, failedLog, gitDiff },
        (event, data) => {
          if (event === "state") setStateLabel(String(data.label ?? "Working..."));
          if (event === "log_analysis") setAnalysis((current) => current + String(data.delta ?? ""));
          if (event === "code_fix") {
            setProposalPatch(typeof data.patch === "string" && data.patch.trim() ? data.patch : null);
            if (data.message) setAnalysis((current) => `${current}\n\n> ${String(data.message)}`);
          }
          if (event === "complete") {
            setStatus("complete");
            setStateLabel(data.mutation_performed ? "Completed" : "Diagnosis complete — no mutation performed");
          }
          if (event === "error") throw new Error(String(data.message ?? "Diagnostic stream failed"));
        },
      );
      setStatus((current) => (current === "streaming" ? "complete" : current));
    } catch (error) {
      setStatus("error");
      setStateLabel(error instanceof Error ? error.message : "Unable to analyze this log");
    }
  };

  const deployApprovedPatch = async () => {
    if (!approvalId) return;
    setDeploying(true);
    try {
      const result = await applyApprovedPatch(slugId, approvalId);
      toast.success(result.result?.pull_request_url ? "Draft pull request created." : "Approved delivery completed.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Approved patch could not be delivered.");
    } finally {
      setDeploying(false);
    }
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-cyan-400/20 bg-slate-950 text-slate-100 shadow-2xl shadow-cyan-950/30">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 bg-slate-900/80 px-4 py-3">
        <div className="flex items-center gap-2"><Sparkles className="size-4 text-cyan-300" /><div><h2 className="text-sm font-semibold">Live CI diagnostic</h2><p className="text-xs text-slate-400">Redacted, untrusted evidence · no mutation during analysis</p></div></div>
        <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-xs text-cyan-100">{stateLabel}</span>
      </header>
      <div className="grid gap-px bg-white/10 xl:grid-cols-2">
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="bg-slate-950 p-4">
          <label className="mb-2 block text-xs font-medium text-slate-300">Failed CI log</label>
          <Textarea value={failedLog} onChange={(event) => setFailedLog(event.target.value)} placeholder="Paste bounded CI output here…" className="min-h-52 border-white/10 bg-slate-900 font-mono text-xs leading-5 text-slate-100 placeholder:text-slate-600" />
          <p className="mt-2 text-xs text-slate-500">Preview: {logLines ? `${logLines.split("\n").length} lines` : "no log supplied"}. Secrets are redacted server-side before processing.</p>
          <Button className="mt-4 bg-cyan-500 text-slate-950 hover:bg-cyan-300" onClick={runDiagnostic} disabled={!canDiagnose}>
            {status === "streaming" ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />} Analyze failure
          </Button>
        </motion.div>
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 }} className="bg-slate-950 p-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300"><Bot className="size-4 text-violet-300" /> Agent breakdown</div>
          <div aria-live="polite" className="min-h-52 whitespace-pre-wrap rounded-lg border border-white/10 bg-slate-900 p-3 font-mono text-xs leading-6 text-slate-200">{analysis || "Run a diagnostic to receive a streamed, evidence-bounded explanation."}{status === "streaming" ? <span className="ml-1 inline-block size-1.5 animate-pulse rounded-full bg-cyan-300" /> : null}</div>
        </motion.div>
      </div>
      <div className="grid gap-px border-t border-white/10 bg-white/10 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <div className="bg-slate-950 p-4"><label className="mb-2 block text-xs font-medium text-slate-300">Workspace diff context</label><Textarea value={gitDiff} onChange={(event) => setGitDiff(event.target.value)} placeholder="Optional current git diff…" className="min-h-48 border-white/10 bg-slate-900 font-mono text-xs leading-5 text-slate-100 placeholder:text-slate-600" /><p className="mt-2 text-xs text-slate-500">Context is treated as untrusted data and never executed as a command.</p></div>
        <div className="bg-slate-950 p-4"><div className="mb-2 flex items-center justify-between gap-3"><p className="text-xs font-medium text-slate-300">Exact proposal diff</p><Button type="button" size="sm" onClick={deployApprovedPatch} disabled={!approvalId || deploying} className="bg-violet-500 text-white hover:bg-violet-400 disabled:opacity-50"><ShieldCheck className={deploying ? "size-4 animate-pulse" : "size-4"} />{deploying ? "Delivering…" : "Deploy approved patch"}</Button></div>{activePatch ? <DiffViewer patch={activePatch} viewMode="split" className="max-h-80 border-white/10 bg-slate-900 text-xs" /> : <div className="flex min-h-48 items-center rounded-lg border border-dashed border-white/15 bg-slate-900/70 p-4 text-sm leading-6 text-slate-400">No executable proposal yet. Ask the repository-backed assistant to create a validated DeliveryPlan; its exact diff will appear here for review.</div>}<p className="mt-2 text-xs text-slate-500">Delivery is enabled only for a pre-existing exact approval. It never commits an arbitrary pasted patch.</p></div>
      </div>
    </section>
  );
}
