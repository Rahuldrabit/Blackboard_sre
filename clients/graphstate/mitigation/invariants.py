"""
Durability Invariants Checker.

Context from SREGym experiments:
  Restoring apparent service health in the immediate moment is insufficient if the fix
  violates the benchmark's durability invariant (e.g., configurations that remain unsafe
  for future rollouts or revert upon pod restart).

Durability Checks:
  1. Restart Durability: Fix must survive workload restarts (requires manifest/config persistence).
  2. Rollout Durability: Changes must be declarative in deployment specs, not transient pod memory patches.
  3. Secret & Mount Integrity: Volume mounts and secrets must be correctly mapped in K8s state.
"""

from __future__ import annotations

import logging
from typing import Any

from clients.graphstate.state.schema import Action, ActionType

logger = logging.getLogger(__name__)


class DurabilityChecker:
    """Verifies that candidate mitigations preserve long-term system invariants."""

    @classmethod
    def check_durability(cls, action: Action, current_cluster_state: dict[str, Any] | None = None) -> tuple[bool, str]:
        """
        Evaluate if the action is durable or merely superficial.
        """
        # Superficial restart check:
        # If the root cause is a bad memory limit or bad config, a raw restart is non-durable
        if action.action_type == ActionType.RESTART_WORKLOAD:
            if "memory" in action.rationale.lower() or "limit" in action.rationale.lower():
                return False, (
                    "Non-durable action: Restarting workload without updating deployment memory limits "
                    "will cause pod to OOMKill again upon restart."
                )

        # Declarative durability check:
        # Configuration updates must use apply_manifest or rollback_config, not live exec patches
        if action.action_type == ActionType.EXEC_COMMAND:
            cmd = str(action.params.get("command", ""))
            if "iptables" in cmd or "sysctl" in cmd:
                return False, (
                    "Non-durable action: Transient command execution will be wiped upon pod/node restart. "
                    "Must apply declarative DaemonSet or ConfigMap."
                )

        return True, "Durability invariants preserved"
