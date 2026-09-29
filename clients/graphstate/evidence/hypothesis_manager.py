"""
HypothesisManager — structured management, scoring, and expansion of hypotheses.

Core Responsibilities:
1. Maintains hypothesis set H_t = {H_1, ... H_k} with provenance, support, and contradictions.
2. Scores hypotheses based on evidence grounding and contradiction penalties.
3. Implements the Hypothesis Expansion Trigger:
     max_i Score(H_i) < tau  ==>  GENERATE NEW HYPOTHESES
   Prevents ranking bad candidates endlessly when all current hypotheses lack support.
4. Manages the transition from Blind Investigation Phase to Cross-Examination Phase.
"""

from __future__ import annotations

import logging
from typing import Any

from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.schema import Hypothesis, HypothesisStatus

logger = logging.getLogger(__name__)

DEFAULT_EXPANSION_THRESHOLD = 0.40  # tau


class HypothesisManager:
    """Manages active hypotheses, contradiction tracking, and expansion triggers."""

    def __init__(self, expansion_threshold: float = DEFAULT_EXPANSION_THRESHOLD):
        self.tau = expansion_threshold

    def score_hypothesis(self, h: Hypothesis, state: SREGraphState | None = None) -> float:
        """
        Compute evidence-grounded score:
          Score(H) = 0.40 * Confidence
                   + 0.40 * (|Support| / (|Support| + |Contradiction| + 1))
                   - 0.20 * min(|Contradiction|, 2)
        Score is clamped to [0.0, 1.0].
        """
        if h.status == HypothesisStatus.REJECTED:
            return 0.0

        num_sup = len(h.supporting_evidence)
        num_contra = len(h.contradictory_evidence)

        evidence_ratio = num_sup / (num_sup + num_contra + 1.0)
        penalty = 0.20 * min(num_contra, 2)

        raw_score = (0.40 * h.confidence) + (0.40 * evidence_ratio) - penalty
        return max(0.0, min(1.0, raw_score))

    def rank_hypotheses(self, state: SREGraphState) -> list[tuple[Hypothesis, float]]:
        """
        Returns active hypotheses sorted by computed score descending.
        """
        active = [
            h for h in state.get("hypotheses", [])
            if h.status == HypothesisStatus.ACTIVE
        ]
        scored = [(h, self.score_hypothesis(h, state)) for h in active]
        return sorted(scored, key=lambda pair: pair[1], reverse=True)

    def should_trigger_expansion(
        self,
        state: SREGraphState,
        tau: float | None = None,
    ) -> tuple[bool, str]:
        """
        Checks if max_i Score(H_i) < tau.
        If all existing hypotheses are poorly supported or contradicted, triggers
        GENERATE NEW HYPOTHESES rather than cycling through bad candidates.
        """
        threshold = tau if tau is not None else self.tau
        ranked = self.rank_hypotheses(state)

        if not ranked:
            return True, "no_active_hypotheses"

        top_h, top_score = ranked[0]
        if top_score < threshold:
            logger.info(
                "Hypothesis expansion triggered: top score %.2f < threshold %.2f (H_id=%s)",
                top_score, threshold, top_h.id,
            )
            return True, f"max_score_{top_score:.2f}_below_tau_{threshold:.2f}"

        return False, "sufficient_support"

    def merge_blind_hypotheses(
        self,
        state: SREGraphState,
        incoming: list[Hypothesis],
    ) -> list[Hypothesis]:
        """
        Merge hypotheses after blind investigation phase into the shared cross-examination pool.
        Deduplicates near-identical claims from different investigators while tracking agent origin.
        """
        existing = {h.id: h for h in state.get("hypotheses", [])}
        for h in incoming:
            # Check for near-identical claim on same service
            duplicate_found = False
            for ex in existing.values():
                if (
                    set(ex.affected_services) == set(h.affected_services)
                    and ex.causal_path == h.causal_path
                    and ex.claim.lower().strip() == h.claim.lower().strip()
                ):
                    # Merge supporting evidence and increase confidence slightly
                    merged_support = list(set(ex.supporting_evidence + h.supporting_evidence))
                    ex.supporting_evidence = merged_support
                    ex.confidence = min(1.0, max(ex.confidence, h.confidence) + 0.05)
                    duplicate_found = True
                    break

            if not duplicate_found:
                existing[h.id] = h

        return list(existing.values())

    def record_contradiction(
        self,
        hypothesis_id: str,
        contradiction_evidence_id: str,
        reason: str,
        state: SREGraphState,
    ) -> bool:
        """
        Add contradictory evidence to a hypothesis and lower confidence.
        If contradictions >= 2 and confidence < 0.3, marks hypothesis REJECTED.
        """
        for h in state.get("hypotheses", []):
            if h.id == hypothesis_id:
                if contradiction_evidence_id not in h.contradictory_evidence:
                    h.contradictory_evidence.append(contradiction_evidence_id)
                h.confidence = max(0.0, h.confidence - 0.25)
                logger.info("Hypothesis %s penalized by %s: %s (new conf=%.2f)",
                            hypothesis_id, contradiction_evidence_id, reason, h.confidence)

                if len(h.contradictory_evidence) >= 2 and h.confidence < 0.30:
                    h.status = HypothesisStatus.REJECTED
                    logger.info("Hypothesis %s rejected due to accumulated contradictions", hypothesis_id)
                return True
        return False
