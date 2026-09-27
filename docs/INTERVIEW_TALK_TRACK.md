# Interview talk track

## What problem does Base64 Ops solve?

It makes repository and CI investigation reviewable. Instead of immediately generating a patch from a prompt or log, it builds evidence, records provenance, separates historical from current state, and gates delivery.

## Why LangGraph?

The graph provides explicit lifecycle stages and resumable approval interruption. It is used for orchestration, not as permission policy; deterministic policy and delivery services remain authoritative.

## Why not let an LLM run shell commands?

Model output is untrusted. Validators come from a fixed command registry and execution policy, with candidate workspaces and sandbox controls. Repository and workflow commands cannot register new commands.

## How does approval binding work?

Approval is attached to the exact DeliveryPlan: repository, base SHA, diff hash, canonical arguments, and action. Changed HEAD, diff, or arguments invalidate it before execution.

## How are stale CI failures handled?

Base64 retrieves the failed-run workflow at its SHA and compares the relevant condition with current state. `HISTORICAL_FIXED` explains the incident but blocks duplicate remediation; unknown current state also fails closed.

## How does memory avoid becoming truth?

Memory has verification and freshness metadata. It is secondary context; current evidence outranks it, and delivered fixes are distinct from merged fixes.

## Why deterministic evals?

Safety invariants—redaction, approval requirements, path safety, prompt injection resistance, and historical/current separation—should not depend on an LLM judge.

## What would you build next?

Exercise read-only live Actions against an authorized fixture repository, then improve calibrated diagnosis quality and production worker observability without adding mutation authority.
