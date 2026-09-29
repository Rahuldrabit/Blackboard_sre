"""
Bounded Jev Decision Router.

Architectural Placement:
    Rules  -->  Jev  -->  LLM

Bounded Responsibilities:
1. Diagnostic test ranking: Utility(q) = #hypotheses_differentiated / Cost(q).
2. Agent routing: decides whether to continue investigation and which specialist to invoke.
3. Model tier routing: matches task complexity to model capability to optimize token budget.
4. Gating / stopping decisions.

CRITICAL INVARIANT:
Jev is NEVER asked to generate the root cause hypothesis.
It ranks and gates bounded candidate sets provided by rules and specialists.
"""

from __future__ import annotations

import logging
from typing import Any

from clients.graphstate.routing.adaptive_invoker import (
    AdaptiveAgentInvoker,
    UncertaintyLevel,
)
from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.schema import FalsificationTest, HypothesisStatus

logger = logging.getLogger(__name__)


class JevRouter:
    """Bounded decision engine for test ranking, model tiering, and agent routing."""

    def __init__(self, adaptive_invoker: AdaptiveAgentInvoker | None = None):
        self.invoker = adaptive_invoker or AdaptiveAgentInvoker()

    def rank_diagnostic_tests(
        self,
        tests: list[FalsificationTest],
    ) -> list[FalsificationTest]:
        """
        Rank candidate tests by utility:
            Utility(q) = # hypotheses differentiated / estimated cost
        """
        if not tests:
            return []
        return sorted(tests, key=lambda t: t.utility, reverse=True)

    def route_agent_invocation(self, state: SREGraphState) -> tuple[str, str]:
        """
        Decide next agent execution step based on incident uncertainty:
        Returns: (action: "submit" | "invoke_specialist" | "full_investigation", target_role_or_reason)
        """
        uncertainty = self.invoker.assess_uncertainty(state)

        if uncertainty == UncertaintyLevel.LOW:
            logger.info("JevRouter: Uncertainty is LOW (N=1). Ready for Diagnosis Gate.")
            return "submit", "low_uncertainty"

        if uncertainty == UncertaintyLevel.MODERATE:
            next_role = self.invoker.select_next_specialist(state)
            if next_role:
                logger.info("JevRouter: Uncertainty is MODERATE (N=2). Invoking specialist '%s'", next_role)
                return "invoke_specialist", next_role
            return "submit", "all_specialists_exhausted"

        # High uncertainty: full multi-agent triangulation
        logger.info("JevRouter: Uncertainty is HIGH (N=3). Triangulating across specialists.")
        return "full_investigation", "high_uncertainty"

    def select_model_tier(
        self,
        state: SREGraphState,
        task_type: str,
        default_model: str = "gemini-2.0-pro",
    ) -> str:
        """
        Selects model tier based on task criticality:
        - Routine parsing / triage: fast/cost-effective tier (e.g. gemini-2.0-flash)
        - Deep causal synthesis / cross-examination: frontier tier (e.g. gemini-2.0-pro)
        """
        uncertainty = self.invoker.assess_uncertainty(state)

        # High uncertainty or causal verification always uses frontier model
        if uncertainty == UncertaintyLevel.HIGH or task_type in ("synthesis", "cross_examination"):
            return default_model

        # Fast model for simple single-agent triage
        if "gemini" in default_model.lower():
            return "gemini-2.0-flash"
        return default_model
