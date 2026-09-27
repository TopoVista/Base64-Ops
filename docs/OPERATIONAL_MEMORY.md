# Operational Memory

## Repository-state freshness and fix outcomes

`GitChangeProvider` uses local `git diff --name-status source..head` first and authenticated GitHub compare only as a fallback. An unavailable or incomplete comparison never proves a memory current: it requires revalidation.

`source_commit_sha` remains immutable provenance; `last_verified_commit_sha` records later successful verification. Relevant modifications require revalidation, deleted supporting files invalidate repository facts, and renames retain historical paths while recording the mapping.

`DELIVERED_FIX` records successful delivery, not acceptance. A linked `MERGED_FIX` is created only after a read-only GitHub lookup returns `merged=true`; open PRs remain delivered and closed-unmerged PRs are never automatically rejected.

Repository-scoped, commit-aware, evidence-gated durable facts about repositories — surfaced selectively into the agent's context pack.

## What Operational Memory Is

Operational memory records **structured facts** about repositories verified through prior investigation runs:

| Kind | Example |
|---|---|
| `command_fact` | "The Python test command for this repo is `pytest`" |
| `port_fact` | "The API service exposes port `8001`" |
| `env_fact` | "An environment variable named `DATABASE_URL` is referenced" |
| `ci_fact` | "GitHub Actions workflows run on `ubuntu-latest`" |
| `dependency_fact` | "Python dependencies are declared in `requirements.txt`" |
| `repo_fact` | "This repository uses FastAPI as its web framework" |
| `path_fact` | "Configuration for the Docker Compose stack lives in `docker-compose.yml`" |

## What Operational Memory Is NOT

- **Not conversation memory.** It does not store chat history or session context.
- **Not model output.** Unverified LLM outputs never become permanent operational truth.
- **Not an authorization mechanism.** Memory entries carry zero approval weight and cannot authorize any mutation.

## Critical Invariants

1. **Evidence gate**: Every entry must carry at least one verified `evidence_id` from the producing run's evidence set. Entries without evidence are silently dropped.
2. **Delivery gate**: Memory entries are only written after `DeliveryResult.status == "success"`. Failed or rejected deliveries produce no memory writes.
3. **Redaction**: All `value` strings are passed through `RedactionService` before persistence. Credential-shaped values are redacted.
4. **Current evidence outranks memory**: Historical memory is injected as secondary context — always labelled `[HISTORICAL — verify against current repository]`. Fresh evidence from the current run always takes precedence.
5. **Stale entries are excluded**: Any entry whose `commit_sha` does not match the repository's current HEAD is flagged `stale` and excluded from context injection.

## Provenance Model

Each `OperationalMemoryEntry` carries a full `MemoryProvenance` record:

```
MemoryProvenance {
    source_type:         "delivery_result" | "evidence_extraction" | "operator_promotion"
    evidence_ids:        list[str]   # verified EvidenceItem IDs
    run_id:              str         # producing investigation run
    delivery_result_id:  str | None  # set if produced by successful DeliveryResult
    operator_user_id:    str | None  # set if operator-promoted
    extracted_at:        datetime
}
```

## Commit-Aware Staleness

Staleness detection runs at the start of every agent session (`_load_context` node):

1. `MemoryService.check_staleness(repository_id, branch, current_commit_sha)` is called.
2. Any stored entry where `commit_sha != current_commit_sha` is bulk-flagged `staleness_status = "stale"`.
3. Entries matching the current HEAD are refreshed to `"current"`.
4. Stale entries are **excluded** from `recall_for_context`.

Staleness detection is **best-effort and non-blocking** — a failure never aborts a graph run.

## Path-Aware Applicability

Each entry carries a `MemoryApplicability` record controlling when it is surfaced:

```
MemoryApplicability {
    path_prefix:    str | None      # matches if candidate path starts with prefix
    glob_patterns:  list[str]       # matched via fnmatch
    language:       str | None      # informational
    framework:      str | None      # informational
}
```

