"""
Unit tests for the Three Independent Investigators (Step 4) and
the Typed Communication Bus (Step 5).
"""

from datetime import datetime, timezone
import pytest

from clients.graphstate.agents.config_system import ConfigSystemSpecialist
from clients.graphstate.agents.telemetry import TelemetrySpecialist
from clients.graphstate.agents.topology import TopologySpecialist
from clients.graphstate.bus.message_bus import AgentCommunicationBus
from clients.graphstate.bus.message_types import BusMessage, MessageType
from clients.graphstate.state.graph_state import initial_state
from clients.graphstate.state.schema import Hypothesis, HypothesisStatus, Observation, ObservationType


@pytest.mark.asyncio
async def test_blind_investigation_phase_hides_other_hypotheses():
    """Verify that in blind mode, agents CANNOT see other agents' hypotheses."""
    state = initial_state(
        incident_id="inc-blind",
        problem_id="prob-blind",
        enabled_stages=["diagnosis"],
        flags={},
    )

    # Pre-populate state with a hypothesis from Agent A (topology)
    hyp_a = Hypothesis(
        id="H_top_1",
        timestamp=datetime.now(timezone.utc),
        author_agent="topology",
        claim="Network ingress drop",
        affected_services=["frontend"],
        causal_path=["frontend"],
        supporting_evidence=["E1"],
        contradictory_evidence=[],
        untested_predictions=[],
        confidence=0.8,
    )
    state["hypotheses"].append(hyp_a)

    agent_b = TelemetrySpecialist()
    agent_c = ConfigSystemSpecialist()

    # Blind mode state views
    view_b = agent_b.get_state_view(state, blind_mode=True)
    view_c = agent_c.get_state_view(state, blind_mode=True)

    # In blind mode, Agent B and Agent C must NOT see Agent A's hypothesis!
    assert len(view_b["hypotheses"]) == 0
    assert len(view_c["hypotheses"]) == 0

    # In cross-examination mode (blind_mode=False), all hypotheses are visible
    cross_exam_b = agent_b.get_state_view(state, blind_mode=False)
    assert len(cross_exam_b["hypotheses"]) == 1
    assert cross_exam_b["hypotheses"][0].id == "H_top_1"


@pytest.mark.asyncio
async def test_three_independent_investigators_execution():
    """Verify all 3 specialists run and formulate distinct domain-specific hypotheses."""
    state = initial_state(
        incident_id="inc-multi",
        problem_id="prob-multi",
        enabled_stages=["diagnosis"],
        flags={},
    )

    agent_a = TopologySpecialist()
    agent_b = TelemetrySpecialist()
    agent_c = ConfigSystemSpecialist()

    # Run in blind mode
    state = await agent_a.run(state, blind_mode=True)
    state = await agent_b.run(state, blind_mode=True)
    state = await agent_c.run(state, blind_mode=True)

    assert len(state["hypotheses"]) == 3
    authors = {h.author_agent for h in state["hypotheses"]}
    assert authors == {"topology", "telemetry", "config_system"}


def test_bus_task_request_and_delegation():
    """Verify TASK_REQUEST creates pending task and enforces delegation limit."""
    bus = AgentCommunicationBus()
    state = initial_state(
        incident_id="inc-bus",
        problem_id="prob-bus",
        enabled_stages=["diagnosis"],
        flags={},
    )

    msg = bus.create_message(
        message_type=MessageType.TASK_REQUEST,
        sender_role="topology",
        target_role="config_system",
        payload={
            "task_type": "check_resource_limits",
            "description": "Inspect cartservice deployment memory limits",
            "parameters": {"service": "cartservice"},
        },
        delegation_depth=1,
    )

    result = bus.dispatch(msg, state)
    assert result.success
    assert len(state["pending_tasks"]) == 1
    assert state["pending_tasks"][0].assignee_role == "config_system"
    assert state["pending_tasks"][0].delegation_depth == 1


def test_bus_blocks_circular_delegation():
    """Verify bus rejects messages exceeding MAX_DELEGATION_DEPTH."""
    bus = AgentCommunicationBus()
    state = initial_state("inc", "prob", ["diagnosis"], {})

    excessive_msg = bus.create_message(
        message_type=MessageType.TASK_REQUEST,
        sender_role="topology",
        target_role="config_system",
        payload={"task_type": "recursive_check"},
        delegation_depth=4,  # Exceeds max depth 3!
    )

    result = bus.dispatch(excessive_msg, state)
    assert not result.success
    assert any("MAX_DELEGATION_DEPTH" in err for err in result.errors)


def test_bus_enforces_evidence_grounding_and_blocks_persuasion():
    """Verify bus rejects ungrounded claims and conversational persuasion."""
    bus = AgentCommunicationBus()
    state = initial_state("inc", "prob", ["diagnosis"], {})

    # Missing evidence reference for a challenge
    ungrounded_challenge = bus.create_message(
        message_type=MessageType.HYPOTHESIS_CHALLENGED,
        sender_role="telemetry",
        payload={"claim": "CPU is not the issue"},
        evidence_refs=[],  # Missing!
    )
    res1 = bus.dispatch(ungrounded_challenge, state)
    assert not res1.success
    assert any("evidence ID" in err for err in res1.errors)

    # Conversational persuasion payload
    persuasive_msg = bus.create_message(
        message_type=MessageType.TASK_REQUEST,
        sender_role="topology",
        target_role="telemetry",
        payload={"persuasion": "I believe Redis is definitely the issue!"},
    )
    res2 = bus.dispatch(persuasive_msg, state)
    assert not res2.success
    assert any("evidence-not-conclusions" in err for err in res2.errors)
