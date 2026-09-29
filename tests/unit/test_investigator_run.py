"""
Test investigator execution and patch application to state.
"""

import asyncio
import pytest

from clients.graphstate.agents.primary import PrimaryInvestigator
from clients.graphstate.state.graph_state import initial_state
from clients.graphstate.tools.semantic_tools import SEMANTIC_TOOL_SCHEMAS


@pytest.mark.asyncio
async def test_primary_investigator_run():
    state = initial_state(
        incident_id="inc-test",
        problem_id="prob-test",
        enabled_stages=["diagnosis"],
        flags={},
    )

    investigator = PrimaryInvestigator(tools=SEMANTIC_TOOL_SCHEMAS)
    new_state = await investigator.run(state)

    print("Observations count:", len(new_state.get("observations", [])))
    print("Hypotheses count:", len(new_state.get("hypotheses", [])))

    assert len(new_state.get("observations", [])) > 0
    assert len(new_state.get("hypotheses", [])) > 0
    assert new_state["hypotheses"][0].author_agent == "primary_investigator"
