"""
Multi-Agent Reliability and Robustness Metrics.

Beyond SREGym's standard operational metrics (DiagnosisAccuracy, MitigationSuccess, E2E, TTD, TTM, Tokens),
these metrics evaluate multi-agent reasoning health and model independence:
  1. Error Propagation Radius (EPR): fraction of downstream components corrupted by an injected upstream fault.
  2. Recovery Rate (RR): fraction of corrupted investigations that successfully self-correct.
  3. Communication Overhead (CO): tokens and messages expended exclusively on inter-agent coordination.
  4. Delegation Success (DS): fraction of inter-agent delegated tasks successfully completed.
  5. Model Gap (ModelGap): performance disparity between frontier and weak underlying LLMs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SREMultiAgentMetrics:
    """Calculates quantitative reliability and model independence metrics."""

    @staticmethod
    def error_propagation_radius(corrupted_nodes: int, reachable_nodes: int) -> float:
        """
        EPR = (downstream reasoning components corrupted) / (reachable components).
        A lower EPR indicates superior isolation against wrong hypotheses.
        """
        if reachable_nodes <= 0:
            return 0.0
        return min(1.0, max(0.0, corrupted_nodes / reachable_nodes))

    @staticmethod
    def recovery_rate(corrupted_runs: int, recovered_runs: int) -> float:
        """
        RR = (corrupted runs that self-correct to right diagnosis) / (total corrupted runs).
        A higher RR indicates superior falsification and contradiction clearance.
        """
        if corrupted_runs <= 0:
            return 1.0
        return min(1.0, max(0.0, recovered_runs / corrupted_runs))

    @staticmethod
    def communication_overhead(
        comm_tokens: int,
        message_count: int,
        bus_latency_ms: float = 0.0,
    ) -> dict[str, Any]:
        """
        CO = tokens_comm + messages + latency.
        Quantifies coordination efficiency.
        """
        return {
            "comm_tokens": comm_tokens,
            "message_count": message_count,
            "bus_latency_ms": bus_latency_ms,
            "tokens_per_message": (comm_tokens / message_count) if message_count > 0 else 0.0,
        }

    @staticmethod
    def delegation_success(successful_tasks: int, total_tasks: int) -> float:
        """
        DS = (successful delegated tasks) / (all delegated tasks).
        """
        if total_tasks <= 0:
            return 1.0
        return min(1.0, max(0.0, successful_tasks / total_tasks))

    @staticmethod
    def model_gap(frontier_accuracy: float, weak_accuracy: float) -> float:
        """
        ModelGap = Accuracy(Frontier) - Accuracy(Weak).
        Core research claim: ModelGap(M4) < ModelGap(M0).
        """
        return frontier_accuracy - weak_accuracy