An entry with no rules (empty prefix, no globs) matches all candidate paths. An entry is only recalled if at least one candidate path in the current investigation matches its applicability rules.

## Write Path

```
DeliveryResult.status == "success"
    └─► _extract_memory_candidates(delivery_plan, evidence)  [rule-based, no LLM]
        └─► MemoryService.record_from_delivery(...)
            ├─► Evidence gate: drop candidates without verified evidence_ids
            ├─► Confidence threshold: drop candidates below MEMORY_MIN_CONFIDENCE (default: 0.6)
            ├─► Redact value through RedactionService
            ├─► Dedup by (repository_id, branch, kind, key):
            │     same value  → update commit_sha + last_confirmed_at + confirmed_count++
            │     diff value  → insert new entry, mark old entry superseded_by = new_id
            └─► Insert new entry
```

Extraction is **rule-based** (no LLM calls): port bindings from Compose files, test runner references from config files, non-secret env var names, dependency file paths.

## Read Path

```
_retrieve node
    └─► MemoryService.recall_for_context(
            repository_id, branch, commit_sha,
            candidate_paths=[paths from retrieved evidence]
        )
        ├─► Filter: staleness_status == "current" AND commit_sha matches
        ├─► Filter: applicability matches at least one candidate_path
        ├─► Filter: confidence >= MEMORY_MIN_CONFIDENCE
        ├─► Sort by confirmed_count DESC
        └─► Cap at MEMORY_MAX_CONTEXT_ENTRIES (default: 12)

Context injection → context_pack["operationalMemory"]
    Each entry labelled: "[HISTORICAL — verify against current repository]"
```

## Deduplication and Supersession

| Scenario | Outcome |
|---|---|
| Same `(repository_id, branch, kind, key)`, same `value` | `confirmed_count++`, `commit_sha` and `last_confirmed_at` updated |
| Same `(repository_id, branch, kind, key)`, **different** `value` | New entry created; old entry marked `superseded_by = new_entry.id` |
| New `(repository_id, branch, kind, key)` | New entry inserted with `confirmed_count = 1` |

## Operator Promotion

Operators (elevated-privilege users) may explicitly promote a memory candidate via `MemoryService.promote_entry(...)`. The same evidence gate applies — the candidate must carry at least one verified evidence ID. Promoted entries have `provenance.source_type = "operator_promotion"` and elevated confidence (minimum 0.85).

## Garbage Collection

`MemoryService.expire_stale(stale_ttl_days=MEMORY_STALE_TTL_DAYS)` soft-deletes stale entries older than `MEMORY_STALE_TTL_DAYS` (default: 90 days). This is a **GC operation** — it must only be called out-of-band (e.g., a scheduled job), never during an active agent graph run.

## API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/memory/{repository_id}` | List current (non-stale) entries, scoped to the authenticated user |
| `GET` | `/api/memory/{repository_id}/staleness` | Return staleness counts for the repository |
| `POST` | `/api/memory/{repository_id}/check-staleness?commit_sha=...` | On-demand staleness check |
| `DELETE` | `/api/memory/entry/{entry_id}` | Soft-delete a single entry |

## Configuration

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `memory_enabled` | `MEMORY_ENABLED` | `true` | Enable/disable the entire memory subsystem |
| `memory_max_context_entries` | `MEMORY_MAX_CONTEXT_ENTRIES` | `12` | Maximum entries injected per run |
| `memory_stale_ttl_days` | `MEMORY_STALE_TTL_DAYS` | `90` | Days before stale entries are soft-deleted by GC |
| `memory_min_confidence` | `MEMORY_MIN_CONFIDENCE` | `0.6` | Minimum confidence for write and recall |

## Security Guarantees

- Memory entries cannot authorize mutations. Only `ApprovalRecord` with valid `approval_hash` can authorize a delivery.
- Memory values are always redacted before persistence.
- All reads are scoped to `user_id` — cross-user access is not possible through any service method.
- Stale entries are always excluded from context injection unless explicitly requested.
- The write path requires both a verified `evidence_id` and a successful `DeliveryResult` — unverified model output cannot become a memory entry.
