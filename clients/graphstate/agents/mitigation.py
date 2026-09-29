"""
Agent D — Mitigation Specialist.

Activates ONLY AFTER the Diagnosis Gate has approved the root cause diagnosis.
Does not independently redo the entire investigation.

Receives:
  - verified evidence (VERIFIED claims)
  - accepted diagnosis
  - rejected hypotheses
  - durability invariants
  - allowed actions

Proposes candidate recovery actions (rollback_config, update_secret, restart_workload, apply_manifest)
with estimated risk, recovery time, and reversibility.
Does NOT directly execute actions — proposes them for TNR safety review.
"""

from __future__ import annotations

import json
from typing import Any

from clients.graphstate.agents.base import BaseSpecialist


class MitigationAgent(BaseSpecialist):
    """Specialist proposing safe recovery actions preserving durability invariants."""

    def __init__(self, **kwargs: Any):
        super().__init__(role="mitigation_planner", **kwargs)

    def get_task_description(self) -> str:
        return (
            "Formulate safe, minimal, and reversible mitigation actions to resolve the accepted diagnosis. "
            "For each proposed action, specify the target resource, action type, reversibility, and "
            "ensure durability invariants are preserved so the fix survives future rollouts and restarts."
        )

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is not None:
            return await super()._call_llm(prompt)

        # Deterministic domain-specific mock for offline testing
        return json.dumps({
            "new_observations": [
                {
                    "source": "mitigation_planner",
                    "service": "cartservice",
                    "observation_type": "config",
                    "summary": "Formulated manifest update to restore cartservice container memory limit to 512Mi",
                    "raw_data": "kubectl set resources deployment cartservice --limits=memory=512Mi",
                }
            ],
            "new_hypotheses": [],
            "hypothesis_updates": [],
            "new_tasks": [],
            "proposed_actions": [
                {
                    "id": "ACT_001",
                    "action_type": "apply_manifest",
                    "target_service": "cartservice",
                    "target_resource": "deployment/cartservice",
                    "params": {"memory_limit": "512Mi", "namespace": "default"},
                    "is_destructive": False,
                    "requires_verified_rca": True,
                    "estimated_recovery_time": 45.0,
                    "estimated_risk": 0.1,
                    "proposed_by": "mitigation_planner",
                    "rationale": "Restoring memory limit to 512Mi eliminates JVM heap space OOM and pod crash loops",
                }
            ],
            "tool_calls_requested": [],
        })
