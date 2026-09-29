"""
Agent B — Telemetry Specialist (Logs, Metrics, Resource Behavior).

Focuses on:
  - Container logs and error messages
  - Prometheus time-series metrics (CPU, Memory, Latency, Error Rate)
  - Anomaly detection (>3σ departures from baseline)

Diagnostic Questions:
  - What changed in the metrics around incident onset?
  - Which telemetry signals are anomalous vs normal?
  - Which anomaly preceded the incident?
  - Is the anomaly causal or merely secondary noise?
"""

from __future__ import annotations

import json
from typing import Any

from clients.graphstate.agents.base import BaseSpecialist


class TelemetrySpecialist(BaseSpecialist):
    """Specialist for container logs, Prometheus metrics, and resource saturation."""

    def __init__(self, **kwargs: Any):
        super().__init__(role="telemetry", **kwargs)

    def get_task_description(self) -> str:
        return (
            "Analyze logs, Prometheus metrics, and resource behavior. "
            "Identify metric anomalies that preceded or accompanied the failure, "
            "and correlate error log patterns to isolate the root cause mechanism."
        )

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is not None:
            return await super()._call_llm(prompt)

        # Deterministic domain-specific mock for offline testing
        return json.dumps({
            "new_observations": [
                {
                    "source": "telemetry",
                    "service": "cartservice",
                    "observation_type": "log",
                    "summary": "cartservice logs show repeated StackOverflowError in GC thread @ 14:02:11",
                    "raw_data": "FATAL: java.lang.OutOfMemoryError: Java heap space [cartservice-6d8b9f-xkz9]",
                }
            ],
            "new_hypotheses": [
                {
                    "claim": "Cartservice JVM memory exhaustion (heap space OOM)",
                    "affected_services": ["cartservice"],
                    "causal_path": ["cartservice"],
                    "supporting_evidence": ["E002"],
                    "contradictory_evidence": [],
                    "untested_predictions": ["Pod memory limit was recently reduced or traffic burst"],
                    "confidence": 0.88,
                }
            ],
            "hypothesis_updates": [],
            "new_tasks": [],
            "tool_calls_requested": [],
        })
