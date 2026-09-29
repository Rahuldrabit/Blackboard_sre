"""
Adaptive Agent Invoker — modulates agent count N based on incident uncertainty.

Core Principle:
    N = f(incident uncertainty)
- Simple incidents: N = 1 (Primary investigator resolves quickly, saves >40% tokens).
- Ambiguous incidents: N = 2 (Targeted specialist invoked to resolve specific evidence gap).
- Conflicted incidents: N = 3 (Full triangulation across topology, telemetry, and system specialists).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.schema import Hypothesis, HypothesisStatus

logger = logging.getLogger(__name__)


class UncertaintyLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class AdaptiveAgentInvoker:
    """Evaluates incident ambiguity to dynamically scale active agent count."""

    def __init__(
        self,
        high_confidence_threshold: float = 0.85,
        min_evidence_for_low_uncertainty: int = 2,
    ):
        self.high_conf = high_confidence_threshold
        self.min_evidence = min_evidence_for_low_uncertainty

    def assess_uncertainty(self, state: SREGraphState) -> UncertaintyLevel:
        """
        Assess current state uncertainty based on hypothesis confidence,
        evidence volume, and active contradictions.
        """
        active_hyps = [
            h for h in state.get("hypotheses", [])
            if h.status == HypothesisStatus.ACTIVE
        ]

        if not active_hyps:
            return UncertaintyLevel.HIGH

        top_h = max(active_hyps, key=lambda h: h.confidence)

        # Condition for LOW uncertainty (N=1):
        # High confidence, sufficient evidence, and no contradictory evidence
        if (
            top_h.confidence >= self.high_conf
            and len(top_h.supporting_evidence) >= self.min_evidence
            and len(top_h.contradictory_evidence) == 0
        ):
            return UncertaintyLevel.LOW

        # Condition for MODERATE uncertainty (N=2):
        # Medium confidence or missing one domain's evidence
        if top_h.confidence >= 0.60 and len(top_h.contradictory_evidence) <= 1:
            return UncertaintyLevel.MODERATE

        # Condition for HIGH uncertainty (N=3):
        # Multiple strong competitors, heavy contradictions, or low confidence
        return UncertaintyLevel.HIGH

    def select_next_specialist(self, state: SREGraphState) -> str | None:
        """
        For MODERATE uncertainty (N=2), select the specialist that addresses
        the primary evidence gap.
        """
        invoked = set(state.get("specialists_invoked", []))
        active_hyps = [
            h for h in state.get("hypotheses", [])
            if h.status == HypothesisStatus.ACTIVE
        ]
        top_h = max(active_hyps, key=lambda h: h.confidence) if active_hyps else None

        # Determine evidence gap from top hypothesis
        if top_h:
            claim_text = (top_h.claim + " " + " ".join(top_h.untested_predictions)).lower()
            if ("config" in claim_text or "manifest" in claim_text or "limit" in claim_text or "k8s" in claim_text):
                if "config_system" not in invoked:
                    return "config_system"
            if ("log" in claim_text or "metric" in claim_text or "cpu" in claim_text or "heap" in claim_text):
                if "telemetry" not in invoked:
                    return "telemetry"
            if ("trace" in claim_text or "latency" in claim_text or "dependency" in claim_text or "network" in claim_text):
                if "topology" not in invoked:
                    return "topology"

        # Default fallback to uninvoked specialist
        for role in ("telemetry", "topology", "config_system"):
            if role not in invoked:
                return role

        return None
