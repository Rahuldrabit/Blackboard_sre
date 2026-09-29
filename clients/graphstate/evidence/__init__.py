"""
Evidence package for Secure Blackboard hypothesis management, falsification, and causal verification.

Exports:
  - HypothesisManager: hypothesis ranking, contradiction penalization, expansion trigger
  - FalsificationEngine: discriminating test generation and utility ranking
  - CausalVerifier: deterministic 4-step causal verification
  - DiagnosisGate: multi-condition pre-submission gate
"""

from clients.graphstate.evidence.causal_verifier import (
    CausalVerifier,
    VerificationResult,
)
from clients.graphstate.evidence.diagnosis_gate import (
    DiagnosisGate,
    GateEvaluationResult,
)
from clients.graphstate.evidence.falsification import FalsificationEngine
from clients.graphstate.evidence.hypothesis_manager import HypothesisManager

__all__ = [
    "CausalVerifier",
    "DiagnosisGate",
    "FalsificationEngine",
    "GateEvaluationResult",
    "HypothesisManager",
    "VerificationResult",
]
