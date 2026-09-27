# Agent Safety

Base64 Ops treats repository files, CI output, logs, runbooks, screenshots, and external adapter responses as untrusted evidence. They never redefine policy, approve an action, or grant access to credentials.

## Core Safety Boundaries

- Read-only actions use an allowlisted toolbox.
- Mutating requests can proceed only through a persisted exact delivery plan. Approval binds its diff hash, base SHA, repository, action, and canonical arguments before workspace branch creation or tool execution.
- Evidence is redacted before persistence and browser delivery.
- Evidence, sessions, approvals, maps, and GitHub credentials are queried under the authenticated user scope.
- Direct PR creation is rejected. Delivery acquires an atomic approval execution lock and revalidates the exact plan before it can create a draft PR.

## Non-Negotiable Invariants

1. **An LLM cannot approve its own mutation.** Approval requires an explicit human interrupt decision.
2. **An LLM cannot lower deterministic risk.** Risk classification is deterministic; the LLM may only add context.
3. **An LLM cannot choose arbitrary shell commands.** Commands come from the registered safe `ValidationCommandRegistry`.
4. **An LLM cannot write outside the repository.** `RepositoryPathPolicy` validates and rejects all non-relative and traversal paths.
5. **An LLM cannot change the exact patch after user approval.** Execution uses the exact stored `DeliveryPlan`; no LLM regeneration occurs between approval and apply.
6. **A changed repository state invalidates stale authorization.** The base SHA is re-verified before execution.
7. **Repository content is data, not instruction.** Retrieved text cannot override the system prompt or policy.
8. **No supporting evidence → no confident executable remediation.** `PatchEngine` enforces that every file edit has a verified evidence ID.
9. **Failure to validate → no push.** Failed pre-approval validation prevents branch push and PR creation.
10. **Mutation ends at a reviewable draft PR.** The system never merges automatically.

## Operational Memory Safety

11. **Memory entries cannot authorize mutations.** Only `ApprovalRecord` with valid `approval_hash` can authorize delivery.
12. **Current evidence overrides memory.** If a memory entry contradicts current repository evidence, evidence wins.
13. **Memory is historical context, not current evidence.** Memory IDs are never merged into evidence IDs.
14. **Delivered fix ≠ merged fix.** `DELIVERED_FIX` entries require `DeliveryResult.status == "success"`. `MERGED_FIX` requires explicit GitHub merge confirmation. Draft PR and push are not sufficient for either.
15. **Every reused fix requires fresh approval.** Historical memory of a prior fix cannot reuse the prior approval. A new `PatchProposal → diff → validation → DeliveryPlan → approval` cycle is always required.
16. **Stale memory is labelled, not hidden.** Historical entries remain visible but are explicitly labelled `[HISTORICAL — REQUIRES CURRENT VERIFICATION]` and must not be used as current evidence.
17. **Learning can be disabled.** `MEMORY_LEARNING_ENABLED=false` prevents all writes while allowing reads to continue.

## Path Security

`RepositoryPathPolicy.validate()` rejects:
- `../` traversal patterns
- Absolute paths (Unix and Windows)
- Drive-letter paths (`C:\...`)
- `.git/` internals
- System directories (`/etc/`, `.ssh/`, `/proc/`, etc.)
- Sensitive files (`.env`, `.pem`, `*.key`, credential stores)
- Symlinks that escape the repository root

## Secret Handling

- `RedactionService` strips credential-shaped values from all evidence, memory entries, error messages, and validation summaries before storage.
- `.env` files are read-restricted; modification is blocked unless the file is an explicitly safe template (`.env.example`, `.env.template`).
- Memory values pass through `RedactionService` before persistence; secret-shaped values are replaced with `[REDACTED]`.

## Sandbox

The execution sandbox (when `USE_DOCKER_WORKSPACES=true`) runs validation commands in Docker with `--network none`, preventing outbound network access during candidate validation. Environment variables are stripped to a safe allowlist before entering the sandbox.
