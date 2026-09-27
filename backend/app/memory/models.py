"""Typed schemas for repository-scoped operational memory entries.

Design principles
-----------------
- Memory is historical context, not current evidence.
- Memory cannot authorize any mutation.
- Current evidence always overrides memory.
- Delivered fix != merged fix.
- Staleness is path-aware, not repository-wide SHA equality.
- Stale history is retained and distinguishable, never silently deleted.
- Secrets are never persisted; all values pass through RedactionService.
- Learning can be disabled globally via Settings.memory_learning_enabled.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Semantic memory kinds
# ---------------------------------------------------------------------------


class OperationalMemoryKind(StrEnum):
    """Semantic categories of operational memory.

    Technical specificity is expressed through ``tags`` on the entry,
    not through dedicated kind variants.  This prevents the kind
    enumeration from growing into an implementation-detail taxonomy.
    """

    REPOSITORY_FACT = "repository_fact"
    """A durable structural fact about the repository.

    Examples: framework, test runner, dependency file, compose port.

    Required provenance: ``source_commit_sha``, ``evidence_ids``,
    ``affected_paths``.
    """

    INCIDENT_OUTCOME = "incident_outcome"
    """The diagnosed outcome of an evidence-supported investigation.

    A code change is NOT required.  Diagnosis alone is sufficient.
    """

    DELIVERED_FIX = "delivered_fix"
    """A fix whose branch/commit/draft-PR delivery completed.

    Requires: ``DeliveryResult.status == "success"``.
    Does NOT imply merged — see MERGED_FIX.
    """

    MERGED_FIX = "merged_fix"
    """A fix confirmed merged by GitHub (PR state == merged).

    May only be written after GitHub returns merge confirmation.
    Draft PR creation or push success are NOT sufficient.
    """

    REJECTED_FIX = "rejected_fix"
    """A concrete proposal that was explicitly rejected by the user.

    Stores what was proposed and that it was rejected.
    Does NOT store "this fix is wrong" unless independent current
    evidence establishes that separately.
    """

    USER_CORRECTION = "user_correction"
    """An explicit repository/workflow correction supplied by the user.

    Must be: explicit, repository-relevant, non-sensitive.
    """

    OPERATIONAL_PREFERENCE = "operational_preference"
    """An explicit workflow preference stated by the user.

    Examples: "always use draft PRs", "do not modify CI automatically".
    Must be explicitly stated; not inferred from a single action.
    """


# Backward compatibility alias so existing callers importing the old name
# continue to work during the transition.
MemoryKind = OperationalMemoryKind


# ---------------------------------------------------------------------------
# Verification lifecycle
# ---------------------------------------------------------------------------

VerificationStatus = Literal[
    "verified",  # Confirmed against current repository state.
    "partially_verified",  # Some but not all aspects confirmed.
    "needs_revalidation",  # Relevant paths changed; must be re-checked.
    "stale",  # Evidence proves the fact is no longer current.
    "expired",  # TTL exceeded.
    "invalidated",  # Explicitly superseded or contradicted.
]

# Lightweight freshness signal for context injection decisions.
FreshnessLevel = Literal["current", "needs_revalidation", "stale"]


# ---------------------------------------------------------------------------
# Source type for provenance
# ---------------------------------------------------------------------------

ProvenanceSourceType = Literal[
    "delivery_result",  # Produced by a successful DeliveryResult.
    "incident_outcome",  # Produced by an evidence-supported investigation.
    "rejected_fix",  # Produced when a proposal was rejected.
    "user_correction",  # Supplied explicitly by the user.
    "user_preference",  # Explicit workflow preference from the user.
    "merged_fix",  # Confirmed merged via GitHub.
    "evidence_extraction",  # Extracted from current evidence (legacy).
    "operator_promotion",  # Explicitly promoted by an operator.
]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class MemoryProvenance(BaseModel):
    """Records the verified origin of a memory entry.

    Trustworthiness is proportional to traceability.
    - ``evidence_ids``: IDs of verified EvidenceItems supporting this fact.
    - ``run_id``: The investigation run that produced the entry.
    - ``delivery_result_id``: Set for DELIVERED_FIX / MERGED_FIX.
    - ``source_commit_sha``: Repository HEAD when the entry was FIRST extracted.
      Never overwritten after initial write — historical provenance is preserved.
    - ``affected_paths``: Paths relevant to this fact (drives path-aware staleness).
    - ``operator_user_id``: Set when an operator explicitly promoted the entry.
    - ``extracted_at``: When the entry was first extracted and persisted.

    PR-specific fields (set for DELIVERED_FIX and MERGED_FIX)
    ----------------------------------------------------------
    - ``pr_number``: GitHub pull request number created by the delivery.
    - ``pr_url``: HTML URL of the pull request.
    - ``delivered_commit_sha``: The commit SHA that was pushed/merged.
    - ``merge_commit_sha``: Set only after a confirmed GitHub merge.
    - ``merged_at``: Datetime of the confirmed merge.
    """

    source_type: ProvenanceSourceType
    evidence_ids: list[str] = Field(default_factory=list)
    run_id: str
    delivery_result_id: str | None = None

    # Immutable — the commit SHA when this entry was FIRST recorded.
    source_commit_sha: str | None = None

    affected_paths: list[str] = Field(default_factory=list)
    operator_user_id: str | None = None
    extracted_at: datetime

    # PR provenance — populated for DELIVERED_FIX / MERGED_FIX.
    pr_number: int | None = None
    pr_url: str | None = None
    delivered_commit_sha: str | None = None

    # Populated only after GitHub merge confirmation.
    merge_commit_sha: str | None = None
    merged_at: datetime | None = None


class MemoryApplicability(BaseModel):
    """Path-aware applicability rules controlling when an entry is surfaced.

    An entry is surfaced only when at least one investigated candidate path
    matches these rules.  Empty rules match all paths.
    """

    path_prefix: str | None = None
    glob_patterns: list[str] = Field(default_factory=list)
    language: str | None = None
    framework: str | None = None


# ---------------------------------------------------------------------------
# Primary persisted model
# ---------------------------------------------------------------------------


class OperationalMemoryEntry(BaseModel):
    """A durable, repository-scoped operational fact.

    Invariants
    ----------
    - ``provenance.evidence_ids`` must be non-empty; entries without evidence
      are rejected by MemoryWritePolicy.
    - ``value`` is always redacted through RedactionService before persistence.
    - ``verification_status`` tracks the full lifecycle; stale entries are
      retained with their history intact.
    - ``superseded_by`` links to the newer entry when a conflicting value
      with the same composite key has been recorded.
    - Memory entries carry ZERO approval weight.  They cannot authorize any
      mutation.
    """

    id: str
    user_id: str
    repository_id: str
    branch: str

    # Semantic kind — replaces the old implementation-detail literals.
    kind: OperationalMemoryKind

    # Human-readable normalized statement or key for deduplication.
    key: str

    # Redacted summary value.
    value: str

    # Technical tags for secondary classification (port, ci, auth, docker …).
    tags: list[str] = Field(default_factory=list)

    confidence: float = Field(ge=0.0, le=1.0, default=0.7)

    provenance: MemoryProvenance
    applicability: MemoryApplicability = Field(default_factory=MemoryApplicability)

    created_at: datetime
    last_confirmed_at: datetime
    confirmed_count: int = Field(ge=1, default=1)

    # Full lifecycle state — never use boolean stale/current alone.
    verification_status: VerificationStatus = "verified"

    # Retained for backward compatibility; derived from verification_status.
    staleness_status: Literal["current", "stale", "unverifiable"] = "current"

    superseded_by: str | None = None
    deleted_at: datetime | None = None

    # The last commit SHA at which this entry was positively revalidated.
    # Distinct from provenance.source_commit_sha, which records the ORIGIN.
    # source_commit_sha is never overwritten; this field advances on each
    # successful revalidation.
    last_verified_commit_sha: str | None = None

    # For DELIVERED_FIX / MERGED_FIX: delivery outcome after PR close.
    # Values: None (unknown), "open", "merged", "closed_unmerged"
    delivery_outcome: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# MemoryMatch — retrieval result with freshness signal
# ---------------------------------------------------------------------------


class MemoryMatch(BaseModel):
    """A retrieved memory entry with freshness and relevance metadata.

    Used in context injection to clearly communicate staleness to consumers.
    Never merged with current evidence IDs.
    """

    memory_id: str
    kind: OperationalMemoryKind
    key: str
    value: str
    tags: list[str] = Field(default_factory=list)
    confidence: float

    # Freshness signal derived from path-aware staleness check.
    freshness: FreshnessLevel

    # Full lifecycle state.
    verification_status: VerificationStatus

    # Commit SHA when this fact was recorded.
    source_commit_sha: str | None

    # Paths relevant to this fact; used to decide path-aware staleness.
    affected_paths: list[str] = Field(default_factory=list)

    # Why this entry matched the current investigation.
    relevance_reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Lightweight read model for context injection
# ---------------------------------------------------------------------------


class MemorySummary(BaseModel):
    """Concise projection of a memory entry for agent context injection.

    Injected into ``context_pack["operational_memory_summary"]``.
    The full entry is not inlined into the prompt.

    Label semantics
    ---------------
    - freshness == "current"             → inject as historical hint
    - freshness == "needs_revalidation"  → label ``[HISTORICAL — REQUIRES CURRENT VERIFICATION]``
    - freshness == "stale"               → label ``[HISTORICAL — REQUIRES CURRENT VERIFICATION]``
    """

    id: str
    kind: OperationalMemoryKind
    key: str
    value: str
    tags: list[str] = Field(default_factory=list)
    confidence: float
    freshness: FreshnessLevel
    verification_status: VerificationStatus
    source_commit_sha: str | None = None
    last_verified_commit_sha: str | None = None
    last_confirmed_at: datetime
    confirmed_count: int
    evidence_ids: list[str]
    affected_paths: list[str] = Field(default_factory=list)
    path_hint: str | None = None
    # PR provenance for DELIVERED_FIX / MERGED_FIX display
    pr_number: int | None = None
    pr_url: str | None = None
    delivery_outcome: str | None = None


# ---------------------------------------------------------------------------
# Write-path input model
# ---------------------------------------------------------------------------


class MemoryEntryCandidate(BaseModel):
    """Input to MemoryWritePolicy / MemoryService.

    ``kind`` must match the appropriate write-policy gate:
    - DELIVERED_FIX   → only via record_delivered_fix (requires delivery_result_id)
    - MERGED_FIX      → only via record_merged_fix (requires GitHub confirmation)
    - INCIDENT_OUTCOME→ via record_incident_outcome (evidence required, delivery not required)
    - REJECTED_FIX    → via record_rejected_fix (requires an actual proposal rejection)
    - USER_CORRECTION → via record_user_correction (explicit user statement required)
    - OPERATIONAL_PREFERENCE → via record_operational_preference (explicit preference required)
    - REPOSITORY_FACT → via record_from_delivery or record_incident_outcome

    Every candidate must supply at least one evidence_id verified during the current run.
    """

    kind: OperationalMemoryKind
    key: str
    value: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    evidence_ids: list[str] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    applicability: MemoryApplicability = Field(default_factory=MemoryApplicability)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Staleness report
# ---------------------------------------------------------------------------


class StalenessReport(BaseModel):
    """Result of MemoryService.check_staleness."""

    repository_id: str
    branch: str
    checked_commit_sha: str
    total_entries: int
    current_count: int
    stale_count: int
    needs_revalidation_count: int
    unverifiable_count: int
    newly_stale_count: int
    newly_needs_revalidation_count: int
