# Operational Memory

Repository-scoped, commit-aware, evidence-gated durable facts — surfaced selectively as historical context into the agent's context pack.

## Architectural Principles

**Memory is historical context, not current evidence.**
Memory entries represent what was learned in prior runs. They are never promoted to the status of current evidence without new verification from the repository.

**Memory cannot authorize any mutation.**
`ApprovalRecord` with a valid `approval_hash` is the only authorization mechanism. No combination of memory entries can substitute for a fresh approval cycle.

**Current evidence always overrides memory.**
If a current evidence source contradicts a memory entry, the evidence wins and the memory entry's `verification_status` is updated accordingly.

**Delivered fix ≠ merged fix.**
`DELIVERED_FIX` means a branch/commit/draft-PR delivery succeeded. `MERGED_FIX` requires explicit GitHub PR merge confirmation.

---

## Semantic Memory Kinds

| Kind | Meaning | Write Gate |
|---|---|---|
| `repository_fact` | Durable structural fact (framework, test runner, port, dep file) | Evidence required, `record_from_delivery` or `record_incident_outcome` |
| `incident_outcome` | Diagnosed outcome of an evidence-supported investigation | Evidence required, no delivery needed |
| `delivered_fix` | A fix whose branch/commit/draft-PR delivery completed | `DeliveryResult.status == "success"` AND `delivery_result_id` required |
| `merged_fix` | A fix confirmed merged by GitHub | `github_merge_confirmed=True` required (draft PR / push are NOT sufficient) |
| `rejected_fix` | A concrete proposal explicitly rejected by the user | `rejection_confirmed=True` required |
| `user_correction` | Explicit repository/workflow correction from the user | `is_explicit_user_statement=True` required |
| `operational_preference` | Explicit workflow preference stated by the user | `is_explicit_preference=True` required; single-action inference NOT sufficient |

### Technical Tags (not kinds)

Technical specificity is expressed through `tags`, not through separate kind variants:

```
port, docker, runtime, environment, ci, auth, database, dependency, deployment
```

---

## Write Policy

`MemoryWritePolicy.check()` gates every write attempt. The LLM may produce a `MemoryEntryCandidate`; only the policy decides eligibility.

```
Universal gates (all kinds)
├── evidence_ids must intersect verified_evidence_ids
│   (exception: USER_CORRECTION, OPERATIONAL_PREFERENCE)
└── confidence >= MEMORY_MIN_CONFIDENCE (default: 0.6)

Kind-specific gates
├── DELIVERED_FIX       → delivery_result_id required
├── MERGED_FIX          → github_merge_confirmed=True required
├── REJECTED_FIX        → rejection_confirmed=True required
├── USER_CORRECTION     → is_explicit_user_statement=True required
└── OPERATIONAL_PREFERENCE → is_explicit_preference=True required
```

---

## Verification Lifecycle

```
verified            — Confirmed against current repository state.
partially_verified  — Some but not all aspects confirmed.
needs_revalidation  — Relevant paths changed; must be re-checked.
stale               — Evidence proves the fact is no longer current.
expired             — TTL exceeded (soft-deleted by GC).
invalidated         — Explicitly superseded or contradicted.
```

Stale entries are **never deleted immediately**. They remain queryable via `recall_historical` and are labelled `[HISTORICAL — REQUIRES CURRENT VERIFICATION]`.

---

## Provenance Model

```python
MemoryProvenance {
    source_type:         ProvenanceSourceType
    evidence_ids:        list[str]     # verified EvidenceItem IDs
    run_id:              str           # producing investigation run
    delivery_result_id:  str | None    # set for DELIVERED_FIX / MERGED_FIX
    source_commit_sha:   str | None    # HEAD when the entry was extracted
    affected_paths:      list[str]     # paths relevant to this fact
    operator_user_id:    str | None    # set for operator_promotion
    extracted_at:        datetime
}
```

---

## Path-Aware Staleness

Staleness uses `affected_paths` rather than repository-wide SHA equality:

```
check_staleness(changed_paths_since_source=[...])
    ↓
For each entry with affected_paths:
    if any(p in changed_paths for p in affected_paths):
        → verification_status = "needs_revalidation"
        → staleness_status    = "stale"
    else:
        → entry remains "current" (HEAD changed but paths unrelated)

Legacy entries with no affected_paths:
    → SHA equality fallback (source_commit_sha != current → stale)

Entries with no source_commit_sha:
    → verification_status = "needs_revalidation" (unverifiable)
```

This means a frontend memory entry is **not** staled when only backend files changed.

---

## Context Precedence

```
system / policy
    > current trusted repository/runtime evidence      ← PRIMARY
    > verified operational memory (freshness="current")← SECONDARY (max 5)
    > needs-revalidation memory                        ← TERTIARY (historical)
    > stale historical memory                          ← HISTORICAL ONLY
```

