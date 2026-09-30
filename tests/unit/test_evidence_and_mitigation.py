"""
Unit tests for HypothesisManager, FalsificationEngine, CausalVerifier,
DiagnosisGate, TNR Mitigation, and Fault Injection Metrics.
"""

from datetime import datetime, timezone
import pytest

from clients.graphstate.evidence.causal_verifier import CausalVerifier
from clients.graphstate.evidence.diagnosis_gate import DiagnosisGate
from clients.graphstate.evidence.falsification import FalsificationEngine
from clients.graphstate.evidence.hypothesis_manager import HypothesisManager
from clients.graphstate.fault_injection.injector import CommunicationFaultInjector
from clients.graphstate.mitigation.invariants import DurabilityChecker
from clients.graphstate.mitigation.safety_rules import ActionSafetyFilter
from clients.graphstate.mitigation.tnr import TestAndRollback
from clients.graphstate.routing.adaptive_invoker import AdaptiveAgentInvoker, UncertaintyLevel
from clients.graphstate.routing.jev_router import JevRouter
from clients.graphstate.evaluation.metrics import SREMultiAgentMetrics
from clients.graphstate.state.graph_state import initial_state
from clients.graphstate.state.schema import (
    Action,
    ActionType,
    Hypothesis,
    HypothesisStatus,
    Observation,
    ObservationType,
)


def test_hypothesis_scoring_and_expansion_trigger():
    """Verify HypothesisManager scoring and expansion when max score < tau."""
    mgr = HypothesisManager(expansion_threshold=0.40)
    state = initial_state("inc", "prob", ["diagnosis"], {})

    # Weak hypothesis with no evidence
    weak_h = Hypothesis(
        id="H_weak",
        timestamp=datetime.now(timezone.utc),
        author_agent="primary",
        claim="Random guess claim",
        affected_services=["frontend"],
        causal_path=["frontend"],
        supporting_evidence=[],
        contradictory_evidence=["E_contra_1"],
        untested_predictions=[],
        confidence=0.30,
    )
    state["hypotheses"].append(weak_h)

    # Score should be low
    score = mgr.score_hypothesis(weak_h, state)
    assert score < 0.40

    # Expansion must trigger because top score < tau
    should_expand, reason = mgr.should_trigger_expansion(state, tau=0.40)
    assert should_expand
    assert "below_tau" in reason


def test_falsification_test_generation_and_utility_ranking():
    """Verify FalsificationEngine generates and utility-ranks tests."""
    engine = FalsificationEngine()
    hyp = Hypothesis(
        id="H_mem",
        timestamp=datetime.now(timezone.utc),
        author_agent="telemetry",
        claim="cartservice pod memory limit exceeded OOMKill",
        affected_services=["cartservice"],
        causal_path=["cartservice"],
        supporting_evidence=["E1"],
        contradictory_evidence=[],
        untested_predictions=[],
        confidence=0.85,
    )

    tests = engine.generate_tests_for_hypothesis(hyp)
    assert len(tests) > 0
    assert any("OOM" in t.description or "memory" in t.description for t in tests)

    ranked = engine.rank_tests(tests)
    assert len(ranked) == len(tests)
    assert ranked[0].utility >= ranked[-1].utility


def test_causal_verifier_four_checks():
    """Verify CausalVerifier executes structural, temporal, statistical, and sufficiency checks."""
    verifier = CausalVerifier(min_evidence_count=2)
    state = initial_state("inc", "prob", ["diagnosis"], {})
    state["service_graph"] = {
        "services": [{"name": "cartservice"}, {"name": "frontend"}],
        "dependencies": [{"from": "frontend", "to": "cartservice"}],
    }

    obs1 = Observation(
        id="E1",
        timestamp=datetime.now(timezone.utc),
        source="tool",
        author_agent="telemetry",
        observation_type=ObservationType.METRIC,
        summary="cartservice memory spike error > 3sigma",
        anomaly_score=0.92,
    )
    obs2 = Observation(
        id="E2",
        timestamp=datetime.now(timezone.utc),
        source="tool",
        author_agent="config",
        observation_type=ObservationType.CONFIG,
        summary="cartservice container limit error: 64Mi",
    )
    state["observations"] = [obs1, obs2]

    # Hypothesis with both evidence IDs and valid path
    valid_hyp = Hypothesis(
        id="H_valid",
        timestamp=datetime.now(timezone.utc),
        author_agent="telemetry",
        claim="cartservice OOM error",
        affected_services=["cartservice"],
        causal_path=["cartservice"],
        supporting_evidence=["E1", "E2"],
        contradictory_evidence=[],
        untested_predictions=[],
        confidence=0.88,
    )

    result = verifier.verify_hypothesis(valid_hyp, state)
    assert result.passed
    assert result.verified_claim is not None
    assert result.verified_claim.knowledge_level.value == "verified"


def test_causal_verifier_accepts_direct_kubernetes_configuration_evidence():
    verifier = CausalVerifier(min_evidence_count=2)
    state = initial_state("inc", "prob", ["diagnosis"], {})
    state["observations"] = [
        Observation(id="E1", timestamp=datetime.now(timezone.utc), source="exec_kubectl_cmd_safely",
                    author_agent="config_system", observation_type=ObservationType.CONFIG,
                    summary="NetworkPolicy manifest", tool_name="exec_kubectl_cmd_safely"),
        Observation(id="E2", timestamp=datetime.now(timezone.utc), source="exec_kubectl_cmd_safely",
                    author_agent="config_system", observation_type=ObservationType.CONFIG,
                    summary="Pod and service selectors", tool_name="exec_kubectl_cmd_safely"),
    ]
    hypothesis = Hypothesis(
        id="H_config", timestamp=datetime.now(timezone.utc), author_agent="config_system",
        claim="NetworkPolicy blocks service traffic", affected_services=["frontend"],
        causal_path=["frontend"], supporting_evidence=["E1", "E2"],
        contradictory_evidence=[], untested_predictions=[], confidence=0.9,
    )
    assert verifier.verify_hypothesis(hypothesis, state).passed


