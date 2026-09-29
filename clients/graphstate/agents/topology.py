"""
Agent A — Topology and Trace Specialist.

Focuses on:
  - Service dependency graph
  - Request propagation and trace spans
  - Network path bottlenecks and cascading failures

Diagnostic Questions:
  - Where does latency or error first appear in the call chain?
  - Which dependency path is affected?
  - Which downstream or upstream service explains the failure?
  - Is there a propagation cascade?
"""

from __future__ import annotations

import json
from typing import Any

from clients.graphstate.agents.base import BaseSpecialist


class TopologySpecialist(BaseSpecialist):
    """Specialist for service graphs, trace latency, and call-tree propagation."""

    def __init__(self, **kwargs: Any):
        super().__init__(role="topology", **kwargs)

    def get_task_description(self) -> str:
        return (
            "Analyze service dependencies, distributed traces, and request propagation. "
            "Identify where in the call chain errors or latency first manifest, and whether "
            "an upstream or downstream dependency is the causal origin."
        )

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is not None:
            return await super()._call_llm(prompt)

        # Deterministic domain-specific mock for offline testing
        return json.dumps({
            "new_observations": [
                {
                    "source": "topology",
                    "service": "frontend",
                    "observation_type": "trace",
                    "summary": "Distributed trace shows 92% of frontend request latency spent blocked on cartservice",
                    "raw_data": "trace_id=4bf92f3577b34da6 span=frontend->cartservice duration=4120ms status=500",
                }
            ],
            "new_hypotheses": [
                {
                    "claim": "Cartservice dependency failure propagating latency to frontend",
                    "affected_services": ["frontend", "cartservice"],
                    "causal_path": ["cartservice", "frontend"],
                    "supporting_evidence": ["E001"],
                    "contradictory_evidence": [],
                    "untested_predictions": ["cartservice CPU or backend DB connectivity failure"],
                    "confidence": 0.82,
                }
            ],
            "hypothesis_updates": [],
            "new_tasks": [
                {
                    "task_type": "inspect_system_config",
                    "assignee_role": "config_system",
                    "description": "Inspect cartservice deployment, pod restart count, and PVC status",
                    "parameters": {"service": "cartservice"},
                }
            ],
            "tool_calls_requested": [],
        })