Fresh memory is injected into `context_pack["operational_memory_summary"]`.
Historical memory is injected into `context_pack["historical_memory"]`.
**Memory IDs are never merged into evidence IDs.** Frontend must display them as "Related history", not "Evidence".

---

## Write Path (Post-Delivery)

```
DeliveryResult.status == "success"
    └─► _extract_memory_candidates(delivery_plan, evidence)  [rule-based, no LLM]
        └─► MemoryWritePolicy.check(candidate, verified_evidence_ids, ...)
            └─► MemoryService.record_from_delivery(...)
                ├─► Evidence gate: drop if no verified evidence_ids
                ├─► Confidence threshold: drop if below MEMORY_MIN_CONFIDENCE
                ├─► Redact value through RedactionService
                ├─► Dedup by (repository_id, branch, kind, key):
                │     same value  → update source_commit_sha + last_confirmed_at + confirmed_count++
                │     diff value  → insert new entry, mark old entry superseded_by = new_id
                └─► Insert new entry
```

---

## Read Path

### Current (fresh) recall
```
_retrieve node
    └─► MemoryService.recall_for_context(
            user_id, repository_id, branch, commit_sha,
            candidate_paths=[paths from retrieved evidence]
        )
        ├─► Filter: staleness_status == "current"
        ├─► Filter: verification_status in {"verified", "partially_verified"}
        ├─► Filter: applicability matches at least one candidate_path
        ├─► Filter: confidence >= MEMORY_MIN_CONFIDENCE
        ├─► Rank by: path_overlap > verification_status > confirmed_count
        └─► Cap at MEMORY_MAX_CONTEXT_ENTRIES (default: 5)
```

### Historical recall (stale)
```
_retrieve node
    └─► MemoryService.recall_historical(
            user_id, repository_id, branch, candidate_paths
        )
        ├─► Filter: staleness_status == "stale"
        ├─► Filter: applicability matches
        ├─► Sort by: last_confirmed_at DESC
        └─► Cap at max_historical_entries (default: 3)
        └─► Each result labelled: "[HISTORICAL — REQUIRES CURRENT VERIFICATION]"
```

---

## Deduplication and Supersession

| Scenario | Outcome |
|---|---|
| Same `(repository_id, branch, kind, key)`, same `value` | `confirmed_count++`, `source_commit_sha` and `last_confirmed_at` updated |
| Same `(repository_id, branch, kind, key)`, **different** `value` | New entry created; old entry: `superseded_by = new_id`, `verification_status = "invalidated"` |
| New `(repository_id, branch, kind, key)` | New entry inserted with `confirmed_count = 1` |

---

## Revalidation Flow

When historical memory is retrieved, the agent must revalidate it against current repository evidence before using it for diagnosis:

```
historical memory
    ↓
candidate hypothesis
    ↓
current repository/runtime retrieval
    ↓
confirm or reject
    ↓
update verification_status via revalidate_entry()
```

**Wrong pattern:**
```
historical memory → diagnosis (without current verification)
```

---

## Configuration

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `memory_enabled` | `MEMORY_ENABLED` | `true` | Enable/disable the entire memory subsystem |
| `memory_learning_enabled` | `MEMORY_LEARNING_ENABLED` | `true` | Gate all writes; reads continue if false |
| `memory_max_context_entries` | `MEMORY_MAX_CONTEXT_ENTRIES` | `5` | Maximum fresh entries injected per run |
| `memory_stale_ttl_days` | `MEMORY_STALE_TTL_DAYS` | `90` | Days before stale entries are soft-deleted by GC |
| `memory_min_confidence` | `MEMORY_MIN_CONFIDENCE` | `0.6` | Minimum confidence for write and recall |

---

## Security Guarantees

- Memory entries cannot authorize mutations. Only `ApprovalRecord` with valid `approval_hash` can authorize a delivery.
- All values are redacted through `RedactionService` before persistence.
- All reads are scoped to `user_id` — cross-user access is architecturally impossible.
- Stale entries retain their history but are clearly labelled and never used as current evidence.
- Write path requires verified evidence (or explicit user statement) — unverified LLM output cannot become a memory entry.
- Draft PR creation and push do NOT produce `MERGED_FIX` entries.
- Every reused fix still requires: new current evidence → new PatchProposal → new diff → new validation → new approval.

---

## API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/memory/{repository_id}` | List current entries, scoped to the authenticated user |
| `GET` | `/api/memory/{repository_id}/staleness` | Return staleness counts |
| `POST` | `/api/memory/{repository_id}/check-staleness?commit_sha=...` | On-demand staleness check |
| `DELETE` | `/api/memory/entry/{entry_id}` | Soft-delete a single entry |
