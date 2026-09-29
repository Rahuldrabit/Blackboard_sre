"""
Falsifier Agent — actively challenges and attempts to disprove candidate hypotheses.

Core Mission:
  Confirmation bias is the #1 reason LLM multi-agent systems fail on difficult SREGym faults.
  The Falsifier agent does NOT search for supportive clues.
  Instead, it asks: "What observation would make this candidate hypothesis FALSE?"
  It designs discriminating tests to eliminate incorrect hypotheses from the active set.
"""

from __future__ import annotations

import json
from typing import Any

from clients.graphstate.agents.base import BaseSpecialist


class FalsifierAgent(BaseSpecialist):
    """Specialist attacking candidate hypotheses with counter-evidence and discriminating tests."""

    def __init__(self, **kwargs: Any):
        super().__init__(role="falsifier", **kwargs)

    def get_task_description(self) -> str:
        return (
            "Critique and actively challenge the leading candidate hypotheses. "
            "For each hypothesis, identify its untested predictions and formulate "
            "the most discriminating diagnostic test that would disprove it if incorrect."
        )

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is not None:
            return await super()._call_llm(prompt)

        # Deterministic mock for offline testing
        return json.dumps({
            "new_observations": [
                {
                    "source": "falsifier",
                    "service": "cartservice",
                    "observation_type": "metric",
                    "summary": "Verified: redis backend latency is 0.8ms (normal), disproving external redis network partition",
                    "raw_data": "redis_ping_latency_ms: 0.8 (p99)",
                }
            ],
            "new_hypotheses": [],
            "hypothesis_updates": [
                {
                    "hypothesis_id": "H_top_1",
                    "confidence_delta": -0.20,
                    "new_supporting_evidence": [],
                    "new_contradictory_evidence": ["E_fals_1"],
                    "new_untested_predictions": [],
                    "reason": "Redis latency is normal, disproving redis network partition hypothesis",
                }
            ],
            "new_tasks": [],
            "tool_calls_requested": [],
        })