def test_diagnosis_gate_enforcement():
    """Verify DiagnosisGate blocks premature submission and approves verified RCA."""
    gate = DiagnosisGate()
    state = initial_state("inc", "prob", ["diagnosis"], {})

    # 1. State with unverified, conflicted hypothesis -> Rejected!
    bad_h = Hypothesis(
        id="H_bad",
        timestamp=datetime.now(timezone.utc),
        author_agent="telemetry",
        claim="Bad claim",
        affected_services=["cartservice"],
        causal_path=["cartservice"],
        supporting_evidence=["E1"],  # only 1 source (< 2)
        contradictory_evidence=["E_contra_1"],  # has contradiction!
        untested_predictions=[],
        confidence=0.60,
    )
    state["hypotheses"].append(bad_h)

    res1 = gate.evaluate_submission(state)
    assert not res1.approved
    assert len(res1.reasons) > 0

    # 2. State with verified hypothesis and clean evidence -> Approved!
    obs1 = Observation(
        id="E1",
        timestamp=datetime.now(timezone.utc),
        source="tool",
        author_agent="telemetry",
        observation_type=ObservationType.METRIC,
        summary="cartservice error spike",
    )
    obs2 = Observation(
        id="E2",
        timestamp=datetime.now(timezone.utc),
        source="tool",
        author_agent="config",
        observation_type=ObservationType.CONFIG,
        summary="cartservice limit error",
    )
    state["observations"] = [obs1, obs2]

    good_h = Hypothesis(
        id="H_good",
        timestamp=datetime.now(timezone.utc),
        author_agent="telemetry",
        claim="cartservice memory limit misconfigured",
        affected_services=["cartservice"],
        causal_path=["cartservice"],
        supporting_evidence=["E1", "E2"],
        contradictory_evidence=[],
        untested_predictions=[],
        confidence=0.92,
    )
    state["hypotheses"] = [good_h]

    res2 = gate.evaluate_submission(state)
    assert res2.approved
    assert res2.diagnosis is not None
    assert res2.diagnosis.root_cause == good_h.claim


@pytest.mark.asyncio
async def test_tnr_rollback_on_regression():
    """Verify TestAndRollback rolls back snapshot when regression occurs."""
    tnr = TestAndRollback()
    state = initial_state("inc", "prob", ["mitigation"], {})

    act = Action(
        id="ACT_test",
        action_type=ActionType.APPLY_MANIFEST,
        target_service="cartservice",
        is_destructive=False,
        proposed_by="mitigation",
        rationale="Update container spec",
    )

    # Simulate regression during execution
    result = await tnr.execute_action(act, state, simulate_regression=True)

    assert not result.committed
    assert result.rolled_back
    assert result.regression_detected
    assert result.status == "rolled_back"


@pytest.mark.asyncio
async def test_tnr_commit_on_successful_mitigation():
    """Verify TestAndRollback commits when health is restored."""
    tnr = TestAndRollback()
    state = initial_state("inc", "prob", ["mitigation"], {})

    act = Action(
        id="ACT_good",
        action_type=ActionType.APPLY_MANIFEST,
        target_service="cartservice",
        is_destructive=False,
        proposed_by="mitigation",
        rationale="Update container spec to valid 512Mi",
    )

    # Clean execution without regression
    result = await tnr.execute_action(act, state, simulate_regression=False)

    assert result.committed
    assert not result.rolled_back
    assert result.status == "committed"


def test_durability_checker_blocks_superficial_fix():
    """Verify DurabilityChecker rejects restarts for config/memory faults."""
    action = Action(
        id="ACT_restart",
        action_type=ActionType.RESTART_WORKLOAD,
        target_service="cartservice",
        is_destructive=False,
        proposed_by="agent",
        rationale="Restart cartservice to temporarily relieve memory pressure",
    )

    durable, reason = DurabilityChecker.check_durability(action)
    assert not durable
    assert "Non-durable" in reason


def test_adaptive_invoker_and_metrics():
    """Verify AdaptiveAgentInvoker levels and SREMultiAgentMetrics calculations."""
    invoker = AdaptiveAgentInvoker()
    state = initial_state("inc", "prob", ["diagnosis"], {})

    # Low uncertainty
    state["hypotheses"].append(
        Hypothesis(
            id="H1",
            timestamp=datetime.now(timezone.utc),
            author_agent="telemetry",
            claim="Verified cause",
            affected_services=["cart"],
            causal_path=["cart"],
            supporting_evidence=["E1", "E2"],
            contradictory_evidence=[],
            untested_predictions=[],
            confidence=0.92,
        )
    )
    assert invoker.assess_uncertainty(state) == UncertaintyLevel.LOW

    # EPR and ModelGap metrics
    epr = SREMultiAgentMetrics.error_propagation_radius(corrupted_nodes=1, reachable_nodes=5)
    assert epr == 0.20

    rr = SREMultiAgentMetrics.recovery_rate(corrupted_runs=10, recovered_runs=8)
    assert rr == 0.80

    gap = SREMultiAgentMetrics.model_gap(frontier_accuracy=0.85, weak_accuracy=0.60)
    assert abs(gap - 0.25) < 1e-5
