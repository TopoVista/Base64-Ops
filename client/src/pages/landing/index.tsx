import {
  ArrowRight,
  Check,
  ChevronRight,
  FileCode2,
  FileSearch,
  GitBranch,
  Play,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import logo from "@/assets/logo.png";

const previewSteps = [
  {
    label: "Failure",
    title: "A failing run is pinned before anything changes.",
    body: "The exact workflow, commit, log window, and repository state stay attached to the investigation.",
  },
  {
    label: "Evidence",
    title: "Every claim stays connected to a source.",
    body: "Logs, workflow files, repository paths, and documented runbooks become inspectable evidence.",
  },
  {
    label: "Change",
    title: "Only the smallest supported repair is proposed.",
    body: "A constrained diff shows precisely what changes, why it is applicable, and what validation ran.",
  },
  {
    label: "Approval",
    title: "Delivery waits for a human decision.",
    body: "Approval binds the plan, base SHA, diff, and arguments. No quiet mutations or broad permissions.",
  },
] as const;

function Brand() {
  return (
    <Link to="/" className="landing-brand" aria-label="Base64 Ops home">
      <img src={logo} alt="" aria-hidden="true" />
      <span>Base64 <em>Ops</em></span>
    </Link>
  );
}

function RepairPreview() {
  const [activeStep, setActiveStep] = useState(0);
  const active = previewSteps[activeStep];

  return (
    <section className="repair-preview" aria-label="Base64 Ops repair workflow preview">
      <div className="preview-topbar">
        <span className="window-dots" aria-hidden="true"><i /><i /><i /></span>
        <span className="preview-product"><img src={logo} alt="" /> Base64 Ops</span>
        <span className="preview-repo"><GitBranch size={13} /> acme/web-platform <b>main</b></span>
      </div>
      <div className="preview-main">
        <aside className="preview-rail" aria-label="Repair stages">
          {previewSteps.map((step, index) => (
            <button
              type="button"
              key={step.label}
              className={index === activeStep ? "is-active" : ""}
              aria-current={index === activeStep ? "step" : undefined}
              onClick={() => setActiveStep(index)}
            >
              <span>{`0${index + 1}`}</span>{step.label}
            </button>
          ))}
        </aside>
        <div className="preview-content">
          <div className="preview-incident">
            <div className="failure-mark">!</div>
            <div><strong>Frontend CI failed</strong><span>GitHub Actions · build-and-test · run #842</span></div>
            <time>2m ago</time>
          </div>
          <div className="preview-log" aria-label="Failed CI excerpt">
            <span>$ npm run build</span>
            <span>npm ERR! code ENOENT</span>
            <b>npm ERR! /repo/server/package.json</b>
            <span>workflow command exited with code 1</span>
          </div>
          <div className="preview-evidence">
            <div><FileSearch size={16} /><span><strong>CI log</strong><small>failed step</small></span><ChevronRight size={15} /></div>
            <div><FileCode2 size={16} /><span><strong>Workflow @ abc1234</strong><small>working directory</small></span><ChevronRight size={15} /></div>
            <div><GitBranch size={16} /><span><strong>Repository path</strong><small>client/package.json</small></span><ChevronRight size={15} /></div>
          </div>
          <div className="preview-diff">
            <div className="diff-header"><span>Proposed change</span><span>1 file</span></div>
            <code><i>−</i> working-directory: ./server</code>
            <code><b>+</b> working-directory: ./client</code>
          </div>
          <div className="preview-approval">
            <div><ShieldCheck size={18} /><span><strong>{active.title}</strong><small>{active.body}</small></span></div>
            <button type="button" onClick={() => setActiveStep((current) => (current + 1) % previewSteps.length)}>
              Review and approve <ArrowRight size={15} />
            </button>
          </div>
        </div>
      </div>
      <div className="preview-progress" aria-live="polite">
        {previewSteps.map((step, index) => <span key={step.label} className={index <= activeStep ? "is-complete" : ""}><i>{index < activeStep ? <Check size={11} /> : index + 1}</i>{step.label}</span>)}
      </div>
    </section>
  );
}

function WorkflowBand() {
  const items = [
    ["Run failure", "Capture the failing run with its source context."],
    ["Pinned evidence", "Lock logs, workflow state, and repository facts."],
    ["Exact diff", "Propose the smallest evidence-backed repair."],
    ["Human approval", "Deliver only the reviewable change that was approved."],
  ] as const;

  return (
    <section className="workflow-band" id="how-it-works" aria-labelledby="workflow-heading">
      <div className="section-shell workflow-intro">
        <h2 id="workflow-heading">The repair is only<br />as strong as its record.</h2>
        <p>Base64 Ops holds the sequence together: original failure, collected evidence, minimal diff, explicit human decision.</p>
      </div>
      <div className="section-shell workflow-map">
        {items.map(([title, description], index) => (
          <article key={title}>
            <span>{`0${index + 1}`}</span>
            <h3>{title}</h3>
            <p>{description}</p>
            {index < items.length - 1 && <ArrowRight className="workflow-arrow" size={18} aria-hidden="true" />}
          </article>
        ))}
        <div className="workflow-approval-card">
          <div><strong>Approval</strong><span><Check size={14} /> verified safe</span></div>
          <p>Only <code>.github/workflows/ci.yml</code></p>
          <button type="button">Approve change <ArrowRight size={14} /></button>
        </div>
      </div>
    </section>
  );
}

export default function LandingPage() {
  useEffect(() => {
    document.title = "Base64 Ops — evidence-first delivery";
  }, []);

  return (
    <main className="landing-page">
      <header className="landing-header">
        <Brand />
        <nav aria-label="Primary navigation">
          <a href="#product">Product</a>
          <a href="#how-it-works">How it works</a>
          <a href="#safety">Safety</a>
        </nav>
        <div className="landing-header-actions">
          <Link to="/sign-in" className="login-link">Sign in</Link>
          <Link to="/demo" className="primary-action">See safe repair <ArrowRight size={17} /></Link>
        </div>
      </header>

      <section className="hero-section" id="product">
        <div className="hero-copy">
          <h1>Every repair deserves a case file.</h1>
          <div className="case-file-meta" aria-label="Repair workflow summary"><span>CI incident</span><i aria-hidden="true" /><span>evidence record</span><i aria-hidden="true" /><span>human consent</span></div>
          <p>Base64 Ops turns a broken run into a reviewable change without ever separating the repair from the evidence that supports it.</p>
          <div className="hero-actions">
            <Link to="/demo" className="primary-action"><Play size={16} fill="currentColor" /> Inspect the repair</Link>
            <Link to="/sign-in" className="secondary-action">Sign in <ArrowRight size={17} /></Link>
          </div>
        </div>
        <RepairPreview />
      </section>

      <div className="hero-rule" aria-hidden="true"><span>01</span><strong>Capture evidence</strong><span>02</span><strong>Review exact change</strong><span>03</span><strong>Approve delivery</strong></div>
      <WorkflowBand />

      <section className="evidence-section" id="safety" aria-labelledby="evidence-heading">
        <div className="section-shell evidence-header">
          <h2 id="evidence-heading">Every conclusion<br />carries receipts.</h2>
          <p>Logs, workflow revisions, repository paths, exact diffs, validation results, and approvals stay attached—not summarized away.</p>
        </div>
        <div className="section-shell evidence-columns">
          <article>
            <span className="feature-number">01</span>
            <h3>Anchor the failure.</h3>
            <p>Pin the exact error, logs, stack trace, and relevant code. Every claim is linked to a source so you can verify the reasoning.</p>
            <div className="feature-art feature-log"><b>Run log</b><span>› npm run build</span><span className="error-line">FAIL: package.json not found</span><span>› expected ./client, received ./server</span></div>
          </article>
          <article>
            <span className="feature-number">02</span>
            <h3>Limit the change.</h3>
            <p>Get a precise, one-file diff that addresses the failure. No drive-by edits, no unrelated refactors.</p>
            <div className="feature-art feature-diff"><b>Proposed change <small>1 file</small></b><span>− working-directory: ./server</span><span>+ working-directory: ./client</span></div>
          </article>
          <article>
            <span className="feature-number">03</span>
            <h3>Deliver with consent.</h3>
            <p>You review the evidence, the exact diff, and the scope of the change. Nothing is applied without your approval.</p>
            <div className="feature-art feature-consent"><b>Approval scope <small>1 file</small></b><span><Check size={14} /> Review evidence</span><span><Check size={14} /> Inspect diff</span><button type="button">Approve change</button></div>
          </article>
        </div>
      </section>

      <section className="closing-section">
        <h2>Give the next change a record worth reviewing.</h2>
        <p>Open a workspace where scope, validation, source evidence, and consent are one continuous operation.</p>
        <Link to="/sign-in" className="primary-action">Open Base64 Ops <ArrowRight size={17} /></Link>
      </section>

      <footer className="landing-footer">
        <Brand />
        <div><a href="#product">Product</a><a href="#how-it-works">How it works</a><Link to="/guide">Docs</Link><Link to="/sign-in">Sign in</Link></div>
      </footer>
    </main>
  );
}
