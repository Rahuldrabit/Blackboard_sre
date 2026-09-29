"""
Action Safety Rules — pre-execution blast radius filter and risk scoring.

Enforces:
  1. Blast radius bounds: blocks unauthorized deletions of critical namespaces or shared PVCs.
  2. Verified RCA requirement: destructive actions strictly require a VerifiedClaim.
  3. Action ranking by risk: J(a) = alpha*T_recovery + beta*Risk + gamma*Cost + delta*SLODamage.
"""

from __future__ import annotations

import logging
from typing import Any

from clients.graphstate.state.schema import Action, ActionType, VerifiedClaim

logger = logging.getLogger(__name__)


class ActionSafetyFilter:
    """Pre-execution safety gate for candidate mitigation actions."""

    FORBIDDEN_TARGETS = {"kube-system", "kube-public", "monitoring", "istio-system"}

    @classmethod
    def filter_safe_actions(
        cls,
        actions: list[Action],
        has_verified_rca: bool = False,
    ) -> tuple[list[Action], list[str]]:
        """
        Filters out dangerous or unverified actions.
        Returns: (safe_actions, rejected_reasons)
        """
        safe: list[Action] = []
        rejected: list[str] = []

        for a in actions:
            target = a.target_service.lower()

            # Target safety check
            if target in cls.FORBIDDEN_TARGETS:
                rejected.append(f"Action {a.id} rejected: Target '{target}' is a protected namespace.")
                continue

            # Destructive verification requirement
            if a.is_destructive and not has_verified_rca:
                rejected.append(
                    f"Action {a.id} rejected: Destructive action requires an accepted VerifiedClaim."
                )
                continue

            # Risk bound check
            if a.estimated_risk > 0.80 and not has_verified_rca:
                rejected.append(
                    f"Action {a.id} rejected: Risk {a.estimated_risk:.2f} too high without verified RCA."
                )
                continue

            safe.append(a)

        # Sort safe actions by risk score ascending (prefer least risky first)
        safe.sort(key=lambda a: a.score())
        return safe, rejected
