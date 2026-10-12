"""Bounded, evidence-safe change impact analysis.

Impact analysis consumes a revision-bound Engineering Graph snapshot and an
already-created DeliveryPlan.  It is intentionally read-only: its output is a
potential blast-radius explanation, never a mutation authorization or proof
that a change will succeed.
"""

from app.intelligence.impact.models import ChangeImpactAnalysis, ChangeImpactStatus
from app.intelligence.impact.service import ChangeImpactService

__all__ = ["ChangeImpactAnalysis", "ChangeImpactService", "ChangeImpactStatus"]
