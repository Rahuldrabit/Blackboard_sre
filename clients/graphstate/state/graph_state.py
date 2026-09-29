"""
SREGraphState — the canonical incident state replacing STRATUS's conversation-only State.

STRATUS uses:
    class State(TypedDict):
        messages: Annotated[list, add_messages]

We replace that with a fully-typed incident state. The `messages` field is still
present for LangGraph's LLM nodes, but all incident knowledge lives in the
structured fields below.

State formula:
    S_t = (G_t, O_t, D_t, H_t, V_t, T_t, A_t, B_t)

where:
    G_t = service_graph          (topology)
    O_t = observations           (OBSERVED)
    D_t = derived_facts          (DERIVED)
    H_t = hypotheses             (HYPOTHESIZED)
    V_t = verified_claims        (VERIFIED)
    T_t = tasks                  (task management)
    A_t = actions                (proposed/executed)
    B_t = budget                 (token/tool/agent limits)
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph import add_messages
from typing_extensions import TypedDict

from clients.graphstate.state.schema import (
    Action,
    ActionResult,
    AgentRun,
    BudgetState,
    Diagnosis,
    DerivedFact,
    FalsificationTest,
    Hypothesis,
    MitigationResult,
    Observation,
    Task,
    TaskResult,
    VerifiedClaim,
)


class SREGraphState(TypedDict):
    """
    The single source of truth for an incident investigation.

    Agents never write to this directly. They return a StatePatch,
    which the deterministic PatchValidator validates before applying.
    """

    # ── Incident identity ──────────────────────────────────────────────────
    incident_id: str
    problem_id: str                        # SREGym problem ID
    phase: str                             # PhaseType: "diagnosis"|"mitigation"|"complete"
    enabled_stages: list[str]              # ["diagnosis"] or ["diagnosis","mitigation"]

    # ── System graph (G_t) ────────────────────────────────────────────────
    service_graph: dict[str, Any]
    # Shape: {
    #   "services": [{"name": str, "namespace": str, "type": str, "status": str}],
    #   "dependencies": [{"from": str, "to": str, "type": str}],
    #   "anomalous_services": [str],
    # }

    # ── Knowledge-leveled evidence (O_t, D_t, H_t, V_t) ──────────────────
    observations: list[Observation]         # OBSERVED — raw telemetry
    derived_facts: list[DerivedFact]        # DERIVED — deterministic rules
    hypotheses: list[Hypothesis]            # HYPOTHESIZED — LLM-generated
    verified_claims: list[VerifiedClaim]    # VERIFIED — passed causal checks
    falsification_tests: list[FalsificationTest]

    # ── Task management (T_t) ─────────────────────────────────────────────
    pending_tasks: list[Task]
    completed_tasks: list[TaskResult]

    # ── Actions (A_t) ─────────────────────────────────────────────────────
    proposed_actions: list[Action]
    executed_actions: list[ActionResult]

    # ── Agent history ─────────────────────────────────────────────────────
    agent_runs: list[AgentRun]
    current_agent_role: str | None          # Which agent node is active

    # ── Budget (B_t) ──────────────────────────────────────────────────────
    token_budget_remaining: int
    tool_calls_remaining: int
    agent_calls_remaining: int
    total_tokens_used: int
    total_tool_calls: int
    total_agent_calls: int

    # ── Query deduplication cache ──────────────────────────────────────────
    query_cache: dict[str, Any]
    # Shape: {cache_key: {"result": ..., "timestamp": ..., "ttl": int, "hit_count": int}}

    # ── Pending state patch (internal — from agent, awaiting validation) ──
    _pending_patch: dict[str, Any] | None   # Raw StatePatch dict before validation

    # ── Routing signals ───────────────────────────────────────────────────
    uncertainty_level: str                  # "low"|"medium"|"high"|"very_high"
    specialists_invoked: list[str]          # Roles already run this investigation
    investigation_round: int                # How many agent cycles completed

    # ── Feature flags (set at startup, never changed) ─────────────────────
    flags: dict[str, Any]                   # FeatureFlags serialized to dict

    # ── Outputs ───────────────────────────────────────────────────────────
    diagnosis: Diagnosis | None
    mitigation: MitigationResult | None
    submitted: bool                         # Has final answer been submitted to SREGym?
    num_steps: int                          # LangGraph step counter

    # ── LangGraph messages (for LLM call history within a single node) ────
    messages: Annotated[list, add_messages]


def initial_state(
    incident_id: str,
    problem_id: str,
    enabled_stages: list[str],
    flags: dict[str, Any],
    token_budget: int = 150_000,
    tool_calls_budget: int = 80,
    agent_calls_budget: int = 12,
) -> SREGraphState:
    """
    Create a fresh SREGraphState for a new incident.
    All evidence lists start empty; budget starts full.
    """
    return SREGraphState(
        incident_id=incident_id,
        problem_id=problem_id,
        phase="diagnosis",
        enabled_stages=enabled_stages,
        service_graph={"services": [], "dependencies": [], "anomalous_services": []},
        observations=[],
        derived_facts=[],
        hypotheses=[],
        verified_claims=[],
        falsification_tests=[],
        pending_tasks=[],
        completed_tasks=[],
        proposed_actions=[],
        executed_actions=[],
        agent_runs=[],
        current_agent_role=None,
        token_budget_remaining=token_budget,
        tool_calls_remaining=tool_calls_budget,
        agent_calls_remaining=agent_calls_budget,
        total_tokens_used=0,
        total_tool_calls=0,
        total_agent_calls=0,
        query_cache={},
        _pending_patch=None,
        uncertainty_level="high",
        specialists_invoked=[],
        investigation_round=0,
        flags=flags,
        diagnosis=None,
        mitigation=None,
        submitted=False,
        num_steps=0,
        messages=[],
    )
