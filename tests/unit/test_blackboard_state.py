"""
Unit tests for Secure Blackboard state, knowledge levels, patch validation,
query caching, policy engine, and prompt compiler.
"""

from datetime import datetime, timezone
import pytest

from clients.graphstate.state.knowledge_levels import KnowledgeLevel
from clients.graphstate.state.graph_state import initial_state
from clients.graphstate.state.schema import (
    Observation,
    ObservationType,
    Hypothesis,
    HypothesisStatus,
    VerifiedClaim,
    Action,
    ActionType,
    Task,
    Provenance,
)
from clients.graphstate.state.patch import StatePatch, ValidationResult
from clients.graphstate.state.patch_validator import PatchValidator
from clients.graphstate.state.query_cache import QueryCache
from clients.graphstate.policy.engine import PolicyEngine
from clients.graphstate.policy.prompt_compiler import PromptCompiler


def test_knowledge_level_invariants():
    """Verify epistemic rank and transition permissions."""
    assert KnowledgeLevel.OBSERVED.rank < KnowledgeLevel.VERIFIED.rank
    assert KnowledgeLevel.HYPOTHESIZED.rank < KnowledgeLevel.VERIFIED.rank

    # Investigators cannot transition to VERIFIED
    assert not KnowledgeLevel.HYPOTHESIZED.can_transition_to(
        KnowledgeLevel.VERIFIED, by_role="telemetry"
    )
    assert not KnowledgeLevel.HYPOTHESIZED.can_transition_to(
        KnowledgeLevel.VERIFIED, by_role="primary_investigator"
    )

    # Verifier or gate can transition to VERIFIED
    assert KnowledgeLevel.HYPOTHESIZED.can_transition_to(
        KnowledgeLevel.VERIFIED, by_role="verifier"
    )
    assert KnowledgeLevel.HYPOTHESIZED.can_transition_to(
        KnowledgeLevel.VERIFIED, by_role="diagnosis_gate"
    )


def test_patch_validator_blocks_action_in_diagnosis():
    """Verify that destructive or mitigation actions are blocked during diagnosis."""
    state = initial_state(
        incident_id="inc-123",
        problem_id="prob-456",
        enabled_stages=["diagnosis"],
        flags={},
    )
    validator = PatchValidator()

    action = Action(
        id="act-1",
        action_type=ActionType.RESTART_WORKLOAD,
        target_service="cartservice",
        is_destructive=False,
        proposed_by="telemetry",
        rationale="Restart cartservice to clear state",
    )

    patch = StatePatch(
        agent_id="agent-telemetry-1",
        agent_role="telemetry",
        proposed_actions=[action],
    )

    result = validator.validate(patch, state, agent_role="telemetry")
    assert not result.valid
    assert any("diagnosis" in err.lower() for err in result.errors)


def test_patch_validator_blocks_self_promotion_to_verified():
    """Verify that investigator cannot write verified claims directly."""
    state = initial_state(
        incident_id="inc-123",
        problem_id="prob-456",
        enabled_stages=["diagnosis"],
        flags={},
    )
    validator = PatchValidator()

    # Attempt to submit a verified claim as an investigator
    patch = StatePatch(
        agent_id="agent-telemetry-1",
        agent_role="telemetry",
        new_hypotheses=[
            Hypothesis(
                id="H1",
                timestamp=datetime.now(timezone.utc),
                author_agent="telemetry",
                claim="Redis OOM error",
                affected_services=["redis"],
                causal_path=["redis"],
                supporting_evidence=["E1"],
                contradictory_evidence=[],
                untested_predictions=[],
                confidence=0.9,
                status=HypothesisStatus.VERIFIED,  # Illegal self-promotion!
            )
        ],
    )

    result = validator.validate(patch, state, agent_role="telemetry")
    assert not result.valid
    assert any("verified" in err.lower() for err in result.errors)


def test_query_cache_deduplication():
    """Verify query caching and hit count tracking."""
    cache = QueryCache(default_ttl_seconds=60)
    params = {"namespace": "default", "label": "app=frontend"}

    assert cache.get("kubectl_get_pods", params) is None
    assert cache.stats["misses"] == 1

    cache.set("kubectl_get_pods", params, {"pods": ["frontend-pod-1"]})
    cached = cache.get("kubectl_get_pods", params)

    assert cached == {"pods": ["frontend-pod-1"]}
    assert cache.stats["hits"] == 1
    assert cache.stats["hit_ratio"] == 0.5


def test_policy_engine_freshness():
    """Verify policy engine marks stale observations."""
    engine = PolicyEngine()
    now = datetime.now(timezone.utc)

    fresh_obs = Observation(
        id="E1",
        timestamp=now,
        source="tool",
        author_agent="telemetry",
        observation_type=ObservationType.METRIC,
        summary="CPU load normal",
        freshness_ttl_seconds=300,
    )

    stale_time = datetime.fromtimestamp(now.timestamp() - 600, tz=timezone.utc)
    stale_obs = Observation(
        id="E2",
        timestamp=stale_time,
        source="tool",
        author_agent="telemetry",
        observation_type=ObservationType.METRIC,
        summary="Old memory reading",
        freshness_ttl_seconds=300,
    )

    state = initial_state(
        incident_id="inc-1",
        problem_id="prob-1",
        enabled_stages=["diagnosis"],
        flags={},
    )
    state["observations"] = [fresh_obs, stale_obs]

    active_obs = engine.get_active_observations(state)
    assert len(active_obs) == 1
    assert active_obs[0].id == "E1"


def test_stateless_prompt_compiler():
    """Verify prompt compiler generates structured, history-free prompts."""
    compiler = PromptCompiler()
    obs = Observation(
        id="E14",
        timestamp=datetime.now(timezone.utc),
        source="prometheus",
        author_agent="telemetry",
        observation_type=ObservationType.METRIC,
        summary="checkoutservice error rate spiked to 45%",
    )

    state_view = {
        "observations": [obs],
        "derived_facts": [],
        "hypotheses": [],
        "service_graph": {"services": [{"name": "checkoutservice"}], "dependencies": []},
    }

    rules = [
        "Cite evidence IDs for all claims.",
        "Read-only operations only.",
    ]

    tools = [{"name": "query_metrics", "description": "Query Prometheus time-series metrics"}]

    prompt = compiler.compile(
        role="telemetry",
        task="Identify telemetry anomalies and formulate hypotheses",
        state_view=state_view,
        rules=rules,
        tools=tools,
    )

    assert "# ROLE DEFINITION" in prompt
    assert "Investigator B (Telemetry Specialist)" in prompt
    assert "checkoutservice error rate spiked to 45%" in prompt
    assert "Cite evidence IDs for all claims." in prompt
    assert "query_metrics" in prompt
    assert "# OUTPUT SPECIFICATION" in prompt
