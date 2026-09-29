"""
Primary Investigator — broad triage and initial incident hypothesis generator.

Used as the baseline investigator (S0 single-agent) and the initial phase in multi-agent routing.
"""

from __future__ import annotations

from typing import Any

from clients.graphstate.agents.base import BaseSpecialist


class PrimaryInvestigator(BaseSpecialist):
    """Broad-spectrum SRE diagnostic investigator."""

    def __init__(self, **kwargs: Any):
        super().__init__(role="primary_investigator", **kwargs)

    def get_task_description(self) -> str:
        return (
            "Perform initial incident triage. Observe the overall health of the microservice cluster, "
            "identify anomalous metrics, error spikes, or degraded pods, and formulate initial root cause hypotheses."
        )
