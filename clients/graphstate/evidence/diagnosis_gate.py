"""
Diagnosis Gate — multi-condition verification gate before SREGym submission.

Architectural Requirement:
  Before submitting a diagnosis to SREGym, the system requires:
    1. Root cause candidate + failure mode
    2. Causal mechanism (propagation path verified against service graph)
    3. Supporting evidence IDs (>= 2 independent sources)
    4. Contradiction check (all contradictory evidence cleared or refuted)
    5. Current failure evidence (demonstrating active ongoing incident)

Combination:
    Rules + Jev + LLM synthesis (not Jev or LLM alone).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from clients.graphstate.evidence.causal_verifier import CausalVerifier
from clients.graphstate.evidence.hypothesis_manager import HypothesisManager
from clients.graphstate.routing.jev_router import JevRouter
from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.schema import (
    Diagnosis,
    Hypothesis,
    HypothesisStatus,
)

logger = logging.getLogger(__name__)


class GateEvaluationResult:
    def __init__(
        self,
        approved: bool,
        diagnosis: Diagnosis | None = None,
        reasons: list[str] | None = None,
        missing_evidence: list[str] | None = None,
    ):
        self.approved = approved
        self.diagnosis = diagnosis
        self.reasons = reasons or []
        self.missing_evidence = missing_evidence or []

    def __bool__(self) -> bool:
        return self.approved


class DiagnosisGate:
    """Rigorous gate preventing premature or weakly-grounded diagnosis submissions."""

    def __init__(
        self,
        causal_verifier: CausalVerifier | None = None,
        hypothesis_manager: HypothesisManager | None = None,
        jev_router: JevRouter | None = None,
    ):
        self.verifier = causal_verifier or CausalVerifier()
        self.hyp_mgr = hypothesis_manager or HypothesisManager()
        self.jev = jev_router or JevRouter()

    def evaluate_submission(self, state: SREGraphState) -> GateEvaluationResult:
        """
        Evaluate if the state satisfies all 5 diagnosis submission criteria.
        """
        reasons: list[str] = []
        missing: list[str] = []

        # 1. Candidate check: at least one active hypothesis
        ranked = self.hyp_mgr.rank_hypotheses(state)
        if not ranked:
            return GateEvaluationResult(
                approved=False,
                reasons=["No active hypotheses available in blackboard state."],
            )

        top_h, top_score = ranked[0]

        # 2. Causal mechanism and verification check
        ver_result = self.verifier.verify_hypothesis(top_h, state)
        if not ver_result.passed:
            reasons.extend(ver_result.failure_reasons)
            if not ver_result.structural_ok:
                missing.append("structural_path_validation")
            if not ver_result.sufficiency_ok:
                missing.append("independent_evidence_sources")

        # 3. Contradiction check: no unresolved contradictions
        if len(top_h.contradictory_evidence) > 0:
            reasons.append(
                f"Candidate hypothesis {top_h.id} has {len(top_h.contradictory_evidence)} "
                f"unresolved contradictory evidence references: {top_h.contradictory_evidence}"
            )
            missing.append("contradiction_resolution")

        # 4. Confidence threshold check
        if top_h.confidence < 0.75:
            reasons.append(f"Confidence {top_h.confidence:.2f} is below minimum submission threshold 0.75")
            missing.append("higher_confidence_grounding")

        # 5. Current failure evidence
        has_active_obs = len(state.get("observations", [])) > 0
        if not has_active_obs:
            reasons.append("No active observations demonstrating ongoing incident")
            missing.append("active_telemetry_observations")

        approved = len(reasons) == 0

        diagnosis = None
        if approved:
            diagnosis = Diagnosis(
                root_cause=top_h.claim,
                affected_services=top_h.affected_services,
                causal_path=top_h.causal_path,
                confidence=top_h.confidence,
                supporting_evidence=list(top_h.supporting_evidence),
                verified_claim_id=ver_result.verified_claim.id if ver_result.verified_claim else None,
                diagnosis_method="diagnosis_gate_verified",
            )
            logger.info("DiagnosisGate APPROVED submission for hypothesis %s: %s", top_h.id, top_h.claim)
        else:
            logger.info("DiagnosisGate REJECTED submission. Reasons: %s", reasons)

        return GateEvaluationResult(
            approved=approved,
            diagnosis=diagnosis,
            reasons=reasons,
            missing_evidence=missing,
        )
