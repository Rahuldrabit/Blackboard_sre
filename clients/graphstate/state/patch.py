"""
StatePatch — the typed delta that every agent returns.

Agents NEVER write to SREGraphState directly.
The flow is:
    Agent → StatePatch → PatchValidator → SREGraphState update

This separation means:
- Policy engine catches violations before they corrupt state.
- Rollback is trivial: the patch was never applied.
- All state changes are logged with who proposed them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from clients.graphstate.state.schema import (
    Action,
    ActionResult,
    AgentRun,
    Diagnosis,
    DerivedFact,
    FalsificationTest,
    Hypothesis,
    HypothesisUpdate,
    Observation,
    Task,
    TaskResult,
)


class StatePatch(BaseModel):
    """
    A typed, validated delta returned by every agent node.

    Rules enforced by PatchValidator before application:
    1. Only HYPOTHESIZED entries in new_hypotheses (never VERIFIED).
    2. proposed_actions only allowed during mitigation phase.
    3. Tokens/tool-calls within remaining budget.
    4. No self-promotion of hypothesis to VERIFIED.
    5. Delegation depth within bounds.
    """

    # ── Producer identity ──────────────────────────────────────────────────
    agent_id: str
    agent_role: str
    produced_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Evidence additions (all lists default to empty) ───────────────────
    new_observations: list[Observation] = Field(default_factory=list)
    new_derived_facts: list[DerivedFact] = Field(default_factory=list)
    new_hypotheses: list[Hypothesis] = Field(default_factory=list)
    new_falsification_tests: list[FalsificationTest] = Field(default_factory=list)

    # ── Evidence updates (deltas only, not full replacements) ─────────────
    hypothesis_updates: list[HypothesisUpdate] = Field(default_factory=list)

    # ── Task management ───────────────────────────────────────────────────
    new_tasks: list[Task] = Field(default_factory=list)
    completed_task_results: list[TaskResult] = Field(default_factory=list)

    # ── Actions (mitigation phase only) ───────────────────────────────────
    proposed_actions: list[Action] = Field(default_factory=list)
    executed_action_results: list[ActionResult] = Field(default_factory=list)

    # ── Resource consumption ───────────────────────────────────────────────
    tokens_used: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)

    # ── Diagnosis submission ───────────────────────────────────────────────
    diagnosis: Diagnosis | None = None

    # ── Metadata ──────────────────────────────────────────────────────────
    blind_mode_used: bool = False
    notes: str = Field(default="", description="Internal notes from the agent (not for prompts)")

    def is_empty(self) -> bool:
        """True if this patch adds nothing useful to the state."""
        return (
            not self.new_observations
            and not self.new_derived_facts
            and not self.new_hypotheses
            and not self.new_falsification_tests
            and not self.hypothesis_updates
            and not self.new_tasks
            and not self.completed_task_results
            and not self.proposed_actions
            and not self.executed_action_results
            and self.diagnosis is None
        )

    def summary(self) -> str:
        """Human-readable summary for logging."""
        parts = []
        if self.new_observations:
            parts.append(f"{len(self.new_observations)} obs")
        if self.new_hypotheses:
            parts.append(f"{len(self.new_hypotheses)} hyp")
        if self.hypothesis_updates:
            parts.append(f"{len(self.hypothesis_updates)} hyp-updates")
        if self.new_derived_facts:
            parts.append(f"{len(self.new_derived_facts)} derived")
        if self.new_falsification_tests:
            parts.append(f"{len(self.new_falsification_tests)} tests")
        if self.new_tasks:
            parts.append(f"{len(self.new_tasks)} tasks")
        if self.proposed_actions:
            parts.append(f"{len(self.proposed_actions)} actions")
        if self.diagnosis:
            parts.append("DIAGNOSIS")
        return f"[{self.agent_role}] " + (", ".join(parts) if parts else "empty")


class ValidationResult(BaseModel):
    """Result of PatchValidator.validate()."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stripped_items: list[str] = Field(
        default_factory=list,
        description="Items removed during sanitization",
    )

    def __bool__(self) -> bool:
        return self.valid
