"""
Falsification Engine — designs and evaluates tests that actively disprove hypotheses.

Core Principle:
  "What observation would make this hypothesis unlikely?"
  Directly attacks confirmation bias.
  Instead of looking for more logs that loosely match a hunch, the system
  generates discriminating diagnostic tests that would definitively eliminate
  a candidate hypothesis if false.
"""

from __future__ import annotations

import logging
from typing import Any

from clients.graphstate.state.schema import (
    FalsificationTest,
    Hypothesis,
    Observation,
)

logger = logging.getLogger(__name__)


class FalsificationEngine:
    """Generates discriminating tests and computes utility based on information gain / cost."""

    def __init__(self, cost_weight: float = 1.0):
        self.cost_weight = cost_weight
        self._next_test_id = 1

    def generate_tests_for_hypothesis(
        self,
        hypothesis: Hypothesis,
        available_tools: list[dict[str, Any]] | None = None,
    ) -> list[FalsificationTest]:
        """
        Formulates discriminating tests for a hypothesis based on its untested predictions
        and causal mechanism.
        """
        tests: list[FalsificationTest] = []
        target_service = hypothesis.affected_services[0] if hypothesis.affected_services else "unknown"

        # Check prediction 1: Connectivity / Network probe
        if "network" in hypothesis.claim.lower() or "connection" in hypothesis.claim.lower():
            test_id = f"T_FALS_{self._next_test_id:03d}"
            self._next_test_id += 1
            tests.append(
                FalsificationTest(
                    id=test_id,
                    target_hypothesis_id=hypothesis.id,
                    description=f"Probe TCP connection to {target_service} to test network reachability",
                    tool_name="probe_dependency",
                    tool_params={"source_service": "frontend", "target_service": target_service, "port": 80},
                    expected_if_hypothesis_true="Connection timed out or refused",
                    expected_if_hypothesis_false="Connection successful (SYN-ACK received)",
                    estimated_cost=1.0,
                    hypotheses_differentiated=2,
                )
            )

        # Check prediction 2: Pod state & OOM events
        if "memory" in hypothesis.claim.lower() or "oom" in hypothesis.claim.lower():
            test_id = f"T_FALS_{self._next_test_id:03d}"
            self._next_test_id += 1
            tests.append(
                FalsificationTest(
                    id=test_id,
                    target_hypothesis_id=hypothesis.id,
                    description=f"Inspect pod status and exit codes for {target_service} (ExitCode 137 = OOM)",
                    tool_name="inspect_resource_pressure",
                    tool_params={"service": target_service},
                    expected_if_hypothesis_true="OOMKilled event or ExitCode 137 found in last_state",
                    expected_if_hypothesis_false="Pod memory usage < 60% of limit, no OOM events",
                    estimated_cost=1.2,
                    hypotheses_differentiated=2,
                )
            )

        # Check prediction 3: Config drift / deployment changes
        if "config" in hypothesis.claim.lower() or "limit" in hypothesis.claim.lower():
            test_id = f"T_FALS_{self._next_test_id:03d}"
            self._next_test_id += 1
            tests.append(
                FalsificationTest(
                    id=test_id,
                    target_hypothesis_id=hypothesis.id,
                    description=f"Inspect recent manifest changes for {target_service}",
                    tool_name="get_recent_changes",
                    tool_params={"service": target_service, "since_minutes": 30},
                    expected_if_hypothesis_true="Manifest modified within 30 min before incident",
                    expected_if_hypothesis_false="No configuration changes in the last 24 hours",
                    estimated_cost=0.8,
                    hypotheses_differentiated=1,
                )
            )

        # Fallback general discriminating check
        if not tests:
            test_id = f"T_FALS_{self._next_test_id:03d}"
            self._next_test_id += 1
            tests.append(
                FalsificationTest(
                    id=test_id,
                    target_hypothesis_id=hypothesis.id,
                    description=f"Verify error logs on {target_service} matching claim",
                    tool_name="query_logs",
                    tool_params={"service_or_pod": target_service, "lines": 50},
                    expected_if_hypothesis_true="Explicit error logs matching causal claim",
                    expected_if_hypothesis_false="Clean logs without fatal exceptions",
                    estimated_cost=1.5,
                    hypotheses_differentiated=1,
                )
            )

        return tests

    def rank_tests(self, tests: list[FalsificationTest]) -> list[FalsificationTest]:
        """
        Rank candidate diagnostic tests by utility:
            Utility(q) = # hypotheses differentiated / estimated cost
        """
        return sorted(tests, key=lambda t: t.utility, reverse=True)

    def evaluate_test_outcome(
        self,
        test: FalsificationTest,
        observation: Observation,
    ) -> tuple[str, float]:
        """
        Evaluates whether the observation disproved, supported, or was inconclusive.
        Returns: (outcome: "disproved" | "supported" | "inconclusive", confidence_delta)
        """
        obs_text = (observation.summary + " " + (observation.raw_data or "")).lower()

        # If counter-evidence observation matches expected_if_hypothesis_false:
        false_clues = test.expected_if_hypothesis_false.lower().split()
        true_clues = test.expected_if_hypothesis_true.lower().split()

        # Check for counter-evidence keywords
        counter_matches = sum(1 for word in false_clues if len(word) > 3 and word in obs_text)
        support_matches = sum(1 for word in true_clues if len(word) > 3 and word in obs_text)

        if counter_matches > support_matches and counter_matches >= 2:
            logger.info("Test %s successfully FALSIFIED hypothesis %s", test.id, test.target_hypothesis_id)
            return "disproved", -0.40
        elif support_matches > counter_matches and support_matches >= 2:
            logger.info("Test %s reinforced hypothesis %s", test.id, test.target_hypothesis_id)
            return "supported", +0.15
        else:
            return "inconclusive", 0.0
