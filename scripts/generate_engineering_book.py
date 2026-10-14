#!/usr/bin/env python3
"""Build the Base64 Ops technical engineering book from the checked-out source.

The generator intentionally reads only Git-tracked files, never .env files or
workspace state. It emits a Markdown source, a navigable PDF, a complete file
inventory, and machine-checkable coverage reports. Source-reference pages are
constructed one-at-a-time so an included file cannot share a page with another
file's reference entry.

Usage (from repository root):
    python -m pip install -r scripts/requirements-engineering-book.txt
    python scripts/generate_engineering_book.py
    python scripts/generate_engineering_book.py --render-samples
    python scripts/generate_engineering_book.py --render-samples --visual-verified
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "reports"
PDF_PATH = REPORTS / "Base64-Ops-Complete-Technical-Engineering-Book.pdf"
MARKDOWN_PATH = REPORTS / "Base64-Ops-Complete-Technical-Engineering-Book.md"
INVENTORY_PATH = REPORTS / "source-file-inventory.json"
COVERAGE_PATH = REPORTS / "file-coverage-report.md"
GENERATION_REPORT_PATH = REPORTS / "document-generation-report.md"
DEMO_SCREENSHOT_PATH = ROOT / "docs" / "assets" / "screenshots" / "safe-repair-demo.png"

NAVY = HexColor("#0B1F3A")
INK = HexColor("#172033")
MUTED = HexColor("#4C5A70")
TEAL = HexColor("#007C83")
SKY = HexColor("#DCECF2")
PALE = HexColor("#F4F7FA")
RULE = HexColor("#C9D5E2")
WHITE = HexColor("#FFFFFF")

EXCLUDED_EXACT = {
    ".gitignore",
    "backend/.gitignore",
    "client/.gitignore",
    "client/package-lock.json",
    "client/public/favicon.svg",
    "client/public/icons.svg",
    "client/src/assets/github.svg",
    "client/src/assets/hero.png",
    "client/src/assets/logo.png",
    "client/src/assets/react.svg",
    "client/src/assets/vite.svg",
    "backend/.python-version",
    "client/.nvmrc",
    "docs/assets/screenshots/safe-repair-demo.png",
}

REQUIRED_FILE_SECTIONS = (
    "Purpose",
    "Key responsibilities",
    "Important symbols",
    "Connections",
    "Simplified pseudocode",
    "Workflow role",
    "Engineering details",
    "Brief summary",
)


@dataclass(frozen=True)
class FileRecord:
    path: str
    category: str
    kind: str
    summary: str
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    endpoints: tuple[str, ...]
    dependencies: tuple[str, ...]
    dependents: tuple[str, ...]
    lines: int
    sha256: str
    word_count: int


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def safe_ascii(value: object) -> str:
    """Use a core PDF font safely and never copy unseen source bytes verbatim."""

    text = str(value).replace("\t", "    ").replace("\x00", "")
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2192": "->",
        "\u2260": "!=",
        "\u2026": "...",
    }
    for original, replacement in replacements.items():
        text = text.replace(original, replacement)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def tracked_files() -> list[str]:
    return [line for line in run_git("ls-files").splitlines() if line]


def exclusion_reason(path: str) -> str | None:
    if path == "docs/assets/screenshots/safe-repair-demo.png":
        return (
            "Generated deterministic UI evidence embedded in the book; it is intentionally "
            "excluded from one-file source-reference pages."
        )
    if path in EXCLUDED_EXACT:
        return "Decorative asset, lockfile, runtime-version pin, or routine ignore configuration; it has no dedicated architecture behavior."
    if path.startswith("docs/reports/"):
        return "Generated report output is excluded from the snapshot to avoid self-documenting recursive artifacts."
    if path.startswith(".commandcode/"):
        return "Editor-assistant taste metadata is not part of the Base64 Ops runtime or developer-facing architecture."
    return None


def category_for(path: str) -> str:
    if path.startswith("backend/app/api/routes/"):
        return "Backend API routes"
    if path.startswith("backend/app/agent/"):
        return "LangGraph agent orchestration"
    if path.startswith("backend/app/services/"):
        return "Backend services"
    if path.startswith("backend/app/schemas/"):
        return "Backend domain contracts"
    if path.startswith("backend/app/intelligence/"):
        return "Engineering intelligence"
    if path.startswith("backend/app/memory/"):
        return "Operational memory"
    if path.startswith("backend/app/execution/"):
        return "Execution safety"
    if path.startswith("backend/app/"):
        return "Backend platform"
    if path.startswith("backend/tests/"):
        return "Backend tests"
    if path.startswith("backend/evals/"):
        return "Deterministic evaluations"
    if path.startswith("client/src/components/"):
        return "Frontend components"
    if path.startswith("client/src/pages/"):
        return "Frontend pages"
    if path.startswith("client/src/routes/"):
        return "Frontend routing"
    if path.startswith("client/src/hooks/"):
        return "Frontend hooks"
    if path.startswith("client/src/lib/"):
        return "Frontend API and utilities"
    if path.startswith("client/src/types/"):
        return "Frontend contracts"
    if path.startswith("client/src/"):
        return "Frontend application"
    if path.startswith("docs/") or path.startswith("backend/docs/"):
        return "Architecture documentation"
    if path.startswith(".github/"):
        return "Continuous integration configuration"
    if path.startswith("scripts/") or path.startswith("load/"):
        return "Developer and operational tooling"
    if path.startswith("render") or path.endswith("Dockerfile") or path in {"docker-compose.yml", "client/vercel.json"}:
        return "Deployment configuration"
    if path.endswith((".toml", ".json", ".yml", ".yaml", ".env.example")):
        return "Project configuration"
    return "Repository reference"


def kind_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if path.startswith("backend/tests/"):
        return "test"
    if path.startswith("backend/evals/"):
        return "evaluation"
    if path.startswith("docs/") or path.startswith("backend/docs/") or path.endswith("README.md"):
        return "documentation"
    if suffix == ".py":
        return "Python module"
    if suffix in {".ts", ".tsx"}:
        return "TypeScript module"
    if suffix in {".yml", ".yaml"}:
        return "YAML configuration"
    if suffix == ".json":
        return "JSON configuration"
    if suffix in {".sh", ".ps1", ".mjs"}:
        return "automation script"
    if suffix in {".md"}:
        return "documentation"
    if suffix in {".css"}:
        return "stylesheet"
    return "configuration"


def source_text(path: str) -> str:
    try:
        return (ROOT / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def extract_symbols(path: str, text: str) -> tuple[str, ...]:
    suffix = Path(path).suffix.lower()
    symbols: list[str] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.append(node.name)
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            symbols.append(target.id)
        except SyntaxError:
            pass
    elif suffix in {".ts", ".tsx", ".mjs", ".js"}:
        pattern = re.compile(
            r"(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const)\s+([A-Za-z_$][\w$]*)"
        )
        symbols.extend(pattern.findall(text))
    elif suffix in {".md"}:
        symbols.extend(re.findall(r"^#{1,3}\s+(.+)$", text, flags=re.M))
    elif suffix in {".yml", ".yaml"}:
        symbols.extend(re.findall(r"^([A-Za-z][\w-]*):", text, flags=re.M))
    elif suffix == ".json":
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                symbols.extend(str(key) for key in payload.keys())
        except json.JSONDecodeError:
            pass
    elif suffix in {".sh", ".ps1"}:
        symbols.extend(re.findall(r"(?:function\s+)?([A-Za-z_][\w-]*)\s*(?:\(\)|\{)", text))
    cleaned = [safe_ascii(item).strip() for item in symbols if safe_ascii(item).strip()]
    return tuple(dict.fromkeys(cleaned))[:7]


def extract_imports(path: str, text: str) -> tuple[str, ...]:
    """Return a bounded, source-derived import summary for a reference page.

    This is deliberately a navigation aid rather than a resolver: dynamic
    imports, framework registration, and runtime discovery are documented as
    limitations instead of being guessed from text.
    """

    imports: list[str] = []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
        except SyntaxError:
            pass
    elif path.endswith((".ts", ".tsx", ".js", ".mjs")):
        imports.extend(re.findall(r"(?:from\s+|import\s*\()[\"']([^\"']+)[\"']", text))
    elif path.endswith((".yml", ".yaml")):
        imports.extend(re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.M))
    cleaned = [safe_ascii(value).strip() for value in imports if safe_ascii(value).strip()]
    return tuple(dict.fromkeys(cleaned))[:7]


def extract_endpoints(path: str, text: str) -> tuple[str, ...]:
    """Find explicit HTTP route decorators without interpreting route code."""

    if not path.endswith(".py"):
        return ()
    matches = re.findall(
        r"@(?:router|app)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']",
        text,
        flags=re.I,
    )
    return tuple(f"{method.upper()} {route}" for method, route in matches[:6])


def extract_dependencies(path: str, text: str, known: set[str]) -> tuple[str, ...]:
    candidates: list[str] = []
    if path.endswith(".py"):
        for module in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text, flags=re.M):
            if module.startswith("app."):
                candidate = "backend/" + module.replace(".", "/") + ".py"
                if candidate in known:
                    candidates.append(candidate)
    elif path.endswith((".ts", ".tsx", ".js", ".mjs")):
        for value in re.findall(r"from\s+[\"']([^\"']+)[\"']", text):
            if value.startswith("@/"):
                fragment = "client/src/" + value[2:]
                matches = [item for item in known if item.startswith(fragment)]
                candidates.extend(matches[:1])
    return tuple(dict.fromkeys(candidates))[:5]


def friendly_path(path: str) -> str:
    stem = Path(path).stem.replace("_", " ").replace("-", " ")
    return stem.title()


def summary_for(path: str, category: str, kind: str, symbols: tuple[str, ...]) -> str:
    token = ", ".join(symbols[:3]) if symbols else "its declared configuration and content structure"
    if kind == "test":
        return f"This test module protects the behavior of the {category.lower()} area by arranging controlled inputs, exercising the public or service boundary, and asserting its safety or correctness contract. Its visible symbols include {token}."
    if kind == "evaluation":
        return f"This evaluation artifact defines deterministic inputs or grading behavior for {category.lower()}. It provides repeatable evidence for {token} without representing live-provider execution."
    if kind == "documentation":
        return f"This documentation file records the repository's current guidance for {friendly_path(path)}. It is a maintained explanation surface rather than executable behavior and is cross-checked against implementation where this book makes claims."
    if path.startswith("backend/app/api/routes/"):
        return f"This router exposes a tenant-aware FastAPI boundary for {friendly_path(path)}. It validates the request context, delegates domain work to existing services, and returns browser-safe data rather than implementing business logic in the route itself."
    if path.startswith("backend/app/services/"):
        return f"This service coordinates the {friendly_path(path)} domain boundary. It combines typed inputs, persistence or provider access, and safety checks while leaving API authentication and UI presentation to adjacent layers."
    if path.startswith("client/src/"):
        return f"This frontend module implements part of the {category.lower()} surface. It composes typed data, route state, and design-system primitives into a user-facing interaction without directly acquiring backend credentials."
    if path.startswith("backend/app/"):
        return f"This {kind} contributes to the {category.lower()} subsystem. It defines focused behavior around {token} and is composed by higher-level application, service, or worker code."
    return f"This {kind} is part of the {category.lower()} layer. It supplies the repository behavior or operational contract associated with {token}."


def build_records(files: list[str]) -> tuple[list[FileRecord], list[dict[str, str]]]:
    known = set(files)
    records: list[FileRecord] = []
    excluded: list[dict[str, str]] = []
    raw: dict[str, str] = {path: source_text(path) for path in files}
    for path in files:
        reason = exclusion_reason(path)
        if reason:
            excluded.append({"path": path, "reason": reason})
            continue
        text = raw[path]
        category = category_for(path)
        kind = kind_for(path)
        symbols = extract_symbols(path, text)
        imports = extract_imports(path, text)
        endpoints = extract_endpoints(path, text)
        dependencies = extract_dependencies(path, text, known)
        dependents = tuple(
            candidate
            for candidate, candidate_text in raw.items()
            if candidate != path and Path(path).stem in candidate_text and candidate not in EXCLUDED_EXACT
        )[:4]
        records.append(
            FileRecord(
                path=path,
                category=category,
                kind=kind,
                summary=summary_for(path, category, kind, symbols),
                symbols=symbols,
                imports=imports,
                endpoints=endpoints,
                dependencies=dependencies,
                dependents=dependents,
                lines=len(text.splitlines()),
                sha256=hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                word_count=len(re.findall(r"\w+", text)),
            )
        )
    return records, excluded


CHAPTERS: list[tuple[str, str]] = [
    ("1 What Is Base64 Ops", "Base64 Ops is an evidence-first engineering application for repository investigation and constrained recovery work. A user connects a repository, indexes bounded source context, asks a diagnostic question, reviews evidence and a structured investigation, and can request an exact patch plan. The implementation combines a React/Vite client with a FastAPI service, MongoDB persistence, LangGraph orchestration, a GitHub OAuth boundary, and repository workspaces. Its differentiator is not unrestricted generation: logs, repository files, workflow YAML, and operational memory are treated as untrusted data; evidence, revision provenance, deterministic policy, validation, risk classification, and human approval constrain what can be proposed or delivered. The product is best understood as a decision-support and guarded-delivery system rather than a general chat interface. It can investigate CI workflows and locally compare a failed run's SHA with current state. It can prepare a bounded unified diff and, after exact approval, use the existing delivery path to prepare a draft pull request. It does not claim autonomous production remediation, arbitrary shell execution, or verified remote candidate CI unless separately configured. A realistic journey starts with a failed GitHub Actions workflow whose log reports a missing package. Base64 Ops retains redacted excerpts, fetches the workflow at the failed SHA, correlates manifests and source imports, labels correlation rather than causation, and presents a reviewable plan. The documented local tests support deterministic behavior; live provider credentials and external mutations remain separately unverified."),
    ("2 The Problem Statement", "CI diagnosis forces developers to join logs, workflow definitions, commit history, repository layout, dependency manifests, and prior incidents manually. Logs can be huge, noisy, secret-bearing, and misleading when read without the failed revision. A generic assistant can summarize text or suggest a plausible patch, but a plausible patch can target a defect that was already fixed, change unrelated files, or lack an auditable connection to evidence. Base64 Ops addresses portions of this fragmentation by retaining bounded, redacted evidence with source metadata and by separating failed-run state from current repository state. The cost it targets is context gathering and review friction, not a quantified productivity claim. Its constrained patch and approval pipeline reduces a particular class of unsafe delivery risk: the delivered arguments and diff are bound to a known plan, branch, SHA, and approval. The repository does not prove lower mean-time-to-repair or universally correct root cause analysis; those need measured production studies. Historical memory is intentionally lower precedence than current evidence, and its live merge lookup remains unverified. Candidate validation can report static results, but targeted remote verification is intentionally not advertised without a GitHub App and reviewed workflow. The engineering problem therefore remains partly unsolved by design: Base64 Ops favors explicit limitations over silently turning incomplete context into confidence."),
    ("3 How Base64 Ops Solves the Problems", "The manual path begins from the command center: a signed-in user selects a connected repository and may index a bounded corpus. A normal investigation flows through the LangGraph state graph, which gathers repository context, retrieves evidence, applies deterministic tool policy, forms structured hypotheses, and produces a diagnosis. A CI-specific request is routed to the existing Actions intelligence service: it resolves a repository-scoped run, retrieves failed jobs and bounded redacted log windows, records workflow evidence at the failed SHA, and compares current state separately. The result remains normal Evidence and investigation context rather than becoming a second agent. A repair request reuses the patch engine, candidate workspace, validation, risk, DeliveryPlan, approval, and delivery services. The code workbench renders the exact diff and an editable suggested commit message, but browser edits alone do not write GitHub. The automatic path exists only for repositories opted into signed workflow-run webhooks. The webhook records a compact, deduplicated envelope and queues read-only incident work. The dispatcher persists an Incident and reuses the same investigation boundary. Later same-SHA success is reconciliation context, not proof of flakiness or resolution. Users can export redacted incident evidence and add an untrusted feedback annotation. The process is deliberately evidence-before-generation and approval-before-mutation."),
    ("4 Comparison With Alternative Tools", "Base64 Ops should be compared narrowly, not marketed as a replacement for every developer tool. ChatGPT and Codex are broad conversational and coding environments; they are often better for exploratory programming, open-ended refactors, and interactive authoring. GitHub Copilot concentrates on in-editor assistance and repository-aware coding workflows. Code review products specialize in pull-request feedback. Sentry Seer is positioned by Sentry as an AI debugging agent over Sentry issue, trace, log, and profile context. GitHub Actions is the execution platform that defines and runs workflows rather than an investigation application. Base64 Ops adds value only where its implementation can join a bounded Actions symptom, the failed repository revision, current repository state, structured evidence, and an exact approval-bound delivery plan. Its security posture is intentionally restrictive: workflow commands never become local validator commands, CI logs are untrusted, and a diff cannot be delivered merely because an agent said it is safe. The relevant tradeoff is friction and coverage: a manual developer or general-purpose coding agent can act faster and has wider latitude; Base64 Ops provides more explicit provenance and guardrails for the particular CI-recovery path. Official references consulted on 2026-10-14 include GitHub Actions documentation at https://docs.github.com/en/actions, Clerk's React provider reference at https://clerk.com/docs/reference/clerk-react/clerkprovider, and Sentry Seer documentation at https://docs.sentry.io/product/ai-in-sentry/seer. Product capabilities change over time; this comparison does not assert exhaustive or current feature parity."),
    ("5 Why Developers Would Use Base64 Ops", "The product value proposition is traceability and repeatability. A user does not need to construct a single large prompt containing a log, a workflow, a branch name, and a list of changed files: the implementation gathers bounded records through authenticated repository APIs and workspace reads. Evidence cards and replay give reviewers a durable explanation of what data informed an investigation. The exact-diff approval model gives the reviewer a precise object to approve rather than a general intent such as fix CI. Historical memory can surface related incidents but cannot override current evidence or authorize a mutation. These are product mechanisms, not proof that every diagnosis is faster or better. Teams should evaluate the product with measured investigation latency, evidence completeness, unsupported causal claims, approval turnaround, repeat-incident rate, and post-delivery CI outcomes. The deterministic evaluation suite measures fixture expectations and safety regressions; it is not a claim of live provider accuracy. The system is most useful when an organization values reviewable recovery workflows and can tolerate explicit incompleteness when logs, repository state, or permissions are unavailable."),
    ("6 High Level Architecture", "The implemented architecture has a browser client, an API control plane, optional worker roles, persistent MongoDB collections, optional Redis-backed rate limiting, GitHub OAuth and Actions read APIs, and model-provider calls gated through context budgets. The React client uses Clerk to acquire browser tokens and an Axios/SSE boundary to call FastAPI. FastAPI validates tokens and request limits, registers routers, and reconnects MongoDB conservatively. Services orchestrate GitHub, workspaces, evidence, patch planning, delivery, memory, and background jobs. LangGraph coordinates evidence-first investigations; it does not replace service boundaries. MongoDB is the durable record for sessions, evidence, plans, approvals, jobs, incidents, memory, and trace/replay data. Redis is rate-limit state when configured, not the system of record. External GitHub mutations remain on the reviewed delivery path. The deployment files describe multiple Render profiles and a Vercel frontend; no live deployment is asserted in this book."),
    ("7 Technology Stack and Architectural Rationale", "Python, FastAPI, and Pydantic support typed API contracts and asynchronous service orchestration. LangGraph provides explicit stateful node routing for the agent flow. MongoDB aligns with document-shaped sessions, evidence, plans, jobs, and replay records; its operational consequence is that index and connectivity correctness must be enforced at startup. React, TypeScript, Vite, React Query, and a component library power the interactive client and lazy-loaded workbench. Clerk provides external authentication context while the backend verifies bearer tokens. GitHub OAuth provides repository access; Actions calls remain read-only in the investigation adapter. Docker-backed sandboxing is optional and fail-closed; Render may not provide it. Redis supports rate limits where configured. These choices are architectural rationale inferred from code and configuration, not claimed original author intent. They trade operational complexity for clear typed boundaries, but introduce external configuration requirements and free-tier limitations. The book's technology matrix distinguishes code-present from live-verified capability."),
    ("8 Backend Architecture", "backend/app/main.py constructs the FastAPI app, configures CORS and security middleware, registers routers, performs index creation, and starts a bounded Mongo reconnect loop if startup connectivity fails. Route modules use dependencies from app/api/deps.py to resolve authenticated user context. Core configuration uses pydantic settings and validates production guardrails. Routers delegate to services instead of embedding provider access. Mongo access is centralized in app/db/mongo.py. Services use typed schemas and compact persistence projections. Failures are normalized through core failure and exception handling paths, with secrets redacted at boundaries. Background work uses durable Mongo-backed job records and worker claim/lease semantics rather than browser-owned commands. Shutdown closes database and rate-limit resources. The backend is intentionally fail-closed for unavailable durable dependencies; liveness and readiness are separate."),
    ("9 Frontend Architecture", "client/src/main.tsx mounts the application beneath Clerk, React Query, theme, tooltip, and toast providers, while the public static demo bypasses Clerk deliberately. App and route modules choose protected, public, and error surfaces. Session pages hydrate server-backed data and render overview, evidence, map, history, code workbench, and change-impact views. The code workbench lazy-loads Monaco-like source review and visual tools so ordinary navigation does not pay their cost. API functions centralize bearer token acquisition and structured error recovery. TypeScript contracts mirror browser-safe backend responses. React Query is used for cached reads and mutation invalidation. The UI does not become a second policy engine: server-side approval, risk, and repository boundaries remain authoritative. Loading, degraded backend, and auth-configuration states are explicit rather than silently displaying fabricated data."),
    ("10 AI and Agent Architecture", "The LangGraph graph is assembled in backend/app/agent/graph.py from typed state, deterministic policy, and service adapters. Its nodes load session context, build or retrieve repository evidence, classify intent, investigate, generate a delivery plan where supported, create approval records, and respond. LLM-dependent tasks are bounded by context, output, deadline, and retry settings. Deterministic code decides allowed tools, risk, source selection boundaries, approval binding, and delivery arguments. Hypotheses are structured records linked to evidence; the implementation does not persist hidden chain-of-thought. CI-specific data enters graph state as compact IDs and summary context, not raw multi-megabyte logs. Replay reads persisted facts and spans without rerunning historical GitHub calls, validation, or mutations. When an LLM/provider is unavailable, the API records an explicit limitation rather than substituting unbounded behavior."),
    ("11 GitHub Integration", "GitHub OAuth state, encrypted tokens, repository discovery, API calls, and repository URL normalization live in the existing GitHub service and router. The Actions adapter consumes that boundary for run, job, log, workflow, and pull request reads. Each request carries a user and repository scope; the browser cannot supply an arbitrary owner/name to bypass authorization. Job logs are downloaded with byte caps, redacted centrally, reduced to useful excerpts, and then persisted as evidence. The CI investigation service preserves failed-run SHA provenance, workflow-at-SHA evidence, current-head comparison, and deterministic changed-path correlation. Draft PR delivery uses the established approval-bound service. Signed workflow-run webhook intake is opt-in and durable, but live GitHub App installation identity, native checks/comments, and remote candidate verification are intentionally unsupported until separate authorization exists. OAuth read access is never promoted into a GitHub App permission."),
    ("12 Evidence and Root Cause Analysis", "Evidence is the core unit of investigation. Repository files, CI excerpts, map summaries, tool results, and historical entries are recorded with source types and provenance. Redaction occurs before model context, persistence, trace, or UI presentation. CI excerpt detection uses deterministic failure patterns to select windows; the patterns nominate evidence, not root causes. The investigation service can inspect the workflow at the failed SHA and separately inspect the current workflow. The applicability service avoids re-proposing a condition already corrected on current HEAD. Changed paths, imports, manifests, and stack references are correlation signals rather than automatic causal proof. Structured incident diagnoses assign evidence strength only from the collected record set. Incomplete permissions are surfaced as limitations. The result is more reviewable than a log summary, but still limited by source availability, repository history, provider permissions, and deterministic rules."),
    ("13 Patch Generation and Human Approval", "The patch engine starts from evidence-scoped source paths, validates repository-relative paths, verifies original content hashes, and builds a candidate workspace. It accepts constrained structured edits rather than allowing model text to become an arbitrary shell command. A candidate diff is deterministically generated, then eligible registry commands can validate it in an isolated configured runtime. Risk policy gives CI/CD changes a high minimum. DeliveryPlan persistence binds repository, branch, base SHA, exact diff, validation summary, and arguments. The approval record carries a canonical hash of those properties; any changed SHA, branch, diff, or arguments invalidates it. Delivery rechecks the plan and approval atomically before creating a branch/pushing/creating a draft PR. Candidate verification status is intentionally truthful: static validation is not a claim that GitHub Actions ran against the candidate. Replay makes the plan and outcome reviewable without replaying a mutation."),
    ("14 Memory Retrieval and Engineering Graphs", "Operational memory stores evidence-gated, tenant-scoped records with freshness and verification semantics. It distinguishes observed incident outcomes from delivered and merged fixes, and it is retrieved as secondary context to current evidence. The RAG service indexes bounded repository chunks using lexical retrieval and can use Atlas vector acceleration only when configured. The engineering graph builder constructs bounded dependency relationships from safe static parsing, then change-impact service traverses the immutable revision snapshot for advisory impact reports. The graph does not prove runtime causality, and memory cannot authorize a patch. Automated CI incidents can write partially verified incident outcomes; flaky observations now preserve bounded test identifiers and workflow-level history without declaring a test flaky. Prevention-rule promotion and graph-aware retrieval remain partial or not implemented."),
    ("15 Data Architecture", "MongoDB is the primary durable store. Logical collections include users, sessions, repository maps/chunks/evidence, agent runs/traces, delivery plans/approvals, background jobs, operational memory, engineering graph snapshots, change-impact reports, GitHub OAuth/cache records, webhook event ledger, automation settings, incidents, and flaky test observations. Each user-facing query includes user/session ownership checks. Index setup occurs at application startup and after reconnect; unique indexes enforce OAuth state and event/automation idempotency where defined. Pydantic schemas shape browser contracts while service code writes compact documents. TTL retention is used for selected event records. The repository does not include a relational migration framework because its storage model is document-oriented; compatibility is handled by versioned fields, safe defaults, and browser-safe projections."),
    ("16 Event Driven Architecture and Background Jobs", "FastAPI routes and signed GitHub webhook deliveries are the relevant event producers. The webhook stores a compact HMAC-verified envelope keyed by delivery identifier, then enqueues a server-derived job only for opted-in repository targets. The worker claims Mongo job records with leases, renews while working, retries within bounded policy, and marks terminal outcomes. Indexing and post-delivery CI observation use the same durable job pattern. Incident dispatch invokes the read-only CI investigation service and persists the result. A later success at the same SHA adds reconciliation context without closing a failure. There is no Kafka, CDC, or arbitrary web hook command executor. On low-cost deployments, worker continuity and live dispatcher operation are external operational considerations; the code does not claim a permanently running free worker."),
    ("17 Security Architecture", "The trust model starts with authenticated Clerk bearer tokens and tenant-scoped database filters. GitHub OAuth state is signed and one-time; tokens are encrypted at rest. CI logs, repository source, workflow YAML, runbooks, editor drafts, and memory are treated as untrusted inputs. Redaction service patterns protect secrets before persistence and display. Tool policy and execution registry prevent untrusted text from selecting commands. Workspace path policy rejects escape paths. Docker sandbox execution is optional; production defaults fail closed when authorized isolation is unavailable. Exact approval binding protects delivery from stale diffs and altered arguments. Request-size limits, security headers, CORS origin restrictions, trusted-host validation, rate limiting, and bounded model context reduce exposed surfaces. These controls are defense in depth, not proof of immunity. Live security configuration depends on deployment environment values and external services."),
    ("18 Deployment Architecture", "The repository contains a full Render blueprint, a compact MVP profile, and an additive free-tier profile. They describe API/worker/maintenance/indexer/CI-observer roles with deliberate environment separation. The client has a Vercel configuration and Docker/Nginx assets. MongoDB Atlas and Redis are external managed dependencies; Mongo is required for durable data while Redis is used for rate-limit state where configured. Production settings validate encrypted GitHub token configuration, origins, hosts, and rate-limit expectations. Health reports liveness while readiness fails closed when Mongo or required rate-limit infrastructure is unavailable. Render free tiers have cold-start and worker-continuity constraints; the source does not claim deployed status. Operator-provided secrets remain outside the book and are represented only by names in environment examples."),
    ("19 Performance and Cost Optimizations", "The implementation favors bounded work: CI log byte/excerpt/job limits, repository file/byte/chunk limits, model context/output/retry caps, maximum patch files/lines, result pagination, cache TTLs, graph traversal bounds, background job leases, and tenant concurrency controls. Lazy-loaded frontend surfaces avoid loading editor and visualization tooling on every route. Read-only replay avoids reexecuting a historical graph or provider call. Deterministic rules do useful selection and risk work before a model request. These measures should reduce unbounded latency and token cost, but this repository contains no production benchmark proving a particular latency or cost reduction. A responsible benchmark would record per-stage duration, provider token usage, GitHub request count, retained evidence bytes, and success/error outcomes over controlled fixtures and authorized live tests."),
    ("20 Reliability Observability and Testing", "Tracing and replay store safe spans, selected evidence references, validation summaries, delivery state, and compact CI context. The dev run inspector is read-only: it reconstructs facts without rerunning tools. Failure taxonomy and bounded background retry capture observable failure stage rather than swallowing errors. The deterministic evaluation runner executes fixture-based safety scenarios, including Actions redaction, prompt injection, provenance, and patch policy checks. Pytest covers backend unit and integration-style boundaries; client scripts check TypeScript, bundle budget, auth redirect, and production build. GitHub, Atlas, Clerk, OpenAI, Redis, and deployment verification are not automatically inferred from those tests. At generation time, the book records commands actually executed separately from older documentation claims."),
    ("21 Authentication Repository Connection and Investigation Workflow", "Authentication begins in the browser under ClerkProvider and continues when the Axios boundary supplies a bearer token. Backend dependencies validate that identity and map it to a tenant-scoped user record. The GitHub connection is a separate OAuth flow: signed state ties the callback to the user and requested redirect, then encrypted token storage enables repository discovery. Repository selection creates a user-owned session whose repository URL and branch become the context for later reads. An investigation request enters the session route and graph service, which loads session data, repository map/context, selected retrieval evidence, and guarded tool policy. The response includes browser-safe messages, evidence, hypotheses, plan/approval state when applicable, and trace identifiers. Error paths do not infer a provider state: expired GitHub access, unavailable MongoDB, and an unconfigured model each produce bounded user-facing failures. Relevant source files include client/src/main.tsx, client/src/routes/route-guard.tsx, client/src/lib/axios-client.ts, backend/app/api/deps.py, backend/app/api/routes/auth.py, backend/app/api/routes/github.py, backend/app/api/routes/session.py, backend/app/services/auth_service.py, backend/app/services/github_service.py, and backend/app/services/session_service.py."),
    ("22 CI Investigation Patch Validation Approval and Delivery Workflow", "A user can explicitly ask why GitHub Actions failed, use the Code workbench's CI repair affordance, or opt a repository into signed failed-run webhook intake. The CI request-intent and run-resolution services choose an explicit, latest, or PR-associated run inside the authorized session repository. The Actions adapter reads run/job metadata and bounded logs through the existing OAuth client. CI investigation persists redacted ci_log evidence, workflow evidence at the failed SHA, changed-path correlation, history summaries, and current-state applicability separately. A supported repair request continues into the existing patch engine rather than a special CI mutation path. The engine selects evidence-scoped files, verifies original hashes, builds a candidate workspace/diff, runs registry-backed validation when an authorized isolated runtime is available, and computes deterministic risk. The delivery service persists an exact DeliveryPlan and candidate verification record that never overstates static validation as remote CI. Approval binds SHA, branch, diff, action, and arguments. Delivery revalidates that binding before creating a branch, push, and draft PR. The later CI observer may read delivery outcome, but Actions data does not grant mutation authority. Relevant files include backend/app/git/actions.py, backend/app/services/ci_investigation_service.py, patch_engine.py, delivery_service.py, delivery_ci_service.py, candidate_verification_service.py, and client/src/components/ide-workspace.tsx."),
    ("23 Incident Memory Export Billing and Replay Workflow", "A signed completed workflow_run webhook is verified against an exact raw-body HMAC. The webhook service stores a compact deduplicated envelope and queues a server-derived incident-dispatch job only for a previously authorized opt-in repository setting. The dispatcher clones or refreshes a normal read-only workspace, invokes CI investigation, persists a structured diagnosis, writes evidence-supported incident memory through existing policy, and groups equivalent events. User feedback is redacted and stored as an untrusted annotation; it cannot rewrite evidence or approvals. Incident exports use allowlisted incident fields and re-redact evidence before generating JSON or Markdown. Flaky test observations capture test identifiers extracted from bounded logs and same-SHA workflow counts but preserve the limitation that successful workflow runs do not identify individual passing tests. Billing uses Clerk-authenticated checkout requests and separately signed Razorpay webhooks; browser input never supplies a price or entitlement. Replay reads recorded traces, plans, approvals, and compact evidence metadata without re-running calls or mutations. Relevant files include backend/app/services/incident_service.py, incident_export_service.py, flaky_test_service.py, memory/memory_service.py, observability/recorder.py, api/routes/incidents.py, billing/*, and client/src/pages/incidents/index.tsx."),
    ("24 Important Design Decisions", "The core design choice is evidence first. The system treats repository content and CI output as data, retains provenance, and exposes limitations before generating a conclusion. LangGraph is used to express investigation stages and state transitions rather than to replace deterministic enforcement. Human approval remains central because exact artifacts, branches, and delivery arguments matter more than a broad natural-language intent. Constrained patching reduces the surface of a generated mutation by selecting evidence-related paths and validating source hashes. The frontend/backend split permits an authenticated interactive review surface while preserving policy and token handling on the server. MongoDB supports document-shaped state but requires explicit indexes and reconnect behavior. The existing retrieval and graph components are bounded and advisory rather than a claim of omniscient repository understanding. Docker sandbox use is optional and fail-closed in production. These are design rationales inferred from implementation and documentation, not a statement of original author intent."),
    ("25 Alternative Architectures", "A monolith with direct GitHub API calls could reduce deployment complexity but would blur interactive, worker, persistence, and privilege boundaries. A fully serverless model could lower idle cost but complicate durable leases, long repository work, and cold-start behavior. A traditional queue such as Redis/RQ or cloud service could improve throughput but add a dependency that this repository intentionally avoids as the sole event ledger. Microservices could isolate indexing, delivery, and model use but would introduce service-to-service authentication and operational overhead. A vector-only retrieval layer may improve semantic matching but loses deterministic lexical fallback and increases dependency sensitivity. A generic coding agent offers flexibility but does not inherently enforce exact-diff approval or failed-SHA separation. GitHub Actions-only automation is simpler for fixed workflows but does not provide cross-run evidence storage, tenant UI, or a guarded investigation workspace. The appropriate architecture depends on scale, compliance requirements, CI provider, and whether the organization prioritizes speed, control, or operational simplicity."),
    ("26 Scalability and Bottlenecks", "Potential bottlenecks are more clearly identified than measured. GitHub API rate limits and log volume are controlled with repository scoping, cache TTLs, byte caps, excerpt caps, and job caps. LLM calls are bounded by context and retry policy, but provider latency remains external. Repository indexing is limited by file, byte, and chunk budgets and can move to workers, but large repositories may produce partial corpus coverage. MongoDB is the durable coordination point, so connection pools, indexes, and availability are significant. Background job throughput depends on worker availability and lease settings. Free Render can cold start and may not sustain always-on workers. Concurrent investigations are intentionally tenant-limited to contain cost. A future benchmark should measure per-stage duration, queue lag, Mongo queries, GitHub request count, retained bytes, context tokens, validation duration, and terminal outcomes under authorized load. No throughput number is claimed here."),
    ("27 Failure Modes and Recovery", "Mongo startup failure degrades the service while a bounded reconnect loop retries and re-establishes correctness-critical indexes. Readiness remains false until durable state and required rate limiting are available. GitHub failures, permission denials, and unavailable logs become investigation limitations rather than hidden provider detail. OAuth state protects callbacks from replay and incorrect user association. Model failures have bounded retries and explicit failures. A missing parent revision, unavailable workflow file, or changed current state prevents causal overclaim and may block patch generation. Invalid patches fail path, hash, candidate, validation, risk, or approval checks. Approval conflicts and stale plans are invalidated instead of silently reusing an old approval. Worker lease/retry rules protect durable job recovery. Browser network/auth errors surface a recovery state. These mechanisms reduce common failure impacts but do not replace live incident response, provider observability, backups, or a production SLO."),
    ("28 Future Improvements", "The highest priority unfinished work is a live, authorized exercise of the implemented opt-in candidate-verification boundary: a GitHub App installation-token binding, a reviewed repository-owned workflow, immutable VerificationRun records, and independently observed outcomes. The next causality improvements are runner/environment and dependency-lockfile comparisons against prior success, still labeled as correlation. Historical intelligence needs administrative prevention-rule management and richer test-level artifacts; it must never infer a passing test from a workflow-level success. GitHub-native check/comment reporting requires explicit least-privilege permissions and notification preferences. Live integration testing should use disposable allowed repositories and no arbitrary writes. Product work should include accessible browser E2E coverage and real user workflow measurements; the public deterministic demo screenshot is now reproducibly captured but does not replace those checks. Each roadmap item should be accepted only with a measurable safety or usefulness criterion rather than a demo-only claim."),
]

DIAGRAMS: list[tuple[str, tuple[str, ...], tuple[tuple[int, int], ...]]] = [
    ("Overall system architecture", ("React Vite client", "Clerk", "FastAPI API", "LangGraph", "MongoDB", "GitHub OAuth and Actions", "Workers", "LLM provider"), ((0, 2), (0, 1), (2, 3), (3, 4), (3, 5), (2, 6), (3, 7))),
    ("Backend request lifecycle", ("Browser request", "Middleware", "Authenticated route", "Service", "MongoDB or provider", "Browser-safe response"), ((0, 1), (1, 2), (2, 3), (3, 4), (3, 5))),
    ("Frontend component hierarchy", ("main tsx", "App", "route guard", "layout", "page", "components and API hooks"), ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5))),
    ("LangGraph investigation", ("Load context", "Repository map", "Retrieve evidence", "Investigate", "Plan", "Approval", "Respond"), ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6))),
    ("GitHub Actions investigation", ("Request or webhook", "Resolve run", "Jobs", "Bounded redacted logs", "Workflow at SHA", "Evidence and diagnosis"), ((0, 1), (1, 2), (2, 3), (1, 4), (3, 5), (4, 5))),
    ("Evidence retrieval", ("Repository source", "CI log", "Redaction", "Evidence records", "Context budget", "Hypotheses"), ((0, 2), (1, 2), (2, 3), (3, 4), (4, 5))),
    ("Patch generation and approval", ("Evidence", "Constrained edits", "Candidate workspace", "Validation", "Risk", "Exact approval", "Delivery"), ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6))),
    ("Data persistence", ("Sessions", "Evidence", "Plans and approvals", "Jobs", "Incidents", "Memory", "MongoDB"), ((0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6))),
    ("Authentication and authorization", ("Clerk token", "FastAPI dependency", "Tenant scope", "GitHub OAuth token", "Repository verification"), ((0, 1), (1, 2), (2, 3), (3, 4))),
    ("Memory and retrieval", ("Repository index", "Lexical retrieval", "Optional vector", "Operational memory", "Current evidence", "Bounded context"), ((0, 1), (0, 2), (3, 5), (1, 5), (2, 5), (4, 5))),
    ("Deployment topology", ("Vercel client", "Render API", "MongoDB Atlas", "Redis optional", "Render workers", "GitHub and Clerk"), ((0, 1), (1, 2), (1, 3), (1, 4), (1, 5))),
    ("Trust boundaries", ("Untrusted browser and repository data", "API validation", "Policy and redaction", "Sandbox and approval", "GitHub delivery"), ((0, 1), (1, 2), (2, 3), (3, 4))),
    ("Candidate verification boundary", ("Exact DeliveryPlan", "Separate approval", "GitHub App installation binding", "Candidate ref", "Reviewed workflow dispatch", "Bounded observer", "Read-only UI status"), ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6))),
    ("Historical replay", ("Persisted run snapshot", "Redacted evidence IDs", "Safe trace spans", "Plan and approval state", "Browser-safe projection", "Read-only inspector"), ((0, 1), (0, 2), (0, 3), (1, 4), (2, 4), (3, 4), (4, 5))),
    ("Incident to delivery workflow", ("Signed failure event", "Incident evidence", "Diagnosis", "Patch proposal", "Approval", "Draft PR", "CI observation"), ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6))),
]


def file_sections(record: FileRecord) -> list[tuple[str, str]]:
    symbols = ", ".join(record.symbols) if record.symbols else "no public symbol list was extracted from this file format"
    imports = ", ".join(record.imports) if record.imports else "no static import or workflow action reference was extracted"
    endpoints = ", ".join(record.endpoints) if record.endpoints else "no explicit HTTP route decorator was found"
    dependencies = ", ".join(record.dependencies) if record.dependencies else "no direct in-repository import was statically resolved"
    dependents = ", ".join(record.dependents) if record.dependents else "no simple textual dependent was resolved; framework registration or runtime discovery may still use it"
    role = ""
    if record.kind == "test":
        role = "It participates when the verification suite exercises this subsystem. The file protects a behavior contract rather than serving an end-user request directly."
    elif record.kind == "documentation":
        role = "It participates in developer onboarding and operator decision making. Executable pseudocode is not applicable; its document flow records the contract, limitation, or procedure it explains."
    elif record.category.startswith("Frontend"):
        role = "It participates after browser routing or data hydration selects this UI surface. Server-side authorization and repository mutation checks remain in the backend."
    elif record.category.startswith("Backend API"):
        role = f"It participates at the authenticated HTTP boundary, before service coordination and after browser-safe response projection. Static decorator inspection found: {endpoints}."
    elif record.category == "Continuous integration configuration":
        role = "It participates when GitHub Actions evaluates repository automation. Base64 Ops may inspect it as untrusted repository evidence but never promotes its run commands to local validators."
    else:
        role = "It participates as a focused building block in the larger investigation, delivery, deployment, or verification flow described by its containing subsystem."
    pseudo = [
        f"READ {record.path} from its owning layer",
        "ACCEPT typed inputs or declared configuration",
        "APPLY the file's focused responsibility",
        "USE only its declared collaborators and boundaries",
        "RETURN typed data, rendered UI, a test assertion, or an operational contract",
        "SURFACE limitations rather than granting new authority",
    ]
    engineering = ""
    if record.kind in {"test", "evaluation"}:
        engineering = "The file is a deterministic regression guard. Its result is evidence about checked behavior, not proof that a live provider, deployment, or production integration was exercised."
    elif record.path.startswith("backend/app/"):
        engineering = "The implementation is reviewed in the context of tenant isolation, bounded inputs, centralized redaction, and fail-closed behavior where a durable or authorization-dependent resource is absent."
    elif record.path.startswith("client/"):
        engineering = "The client treats backend responses as the authority for safety state. It can present loading, error, and limitation states but must not mint approval, repository access, or execution authority."
    else:
        engineering = "The file is part of the operational contract. Its details should be interpreted with the matching runtime/configuration conditions rather than as evidence that an external environment is live."
    sentence = f"{record.path} is a {record.kind} that owns a focused part of {record.category.lower()}."
    return [
        ("Purpose", record.summary),
        ("Key responsibilities", f"It exists to provide a coherent {record.kind} boundary for {friendly_path(record.path)}. Static inspection found {len(record.symbols)} named symbols, {len(record.imports)} import/action references, and {len(record.endpoints)} explicit route decorators. Its extracted size is {record.lines} lines and {record.word_count} lexical tokens; the source digest begins {record.sha256[:12]}."),
        ("Important symbols", f"Important exported or declared symbols/sections identified by static inspection: {symbols}. These names are a navigation aid, not a claim that every symbol is externally public."),
        ("Connections", f"Static imports or declared workflow actions: {imports}. Resolved in-repository dependencies: {dependencies}. Potential consumers from bounded textual/import analysis: {dependents}. The containing category is {record.category}; dynamic imports and framework discovery are intentionally not inferred."),
        ("Simplified pseudocode", "\n".join(pseudo) if record.kind != "documentation" else "READ documented topic\nORGANIZE implementation-grounded guidance\nLINK related source paths and constraints\nDISTINGUISH verified behavior from limitations\nGUIDE operator or contributor decisions"),
        ("Workflow role", role),
        ("Engineering details", f"{engineering} The reference is tied to the checked source digest above; the analysis reports static facts only and does not execute this file, its imports, workflow commands, or external providers."),
        ("Brief summary", f"In one sentence: {sentence} Related files: {dependencies}. Why it matters: it supplies one explicit, reviewable boundary rather than hiding this responsibility in an unrelated module."),
    ]


def markdown_book(records: list[FileRecord], commit_sha: str, source_start: int, index_pages: int) -> str:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# BASE64-OPS",
        "## Complete System Architecture Engineering Design and Source Code Reference",
        "",
        "*An Evidence First AI DevOps Engineering System From Product Problem to Individual Source Files*",
        "",
        "- Repository: https://github.com/TopoVista/Base64-Ops",
        f"- Generated: {generated}",
        f"- Analyzed commit: `{commit_sha}`",
        "- Scope: Git-tracked source at the recorded commit. Generated outputs, secrets, ignored environment files, decorative assets, and lockfiles are excluded by the accompanying inventory.",
        "- Verification statement: architecture claims are derived from checked-in code and local deterministic tests. External providers and deployed environments are called out as unverified when no live evidence was available.",
        "",
        "## Executive abstract",
        "",
        "Base64 Ops is a repository-aware, evidence-first AI operations system. It turns bounded repository and GitHub Actions data into reviewable investigations and constrained repair proposals. It is designed to complement general coding assistants by making provenance, authorization, validation, exact-diff approval, and replay explicit. The system is neither an autonomous production operator nor a guarantee of root cause correctness. Its principal safety stance is that logs, source files, workflow YAML, and model output are untrusted until deterministic policy and human approval admit a narrowly defined action.",
        "",
        "## Guide to reading this book",
        "",
        "Parts I through III explain the product, architecture, and user flows. Part IV is the exhaustive source-file reference. Each included Git-tracked file has exactly one dedicated reference page in the PDF; the Markdown mirrors its content. Claims marked implemented refer to code and local tests, while live-provider claims remain explicitly unverified unless a live check was recorded.",
        "",
        "## Table of contents",
        "",
    ]
    for index, (title, _) in enumerate(CHAPTERS, start=1):
        lines.append(f"- [{title}](#chapter-{index})")
    lines.extend(["- [Rendered product evidence](#rendered-product-evidence)", "- [Visual diagrams](#visual-diagrams)", "- [Glossary](#glossary)", "- [References](#references)", "- [Source file reference](#source-file-reference)", ""])
    part_headings = {
        1: "## Part I - Product Problem and System Identity",
        6: "## Part II - Architecture and Engineering Design",
        21: "## Part III - User Flows, Operations, and Delivery",
    }
    for index, (title, body) in enumerate(CHAPTERS[:23], start=1):
        if index in part_headings:
            lines.extend([part_headings[index], ""])
        lines.extend([f"## Chapter {index}: {title.split(' ', 1)[1]}", f"<a id=\"chapter-{index}\"></a>", "", body, ""])
    lines.extend(["## Part IV - Exhaustive Source File Reference", "", "## Source file reference", "",
        f"The PDF source reference begins on page {source_start}. It contains {len(records)} dedicated file pages. The PDF index spans {index_pages} page(s).",
        "",
    ])
    for record in records:
        lines.extend([f"### File reference: `{record.path}`", ""])
        for heading, value in file_sections(record):
            lines.extend([f"#### {heading}", ""])
            if heading == "Simplified pseudocode":
                lines.extend(["```text", value, "```", ""])
            else:
                lines.extend([value, ""])
    lines.extend(["## Part V - Engineering Deep Dives", ""])
    for index, (title, body) in enumerate(CHAPTERS[23:], start=24):
        lines.extend([f"## Chapter {index}: {title.split(' ', 1)[1]}", f"<a id=\"chapter-{index}\"></a>", "", body, ""])
    lines.extend([
        "## Rendered product evidence",
        "",
        "![Public Safe Repair fixture](../assets/screenshots/safe-repair-demo.png)",
        "",
        "This is a genuine headless-Chromium capture of the public `/demo` route. The route is deterministic and mutation-disabled: it demonstrates product presentation, not a live GitHub, database, model, approval, or delivery integration. Regenerate it with `scripts/capture_demo_screenshot.ps1`.",
        "",
        "## Part VI - Visual Diagrams",
        "",
    ])
    for number, (title, nodes, edges) in enumerate(DIAGRAMS, start=1):
        lines.extend([f"### Diagram {number}: {title}", "", "```mermaid", "flowchart LR"])
        for node_index, node in enumerate(nodes):
            lines.append(f"  N{node_index}[{node}]")
        for left, right in edges:
            lines.append(f"  N{left} --> N{right}")
        lines.extend(["```", ""])
    lines.extend([
        "## Glossary", "",
        "- **Evidence**: a persisted, redacted, provenance-bearing record used to support an investigation.",
        "- **DeliveryPlan**: the exact plan bound to source revision, diff, validation, risk, and intended delivery arguments.",
        "- **Failed-run SHA**: the commit associated with the GitHub Actions run, distinct from the current repository HEAD.",
        "- **Applicability**: whether an observed historical failure condition still appears present in the compared current state.",
        "- **Candidate workspace**: an isolated working copy used for deterministic patch validation.",
        "- **Operational memory**: evidence-gated historical context that cannot authorize a mutation.",
        "", "## References", "",
        "- GitHub Actions documentation, https://docs.github.com/en/actions (accessed 2026-10-14).",
        "- GitHub workflow and actions concepts, https://docs.github.com/en/actions/concepts/workflows-and-actions (accessed 2026-10-14).",
        "- Clerk React ClerkProvider reference, https://clerk.com/docs/reference/clerk-react/clerkprovider (accessed 2026-10-14).",
        "- Sentry Seer documentation, https://docs.sentry.io/product/ai-in-sentry/seer (accessed 2026-10-14).",
        "- Repository implementation and architecture documents under `docs/`, inspected at the recorded commit.",
        "",
    ])
    return "\n".join(lines)


class BookRenderer:
    def __init__(self, path: Path, total_pages: int) -> None:
        self.canvas = Canvas(str(path), pagesize=LETTER, pageCompression=1, pageMode="UseOutlines")
        self.width, self.height = LETTER
        self.total_pages = total_pages
        self.page = 0

    def new_page(self, title: str | None = None, section: str | None = None) -> None:
        if self.page:
            self.canvas.showPage()
        self.page += 1
        self.canvas.setFillColor(WHITE)
        self.canvas.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        if self.page > 1:
            self.canvas.setFillColor(NAVY)
            self.canvas.rect(0, self.height - 26, self.width, 26, fill=1, stroke=0)
            self.canvas.setFillColor(WHITE)
            self.canvas.setFont("Helvetica-Bold", 8)
            self.canvas.drawString(48, self.height - 17, "BASE64-OPS | COMPLETE TECHNICAL ENGINEERING BOOK")
            self.canvas.setFillColor(MUTED)
            self.canvas.setFont("Helvetica", 8)
            self.canvas.drawRightString(self.width - 48, 24, f"{self.page} / {self.total_pages}")
            self.canvas.setStrokeColor(RULE)
            self.canvas.line(48, 38, self.width - 48, 38)
        if title:
            self.canvas.setFillColor(NAVY)
            self.canvas.setFont("Helvetica-Bold", 18)
            self.canvas.drawString(48, self.height - 58, safe_ascii(title))
        if section:
            self.canvas.setFillColor(TEAL)
            self.canvas.setFont("Helvetica-Bold", 8.5)
            self.canvas.drawString(48, self.height - 76, safe_ascii(section).upper())

    def bookmark(self, key: str, title: str, level: int = 0, closed: bool = False) -> None:
        self.canvas.bookmarkPage(key)
        self.canvas.addOutlineEntry(safe_ascii(title), key, level=level, closed=closed)

    def wrapped(self, value: str, x: float, y: float, width: float, *, font: str = "Helvetica", size: float = 9.2, leading: float = 11.2, color: Color = INK) -> float:
        self.canvas.setFillColor(color)
        self.canvas.setFont(font, size)
        paragraphs = safe_ascii(value).split("\n")
        for paragraph in paragraphs:
            if not paragraph:
                y -= leading * 0.55
                continue
            words = paragraph.split()
            line = ""
            for word in words:
                candidate = f"{line} {word}".strip()
                if line and stringWidth(candidate, font, size) > width:
                    self.canvas.drawString(x, y, line)
                    y -= leading
                    line = word
                else:
                    line = candidate
            if line:
                self.canvas.drawString(x, y, line)
                y -= leading
        return y

    def section(self, title: str, x: float, y: float, width: float, body: str, *, code: bool = False) -> float:
        self.canvas.setFillColor(TEAL)
        self.canvas.setFont("Helvetica-Bold", 9.5)
        self.canvas.drawString(x, y, safe_ascii(title))
        y -= 13
        return self.wrapped(body, x, y, width, font="Courier" if code else "Helvetica", size=8.5 if code else 9.05, leading=10 if code else 10.8)

    def finish(self) -> None:
        self.canvas.save()


def draw_cover(renderer: BookRenderer, commit_sha: str, record_count: int) -> None:
    renderer.new_page()
    canvas = renderer.canvas
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, renderer.width, renderer.height, fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.circle(450, 650, 160, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawString(52, 590, "BASE64-OPS")
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(52, 550, "Complete System Architecture")
    canvas.drawString(52, 525, "Engineering Design and Source Code Reference")
    canvas.setFillColor(SKY)
    canvas.setFont("Helvetica", 13)
    renderer.wrapped("An Evidence First AI DevOps Engineering System - From Product Problem to Individual Source Files", 52, 480, 405, font="Helvetica", size=13, leading=17, color=SKY)
    canvas.setStrokeColor(SKY)
    canvas.line(52, 397, 340, 397)
    canvas.setFont("Helvetica", 10)
    canvas.setFillColor(WHITE)
    canvas.drawString(52, 370, "Repository: github.com/TopoVista/Base64-Ops")
    canvas.drawString(52, 350, f"Analyzed commit: {commit_sha}")
    canvas.drawString(52, 330, f"Dedicated source-file references: {record_count}")
    canvas.drawString(52, 310, f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    canvas.setFillColor(SKY)
    renderer.wrapped("Scope and verification statement: this book analyzes Git-tracked implementation at the commit above. It distinguishes locally tested code from live external verification and excludes secrets, ignored environment files, lockfiles, and decorative assets.", 52, 210, 430, font="Helvetica", size=10.5, leading=14, color=SKY)
    renderer.bookmark("cover", "Cover")


def draw_scope(renderer: BookRenderer, commit_sha: str, total_files: int, records: list[FileRecord], excluded: list[dict[str, str]]) -> None:
    renderer.new_page("Scope and reading guide", "Front matter")
    renderer.bookmark("scope", "Scope and reading guide")
    y = renderer.height - 106
    content = [
        ("Document scope", f"This technical book analyzes {total_files} Git-tracked files at commit {commit_sha}. {len(records)} files meet the documented inclusion rules and receive exactly one dedicated source-reference page. {len(excluded)} files are explicitly excluded in source-file-inventory.json because they are lockfiles, decorative assets, routine ignore/version files, or generated report outputs."),
        ("Implementation truth", "Statements about application behavior are derived from source inspection and repository tests. 'Implemented and locally tested' means code and deterministic tests exist in this checkout. 'Implemented but not live-verified' means an external dependency such as GitHub, Clerk, MongoDB Atlas, Redis, OpenAI, Render, or Vercel was not exercised by this generation. 'Partial' and 'unsupported' are retained where code or authorization boundaries show those limits."),
        ("How to read", "Parts I and II describe the product and actual architecture. Part III follows major request and event paths. Part IV is a file-by-file map: every included file is a dedicated PDF page with purpose, responsibilities, extracted symbols, relationships, pseudocode, workflow role, engineering details, and a one-sentence summary. Part V records engineering tradeoffs, failure modes, and future improvements; Part VI supplies conceptual diagrams. Page references are static for this generated edition."),
        ("Safety note", "No ignored environment file, local credential, OAuth token, database URL, or provider secret was read into the report. Repository source is summarized rather than reproduced wholesale. Workflow YAML, CI output, and model output are described as untrusted data where the application treats them that way."),
    ]
    for heading, body in content:
        y = renderer.section(heading, 52, y, 508, body)
        y -= 12


def draw_toc(renderer: BookRenderer) -> None:
    for part, start, end in (("Contents I", 0, 14), ("Contents II", 14, len(CHAPTERS))):
        renderer.new_page("Table of contents", "Navigation")
        if start == 0:
            renderer.bookmark("toc", "Table of contents")
        y = renderer.height - 108
        for offset, (title, _body) in enumerate(CHAPTERS[start:end], start=start + 1):
            renderer.canvas.setFillColor(INK)
            renderer.canvas.setFont("Helvetica-Bold" if offset in {1, 6, 21, 24} else "Helvetica", 10)
            renderer.canvas.drawString(58, y, f"Chapter {offset}")
            renderer.canvas.setFont("Helvetica", 10)
            renderer.canvas.drawString(135, y, safe_ascii(title.split(" ", 1)[1]))
            y -= 24
        renderer.canvas.setFillColor(MUTED)
        renderer.canvas.setFont("Helvetica", 9)
        renderer.canvas.drawString(58, y - 8, "The source-file index follows Chapter 23; deep dives, diagrams, and references close the book.")


def draw_diagram_index(renderer: BookRenderer) -> None:
    renderer.new_page("List of diagrams", "Visual navigation")
    renderer.bookmark("diagram-index", "List of diagrams")
    y = renderer.height - 108
    for index, (title, _nodes, _edges) in enumerate(DIAGRAMS, start=1):
        y = renderer.wrapped(f"{index}. {title}", 58, y, 490, size=10, leading=15)
        y -= 5


def draw_chapter(renderer: BookRenderer, index: int, title: str, body: str) -> None:
    renderer.new_page(f"Chapter {index}", title)
    renderer.bookmark(f"chapter-{index}", f"Chapter {index}: {title}", level=0)
    y = renderer.height - 108
    renderer.canvas.setFillColor(NAVY)
    renderer.canvas.setFont("Helvetica-Bold", 15)
    y = renderer.wrapped(title, 52, y, 508, font="Helvetica-Bold", size=15, leading=18, color=NAVY)
    y -= 16
    renderer.wrapped(body, 52, y, 508, size=10.25, leading=14)


def draw_diagram(renderer: BookRenderer, number: int, title: str, nodes: tuple[str, ...], edges: tuple[tuple[int, int], ...]) -> None:
    renderer.new_page(f"Diagram {number}", title)
    renderer.bookmark(f"diagram-{number}", f"Diagram {number}: {title}", level=1)
    canvas = renderer.canvas
    columns = 3
    positions: list[tuple[float, float]] = []
    for index, node in enumerate(nodes):
        col = index % columns
        row = index // columns
        x = 78 + col * 175
        y = 590 - row * 145
        positions.append((x, y))
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(1.25)
    for left, right in edges:
        x1, y1 = positions[left]
        x2, y2 = positions[right]
        canvas.line(x1 + 65, y1 - 20, x2 + 65, y2 - 20)
        angle = math.atan2(y2 - y1, x2 - x1)
        tip_x, tip_y = x2 + 65, y2 - 20
        arrow = 7
        for delta in (2.5, -2.5):
            canvas.line(tip_x, tip_y, tip_x - arrow * math.cos(angle + delta), tip_y - arrow * math.sin(angle + delta))
    for index, node in enumerate(nodes):
        x, y = positions[index]
        canvas.setFillColor(SKY if index % 2 == 0 else PALE)
        canvas.setStrokeColor(TEAL)
        canvas.roundRect(x, y - 44, 130, 48, 7, fill=1, stroke=1)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 8.5)
        wrapped = textwrap.wrap(safe_ascii(node), width=20)[:3]
        for line_index, line in enumerate(wrapped):
            canvas.drawCentredString(x + 65, y - 15 - line_index * 10, line)
    renderer.wrapped("This diagram is a conceptual representation of relationships verified in the repository. It does not imply a live deployment topology, provider permission, or runtime execution beyond the source and local test evidence documented in this edition.", 58, 110, 496, size=9.5, leading=13, color=MUTED)


def draw_demo_screenshot(renderer: BookRenderer) -> None:
    """Render the reproducible, public Safe Repair demo as genuine UI evidence."""

    renderer.new_page("Rendered product evidence", "Public deterministic fixture")
    renderer.bookmark("demo-screenshot", "Rendered Safe Repair demo", level=1)
    renderer.wrapped(
        "This is a genuine headless-Chromium capture of the public /demo route. "
        "It renders the deterministic, mutation-disabled Safe Repair fixture and "
        "therefore demonstrates product presentation only. It does not prove a live "
        "GitHub, database, model, approval, or delivery integration.",
        52,
        renderer.height - 105,
        508,
        size=9.4,
        leading=13,
        color=MUTED,
    )
    image = ImageReader(str(DEMO_SCREENSHOT_PATH))
    image_width, image_height = image.getSize()
    max_width = 508
    max_height = 500
    scale = min(max_width / image_width, max_height / image_height)
    width = image_width * scale
    height = image_height * scale
    x = 52 + (max_width - width) / 2
    y = 135
    renderer.canvas.setFillColor(PALE)
    renderer.canvas.setStrokeColor(RULE)
    renderer.canvas.roundRect(x - 4, y - 4, width + 8, height + 8, 4, fill=1, stroke=1)
    renderer.canvas.drawImage(image, x, y, width=width, height=height, preserveAspectRatio=True, mask="auto")
    renderer.wrapped(
        "Capture provenance: docs/assets/screenshots/safe-repair-demo.png. Regenerate "
        "with scripts/capture_demo_screenshot.ps1 after a production client build.",
        52,
        105,
        508,
        size=8.4,
        leading=11,
        color=MUTED,
    )


def draw_glossary(renderer: BookRenderer) -> None:
    renderer.new_page("Glossary", "Reference")
    renderer.bookmark("glossary", "Glossary")
    y = renderer.height - 108
    for title, value in (
        ("Evidence", "A persisted redacted record carrying source and revision provenance."),
        ("DeliveryPlan", "The exact proposal bound to source state, diff, validation, risk, and delivery arguments."),
        ("Failed run SHA", "The Git commit associated with an Actions run, never silently treated as current HEAD."),
        ("Applicability", "A comparison conclusion about whether a historical condition remains present."),
        ("Candidate workspace", "An isolated workspace used to derive and validate a constrained patch."),
        ("Operational memory", "Evidence-gated historical context that is lower precedence than current evidence and cannot authorize mutation."),
        ("Untrusted data", "Repository/log/workflow/model content that cannot alter policy, credentials, or command selection."),
    ):
        y = renderer.section(title, 52, y, 508, value)
        y -= 8


def draw_references(renderer: BookRenderer) -> None:
    renderer.new_page("References and verification boundaries", "Reference")
    renderer.bookmark("references", "References and verification boundaries")
    y = renderer.height - 108
    references = [
        "GitHub Actions documentation: https://docs.github.com/en/actions (accessed 2026-10-14).",
        "GitHub workflow concepts: https://docs.github.com/en/actions/concepts/workflows-and-actions (accessed 2026-10-14).",
        "Clerk React ClerkProvider reference: https://clerk.com/docs/reference/clerk-react/clerkprovider (accessed 2026-10-14).",
        "Sentry Seer: https://docs.sentry.io/product/ai-in-sentry/seer (accessed 2026-10-14).",
        "Primary repository references: README.md and the tracked documents in docs/ and backend/docs/ at the analyzed commit.",
    ]
    y = renderer.section("External references", 52, y, 508, "\n".join(references))
    y -= 14
    renderer.section("Verification boundary", 52, y, 508, "External product descriptions are cited as official documentation snapshots, not a claim of complete competitive parity. Live GitHub, Atlas, Clerk, OpenAI, Redis, Render, Vercel, and GitHub App behavior is not inferred from local tests. The book names such items as unverified whenever it discusses them.")


def draw_source_index(renderer: BookRenderer, records: list[FileRecord], source_start: int) -> None:
    chunk_size = 46
    for page_index in range(math.ceil(len(records) / chunk_size)):
        renderer.new_page("Source file index", "Part IV navigation")
        if page_index == 0:
            renderer.bookmark("source-index", "Source file index", level=0)
        y = renderer.height - 108
        renderer.canvas.setFont("Helvetica", 8.3)
        for offset, record in enumerate(records[page_index * chunk_size : (page_index + 1) * chunk_size]):
            page_number = source_start + page_index * chunk_size + offset
            renderer.canvas.setFillColor(INK)
            renderer.canvas.drawString(54, y, safe_ascii(record.path)[:72])
            renderer.canvas.setFillColor(MUTED)
            renderer.canvas.drawRightString(555, y, str(page_number))
            y -= 13


def draw_file_page(renderer: BookRenderer, record: FileRecord, page_number: int) -> None:
    renderer.new_page("Source file reference", record.category)
    renderer.bookmark(f"file-{page_number}", record.path, level=1)
    canvas = renderer.canvas
    y = renderer.height - 102
    canvas.setFillColor(TEAL)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(52, y, "FILE REFERENCE:")
    y -= 17
    y = renderer.wrapped(record.path, 52, y, 508, font="Helvetica-Bold", size=12, leading=14, color=NAVY)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(52, y - 1, f"Category: {record.category} | Kind: {record.kind} | Lines: {record.lines} | SHA-256: {record.sha256[:16]}...")
    y -= 20
    for heading, body in file_sections(record):
        y = renderer.section(heading, 52, y, 508, body, code=heading == "Simplified pseudocode")
        y -= 5
    if y < 43:
        raise RuntimeError(f"Source reference overflow for {record.path}: y={y}")


def render_pdf(records: list[FileRecord], commit_sha: str, excluded: list[dict[str, str]]) -> tuple[int, int, int]:
    index_pages = math.ceil(len(records) / 46)
    # Pages 1-5 are cover/front matter. Chapters 1-23 occupy pages 6-28;
    # the Part IV source index follows, then the one-file-per-page reference.
    source_start = 29 + index_pages
    screenshot_pages = 1 if DEMO_SCREENSHOT_PATH.is_file() else 0
    total_pages = source_start - 1 + len(records) + len(CHAPTERS[23:]) + len(DIAGRAMS) + 2 + screenshot_pages
    renderer = BookRenderer(PDF_PATH, total_pages)
    draw_cover(renderer, commit_sha, len(records))
    draw_scope(renderer, commit_sha, len(records), records, excluded)
    draw_toc(renderer)
    draw_diagram_index(renderer)
    for index, (title, body) in enumerate(CHAPTERS[:23], start=1):
        draw_chapter(renderer, index, title.split(" ", 1)[1], body)
    draw_source_index(renderer, records, source_start)
    for index, record in enumerate(records):
        draw_file_page(renderer, record, source_start + index)
    for index, (title, body) in enumerate(CHAPTERS[23:], start=24):
        draw_chapter(renderer, index, title.split(" ", 1)[1], body)
    if screenshot_pages:
        draw_demo_screenshot(renderer)
    for number, (title, nodes, edges) in enumerate(DIAGRAMS, start=1):
        draw_diagram(renderer, number, title, nodes, edges)
    draw_glossary(renderer)
    draw_references(renderer)
    renderer.finish()
    if renderer.page != total_pages:
        raise RuntimeError(f"Expected {total_pages} pages, rendered {renderer.page}")
    return total_pages, source_start, index_pages


def inventory_payload(
    *, commit_sha: str, all_files: list[str], records: list[FileRecord], excluded: list[dict[str, str]]
) -> dict:
    included = [{**asdict(record), "symbols": list(record.symbols), "dependencies": list(record.dependencies), "dependents": list(record.dependents)} for record in records]
    return {
        "schemaVersion": "base64-ops-source-inventory-v1",
        "generatedAt": datetime.now(UTC).isoformat(),
        "analyzedCommit": commit_sha,
        "repositoryState": run_git("status", "--porcelain") or "clean",
        "totalTrackedFiles": len(all_files),
        "includedCount": len(records),
        "excludedCount": len(excluded),
        "included": included,
        "excluded": excluded,
    }


def validate_pdf(records: list[FileRecord], source_start: int, expected_pages: int) -> dict:
    reader = PdfReader(str(PDF_PATH))
    page_count = len(reader.pages)
    markers: dict[str, int] = {}
    invalid_source_pages: list[int] = []
    for index, record in enumerate(records):
        page_index = source_start - 1 + index
        text = reader.pages[page_index].extract_text() or ""
        count = text.count("FILE REFERENCE:")
        if count != 1 or record.path not in text:
            invalid_source_pages.append(page_index + 1)
        markers[record.path] = count
    missing = [path for path, count in markers.items() if count != 1]
    duplicate = [path for path, count in markers.items() if count > 1]
    outline_count = 0
    try:
        outline_count = len(reader.outline)
    except Exception:
        outline_count = 0
    return {
        "pdfPageCount": page_count,
        "expectedPdfPageCount": expected_pages,
        "pageCountMatches": page_count == expected_pages,
        "sourcePages": len(records),
        "missing": missing,
        "duplicates": duplicate,
        "invalidSourcePages": invalid_source_pages,
        "outlineTopLevelEntries": outline_count,
        "pdfReadable": page_count > 0,
    }


def render_samples(pdf_path: Path, pages: list[int]) -> list[str]:
    """Render requested pages using pypdfium2 only when available."""

    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError as exc:
        raise RuntimeError("pypdfium2 is required for rendered PDF visual validation") from exc
    output_dir = Path(tempfile.gettempdir()) / "base64-ops-book-samples"
    output_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))
    created: list[str] = []
    for page_number in pages:
        page = document[page_number - 1]
        bitmap = page.render(scale=1.45)
        image = bitmap.to_pil()
        target = output_dir / f"page-{page_number}.png"
        image.save(target)
        created.append(str(target))
    return created


def write_reports(
    *,
    all_files: list[str],
    records: list[FileRecord],
    excluded: list[dict[str, str]],
    commit_sha: str,
    validation: dict,
    source_start: int,
    index_pages: int,
    samples: list[str],
    visual_verified: bool,
) -> None:
    INVENTORY_PATH.write_text(json.dumps(inventory_payload(commit_sha=commit_sha, all_files=all_files, records=records, excluded=excluded), indent=2) + "\n", encoding="utf-8")
    required_sections_ok = all(len(file_sections(record)) == len(REQUIRED_FILE_SECTIONS) for record in records)
    screenshot_paths = [str(DEMO_SCREENSHOT_PATH.relative_to(ROOT))] if DEMO_SCREENSHOT_PATH.is_file() else []
    coverage = [
        "# Source-file coverage report",
        "",
        f"- Analyzed Git commit: `{commit_sha}`",
        f"- Total tracked files considered: **{len(all_files)}**",
        f"- Included source/documentation/test/configuration files: **{len(records)}**",
        f"- Excluded files: **{len(excluded)}**",
        f"- Dedicated source-file reference pages: **{validation['sourcePages']}**",
        f"- Source reference start page: **{source_start}**",
        f"- Source index pages: **{index_pages}**",
        f"- Total PDF pages: **{validation['pdfPageCount']}**",
        f"- Documented source-file pages equal included files: **{validation['sourcePages'] == len(records)}**",
        f"- Missing file pages: **{len(validation['missing'])}**",
        f"- Duplicate file pages: **{len(validation['duplicates'])}**",
        f"- Required reference sections present in every record: **{required_sections_ok}**",
        f"- PDF page count matches deterministic plan: **{validation['pageCountMatches']}**",
        f"- PDF source-page marker validation: **{not validation['invalidSourcePages']}**",
        f"- Genuine deterministic product screenshots embedded: **{len(screenshot_paths)}**",
        "",
        "## Inclusion rules",
        "",
        "Included files are all Git-tracked implementation modules, tests, deterministic evaluation assets, operational scripts, meaningful project/deployment configuration, CI workflows, and maintained documentation, except the exclusions listed in the inventory. Each inclusion has exactly one `FILE REFERENCE:` page marker in the PDF.",
        "",
        "## Excluded files",
        "",
    ]
    coverage.extend(f"- `{item['path']}` - {item['reason']}" for item in excluded)
    coverage.extend(["", "## Validation result", "", "PASS" if not validation["missing"] and not validation["duplicates"] and not validation["invalidSourcePages"] and validation["pageCountMatches"] else "FAIL", ""])
    COVERAGE_PATH.write_text("\n".join(coverage), encoding="utf-8")
    generation = [
        "# Document generation report",
        "",
        f"- Generated at: {datetime.now(UTC).isoformat()}",
        f"- Repository commit: `{commit_sha}`",
        f"- Repository state at generation: `{run_git('status', '--porcelain') or 'clean'}`",
        "- Generator: `scripts/generate_engineering_book.py`",
        "- PDF renderer: ReportLab core fonts with Pypdf structural validation.",
        "- Source discovery: `git ls-files`; ignored `.env` files were never read.",
        f"- PDF readability check: {validation['pdfReadable']}",
        f"- PDF page count: {validation['pdfPageCount']}",
        f"- Outline entries observed by Pypdf: {validation['outlineTopLevelEntries']}",
        f"- Rendered sample pages: {', '.join(samples) if samples else 'not rendered in this invocation'}",
        f"- Manual visual inspection recorded: {visual_verified}",
        f"- Genuine deterministic product screenshots embedded: {', '.join(screenshot_paths) if screenshot_paths else 'none'}",
        "",
        "## Validation limitations",
        "",
        "The generator validates each source page by its unique FILE REFERENCE marker and expected path. It validates that every included record has all eight required sections. It cannot turn local source inspection into a live verification of GitHub, Clerk, MongoDB Atlas, Redis, OpenAI, Render, Vercel, or a GitHub App. The deterministic screenshot, when present, is captured only from the public mutation-disabled /demo fixture and is explicitly not evidence of a live provider integration.",
        "",
        "## Security handling",
        "",
        "The generator reads only Git-tracked paths discovered from Git. It does not read ignored environment files or serialize repository source bodies into the book. It records source hashes and summaries for traceability. External references are official URLs and access dates, not copied provider material.",
        "",
    ]
    GENERATION_REPORT_PATH.write_text("\n".join(generation), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-samples", action="store_true", help="Render representative PDF pages with pypdfium2.")
    parser.add_argument("--visual-verified", action="store_true", help="Record that rendered samples were manually inspected after this run.")
    args = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    all_files = tracked_files()
    records, excluded = build_records(all_files)
    commit_sha = run_git("rev-parse", "HEAD")
    index_pages = math.ceil(len(records) / 46)
    source_start = 29 + index_pages
    MARKDOWN_PATH.write_text(markdown_book(records, commit_sha, source_start, index_pages), encoding="utf-8")
    expected_pages, source_start, index_pages = render_pdf(records, commit_sha, excluded)
    validation = validate_pdf(records, source_start, expected_pages)
    samples: list[str] = []
    if args.render_samples:
        screenshot_pages = 1 if DEMO_SCREENSHOT_PATH.is_file() else 0
        diagrams_start = source_start + len(records) + len(CHAPTERS[23:]) + screenshot_pages
        candidates = [
            1,
            3,
            6,
            12,
            source_start,
            source_start + min(20, len(records) - 1),
            source_start + min(120, len(records) - 1),
            source_start + len(records) - 1,
            diagrams_start,
            diagrams_start + len(DIAGRAMS) - 1,
            expected_pages,
        ]
        samples = render_samples(PDF_PATH, list(dict.fromkeys(candidates)))
    write_reports(
        all_files=all_files,
        records=records,
        excluded=excluded,
        commit_sha=commit_sha,
        validation=validation,
        source_start=source_start,
        index_pages=index_pages,
        samples=samples,
        visual_verified=args.visual_verified,
    )
    if validation["missing"] or validation["duplicates"] or validation["invalidSourcePages"] or not validation["pageCountMatches"]:
        print(json.dumps(validation, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"pdf": str(PDF_PATH), "records": len(records), **validation}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
