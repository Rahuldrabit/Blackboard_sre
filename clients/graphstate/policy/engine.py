"""
PolicyEngine — the deterministic rule engine.

This is NOT an LLM. It is pure Python.
No model is called here. No generation happens here.
Every decision is a deterministic function of current state.

Responsibilities:
  - Phase transition validation
  - Agent permission checking (who can write what)
  - Freshness enforcement (expire stale observations)
  - Budget enforcement
  - Delegation depth limits
  - Producing the rule set injected into compiled prompts
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.knowledge_levels import KnowledgeLevel
from clients.graphstate.state.schema import (
    HypothesisStatus,
    Observation,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_DELEGATION_DEPTH = 3
DIAGNOSIS_CONFIDENCE_THRESHOLD = 0.75
DIAGNOSIS_MIN_EVIDENCE_SOURCES = 2
DIAGNOSIS_MAX_UNRESOLVED_COMPETITORS = 1
BUDGET_WARN_THRESHOLD = 0.2  # Warn when < 20% budget remains


# ── Permission matrix ────────────────────────────────────────────────────────

WRITE_PERMISSIONS: dict[str, dict[str, set[KnowledgeLevel]]] = {
    # role → phase → {allowed knowledge levels}
    "primary_investigator": {
        "diagnosis": {KnowledgeLevel.OBSERVED, KnowledgeLevel.DERIVED, KnowledgeLevel.HYPOTHESIZED},
        "mitigation": set(),
    },
    "telemetry": {
        "diagnosis": {KnowledgeLevel.OBSERVED, KnowledgeLevel.DERIVED, KnowledgeLevel.HYPOTHESIZED},
        "mitigation": set(),
    },
    "topology": {
        "diagnosis": {KnowledgeLevel.OBSERVED, KnowledgeLevel.DERIVED, KnowledgeLevel.HYPOTHESIZED},
        "mitigation": set(),
    },
    "config_system": {
        "diagnosis": {KnowledgeLevel.OBSERVED, KnowledgeLevel.DERIVED, KnowledgeLevel.HYPOTHESIZED},
        "mitigation": set(),
    },
    "falsifier": {
        "diagnosis": {KnowledgeLevel.OBSERVED, KnowledgeLevel.DERIVED},
        "mitigation": set(),
    },
    "causal_verifier": {
        "diagnosis": {KnowledgeLevel.VERIFIED},
        "mitigation": {KnowledgeLevel.VERIFIED},
    },
    "mitigation_planner": {
        "diagnosis": set(),
        "mitigation": {KnowledgeLevel.OBSERVED, KnowledgeLevel.DERIVED},
    },
}


class PolicyEngine:
    """
    Deterministic governance layer. Governs all state transitions and permissions.
    Called by PatchValidator and by graph routing functions.
    """

    # ── Phase management ─────────────────────────────────────────────────────

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        "diagnosis": {"mitigation", "complete"},
        "mitigation": {"complete"},
        "complete": set(),
    }

    def can_transition_phase(self, current: str, proposed: str) -> bool:
        return proposed in self.ALLOWED_TRANSITIONS.get(current, set())

    def is_write_allowed(
        self,
        agent_role: str,
        knowledge_level: KnowledgeLevel,
        phase: str,
    ) -> bool:
        """Can `agent_role` write an entry with `knowledge_level` during `phase`?"""
        role_perms = WRITE_PERMISSIONS.get(agent_role, {})
        allowed = role_perms.get(phase, set())
        return knowledge_level in allowed

    # ── Freshness enforcement ─────────────────────────────────────────────────

    def expire_stale_observations(
        self,
        observations: list[Observation],
    ) -> tuple[list[Observation], list[str]]:
        """
        Remove observations whose TTL has elapsed.
        Returns (fresh_observations, expired_ids).
        """
        now = datetime.now(timezone.utc)
        fresh, expired = [], []
        for obs in observations:
            age = (now - obs.timestamp.replace(tzinfo=timezone.utc)).total_seconds()
            if age > obs.freshness_ttl_seconds:
                expired.append(obs.id)
                logger.debug("Expired observation %s (age=%.0fs)", obs.id, age)
            else:
                fresh.append(obs)
        return fresh, expired

    def get_active_observations(self, state: SREGraphState) -> list[Observation]:
        """Returns all non-stale observations from the state."""
        fresh, _ = self.expire_stale_observations(state.get("observations", []))
        return fresh

    # ── Budget enforcement ────────────────────────────────────────────────────

    def budget_status(self, state: SREGraphState) -> dict[str, Any]:
        token_pct = state.get("token_budget_remaining", 0) / max(
            state.get("flags", {}).get("token_budget_total", 150_000), 1
        )
        tool_pct = state.get("tool_calls_remaining", 0) / max(
            state.get("flags", {}).get("tool_calls_total", 80), 1
        )
        return {
            "exhausted": state.get("token_budget_remaining", 0) <= 0
            or state.get("tool_calls_remaining", 0) <= 0,
            "warning": token_pct < BUDGET_WARN_THRESHOLD or tool_pct < BUDGET_WARN_THRESHOLD,
            "token_pct": token_pct,
            "tool_pct": tool_pct,
        }

    def is_budget_exhausted(self, state: SREGraphState) -> bool:
        return self.budget_status(state)["exhausted"]

    # ── Diagnosis stopping conditions ─────────────────────────────────────────

    def should_submit_diagnosis(self, state: SREGraphState) -> tuple[bool, str]:
        """
        Returns (should_stop, reason).
        Stopping conditions:
            1. Budget exhausted → force submit with best available
            2. A VerifiedClaim exists → confident enough
            3. Confidence(H*) > threshold AND evidence ≥ 2 sources AND ≤ 1 strong competitor
        """
        if self.is_budget_exhausted(state):
            return True, "budget_exhausted"

        # Verified claim exists → strong stop
        if state.get("verified_claims"):
            return True, "verified_claim_exists"

        # Check top hypothesis
        active_hyps = [
            h for h in state.get("hypotheses", [])
            if h.status == HypothesisStatus.ACTIVE
        ]
        if not active_hyps:
            return False, "no_hypotheses_yet"

        top = max(active_hyps, key=lambda h: h.confidence)

        has_confidence = top.confidence >= DIAGNOSIS_CONFIDENCE_THRESHOLD
        has_evidence = len(top.supporting_evidence) >= DIAGNOSIS_MIN_EVIDENCE_SOURCES
        competitors = [
            h for h in active_hyps
            if h.id != top.id and h.confidence > 0.4
        ]
        few_competitors = len(competitors) <= DIAGNOSIS_MAX_UNRESOLVED_COMPETITORS

        if has_confidence and has_evidence and few_competitors:
            return True, "high_confidence_hypothesis"

        return False, "investigation_needed"

    # ── Rule compilation for prompt injection ─────────────────────────────────

    def compile_rules(self, agent_role: str, phase: str, state: SREGraphState) -> list[str]:
        """
        Return the deterministic rule set for this agent's prompt.
        These rules are injected verbatim into every prompt; they are not generated.
        """
        base_rules = [
            "Cite evidence IDs (e.g. E17, D4) for every claim you make.",
            "Do not treat hypotheses as verified facts.",
            "Identify contradictory evidence explicitly.",
            "Propose at least one falsification test for each hypothesis.",
            "Use the exact evidence IDs from the state — do not invent IDs.",
        ]

        phase_rules: dict[str, list[str]] = {
            "diagnosis": [
                "READ-ONLY: Do not modify, restart, or delete any system components.",
                "Your output MUST include at least one hypothesis with a causal path.",
                "Include confidence scores (0.0–1.0) for each hypothesis.",
            ],
            "mitigation": [
                "Propose specific, reversible actions where possible.",
                "Estimate risk (0.0–1.0) and recovery time (seconds) for each action.",
                "Do NOT execute actions directly — propose them for safety review.",
            ],
        }

        role_rules: dict[str, list[str]] = {
            "falsifier": [
                "Your goal is to DISPROVE hypotheses, not confirm them.",
                "For each hypothesis, ask: what would demonstrate it is WRONG?",
                "Run the most discriminating test you can find.",
            ],
            "primary_investigator": [
                "Perform a broad initial investigation.",
                "Generate competing hypotheses even when one seems obvious.",
                "Flag evidence gaps that specialists should fill.",
            ],
            "telemetry": [
                "Focus exclusively on logs and metrics.",
                "Look for anomaly patterns and timing correlations.",
            ],
            "topology": [
                "Focus on service dependencies, traces, and network paths.",
                "Identify which services in the call chain show anomalies.",
            ],
            "config_system": [
                "Focus on recent configuration changes, K8s resource state, and OS-level issues.",
                "Check for resource exhaustion, misconfigured limits, and deployment drift.",
            ],
        }

        rules = base_rules.copy()
        rules.extend(phase_rules.get(phase, []))
        rules.extend(role_rules.get(agent_role, []))

        budget = self.budget_status(state)
        if budget["warning"]:
            rules.append(
                f"⚠️ Budget warning: {budget['token_pct']*100:.0f}% tokens, "
                f"{budget['tool_pct']*100:.0f}% tool calls remaining. "
                "Prioritize the most discriminating actions."
            )

        return rules

    def compile_rules_for_agent(
        self,
        agent_role: str,
        phase: str,
        state: SREGraphState | None = None,
    ) -> list[str]:
        """Convenience alias for compile_rules supporting optional state."""
        if state is None:
            state = {  # type: ignore[typeddict-item]
                "token_budget_remaining": 100_000,
                "tool_calls_remaining": 50,
                "flags": {},
            }
        return self.compile_rules(agent_role, phase, state)
