"""
Pydantic schemas for every object that lives inside SREGraphState.

Design principles:
- Every evidence object carries provenance (author_agent, source, timestamp).
- Raw telemetry text is quarantined in `raw_data` and never injected into prompts directly.
- Hypotheses retain supporting + contradictory evidence IDs, not free-text summaries.
- Knowledge level is set at creation time and can only change via the policy engine.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from clients.graphstate.state.knowledge_levels import KnowledgeLevel


# ─────────────────────────────────────────────────────────────────────────────
# Supporting enums and base types
# ─────────────────────────────────────────────────────────────────────────────


class Provenance(BaseModel):
    """Provenance and audit record for every object written to the blackboard."""

    author_agent: str = Field(description="Agent ID or role that authored this")
    source: str = Field(description="Tool, rule, or mechanism that provided the underlying data")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    signature: str | None = Field(default=None, description="Optional integrity checksum or token")



class ObservationType(str, Enum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    K8S_EVENT = "k8s_event"
    ALERT = "alert"
    CONFIG = "config"
    TOPOLOGY = "topology"
    PROCESS = "process"


class HypothesisStatus(str, Enum):
    ACTIVE = "active"
    REJECTED = "rejected"
    VERIFIED = "verified"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActionType(str, Enum):
    ROLLBACK_CONFIG = "rollback_config"
    UPDATE_SECRET = "update_secret"
    RESTART_WORKLOAD = "restart_workload"
    SCALE_SERVICE = "scale_service"
    REROUTE_TRAFFIC = "reroute_traffic"
    DELETE_POD = "delete_pod"
    APPLY_MANIFEST = "apply_manifest"
    EXEC_COMMAND = "exec_command"


class PhaseType(str, Enum):
    DIAGNOSIS = "diagnosis"
    MITIGATION = "mitigation"
    COMPLETE = "complete"


# ─────────────────────────────────────────────────────────────────────────────
# Evidence objects
# ─────────────────────────────────────────────────────────────────────────────


class Observation(BaseModel):
    """
    OBSERVED knowledge level.
    Direct telemetry read from the system via tools.
    Raw data is quarantined and never injected verbatim into LLM prompts.
    """

    id: str = Field(description="Unique ID, e.g. 'E17'")
    timestamp: datetime
    source: str = Field(description="Tool name or agent ID that produced this")
    author_agent: str = Field(description="Agent ID that called the tool")
    service: str | None = Field(default=None, description="Service/component this observation is about")
    observation_type: ObservationType
    summary: str = Field(description="Concise, sanitized summary safe for prompt injection")
    raw_data: str | None = Field(
        default=None,
        description="QUARANTINED: raw tool output. Never inject directly into prompts.",
    )
    anomaly_score: float | None = Field(default=None, ge=0.0, le=1.0)
    tool_name: str | None = None
    tool_params: dict[str, Any] = Field(default_factory=dict)
    knowledge_level: KnowledgeLevel = KnowledgeLevel.OBSERVED
    freshness_ttl_seconds: int = 300  # How long before this becomes stale
    tags: list[str] = Field(default_factory=list)

    @field_validator("knowledge_level")
    @classmethod
    def must_be_observed(cls, v: KnowledgeLevel) -> KnowledgeLevel:
        if v != KnowledgeLevel.OBSERVED:
            raise ValueError("Observation must have OBSERVED knowledge level")
        return v


class DerivedFact(BaseModel):
    """
    DERIVED knowledge level.
    Produced by deterministic computation from observations (no LLM).
    Examples: "transport reachable", "latency > 3σ from baseline", "pod restart count > 5".
    """

    id: str = Field(description="Unique ID, e.g. 'D4'")
    timestamp: datetime
    source: str = Field(description="Rule or computation that produced this")
    service: str | None = None
    claim: str = Field(description="Declarative fact statement")
    derivation_rule: str = Field(description="Which rule/function produced this")
    input_evidence: list[str] = Field(description="Observation IDs this was derived from")
    confidence: float = Field(ge=0.0, le=1.0, description="Deterministic confidence (usually 1.0)")
    knowledge_level: KnowledgeLevel = KnowledgeLevel.DERIVED

    @field_validator("knowledge_level")
    @classmethod
    def must_be_derived(cls, v: KnowledgeLevel) -> KnowledgeLevel:
        if v != KnowledgeLevel.DERIVED:
            raise ValueError("DerivedFact must have DERIVED knowledge level")
        return v


class FalsificationTest(BaseModel):
    """A test designed to DISPROVE a hypothesis."""

    id: str
    target_hypothesis_id: str
    description: str = Field(description="What this test checks")
    tool_name: str
    tool_params: dict[str, Any]
    expected_if_hypothesis_true: str
    expected_if_hypothesis_false: str
    estimated_cost: float = Field(default=1.0, description="Relative cost units")
    hypotheses_differentiated: int = Field(
        default=1,
        description="How many active hypotheses this test can distinguish",
    )

    @property
    def utility(self) -> float:
        """V1 heuristic: #hypotheses_differentiated / estimated_cost."""
        return self.hypotheses_differentiated / max(self.estimated_cost, 0.001)


class HypothesisUpdate(BaseModel):
    """A delta to apply to an existing hypothesis (agents cannot replace hypotheses directly)."""

    hypothesis_id: str
    confidence_delta: float = Field(ge=-1.0, le=1.0)
    new_supporting_evidence: list[str] = Field(default_factory=list)
    new_contradictory_evidence: list[str] = Field(default_factory=list)
    new_untested_predictions: list[str] = Field(default_factory=list)
    status_update: HypothesisStatus | None = None
    reason: str = Field(description="Why this update is being applied")


class Hypothesis(BaseModel):
    """
    HYPOTHESIZED knowledge level.
    LLM-generated causal explanation. Unverified until CausalVerifier promotes it.
    """

    id: str = Field(description="Unique ID, e.g. 'H3'")
    timestamp: datetime
    author_agent: str = Field(description="Agent ID that generated this hypothesis")
    claim: str = Field(description="Human-readable root cause claim")
    affected_services: list[str] = Field(description="Services implicated in this hypothesis")
    causal_path: list[str] = Field(
        description="Ordered causal chain, e.g. ['redis', 'payment', 'checkout', 'frontend']"
    )
    supporting_evidence: list[str] = Field(
        description="Evidence IDs (Observations/DerivedFacts) that support this"
    )
    contradictory_evidence: list[str] = Field(
        description="Evidence IDs that contradict this"
    )
    untested_predictions: list[str] = Field(
        description="Predictions this hypothesis makes that haven't been tested yet"
    )
    falsification_tests: list[str] = Field(
        default_factory=list,
        description="FalsificationTest IDs for this hypothesis",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    knowledge_level: KnowledgeLevel = KnowledgeLevel.HYPOTHESIZED

    @field_validator("knowledge_level")
    @classmethod
    def must_be_hypothesized(cls, v: KnowledgeLevel) -> KnowledgeLevel:
        if v != KnowledgeLevel.HYPOTHESIZED:
            raise ValueError("Hypothesis must have HYPOTHESIZED knowledge level")
        return v

    @field_validator("causal_path")
    @classmethod
    def must_have_path(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("causal_path must have at least one element")
        return v


class VerifiedClaim(BaseModel):
    """
    VERIFIED knowledge level.
    Promoted from HYPOTHESIZED only by the deterministic CausalVerifier node.
    Agents cannot create VerifiedClaims directly.
    """

    id: str = Field(description="Unique ID, e.g. 'V2'")
    timestamp: datetime
    original_hypothesis_id: str
    claim: str
    affected_services: list[str]
    causal_path: list[str]
    verification_method: str = Field(
        description="Which checks passed: structural|temporal|statistical|trace"
    )
    verification_evidence: list[str] = Field(
        description="Evidence IDs that confirmed this claim"
    )
    structural_check: bool = False
    temporal_check: bool = False
    statistical_check: bool = False
    evidence_sufficiency_check: bool = False
    knowledge_level: KnowledgeLevel = KnowledgeLevel.VERIFIED

    @field_validator("knowledge_level")
    @classmethod
    def must_be_verified(cls, v: KnowledgeLevel) -> KnowledgeLevel:
        if v != KnowledgeLevel.VERIFIED:
            raise ValueError("VerifiedClaim must have VERIFIED knowledge level")
        return v

    @property
    def all_checks_passed(self) -> bool:
        return all([
            self.structural_check,
            self.temporal_check,
            self.evidence_sufficiency_check,
        ])


# ─────────────────────────────────────────────────────────────────────────────
# Task management
# ─────────────────────────────────────────────────────────────────────────────


class Task(BaseModel):
    """A unit of work delegated to a specialist agent."""

    id: str
    task_type: str = Field(description="e.g. 'investigate_traces', 'falsify_hypothesis'")
    assignee_role: str | None = Field(
        default=None,
        description="Required specialist role; None = any available",
    )
    capability_required: str | None = None
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_by: str
    delegation_depth: int = Field(
        default=0,
        description="Depth in the delegation chain. Rejected if > MAX_DELEGATION_DEPTH.",
    )
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deadline: datetime | None = None


class TaskResult(BaseModel):
    """Result returned when a task completes."""

    task_id: str
    agent_id: str
    status: TaskStatus
    patch_summary: str = Field(description="Human-readable summary of what was added to state")
    tokens_used: int = 0
    tool_calls_used: int = 0
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Actions (mitigation phase only)
# ─────────────────────────────────────────────────────────────────────────────


class Action(BaseModel):
    """A proposed mitigation action. LLM generates, policy validates, TNR executes."""

    id: str
    action_type: ActionType
    target_service: str
    target_resource: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    is_destructive: bool = Field(
        description="True if this action can cause data loss or extended downtime"
    )
    requires_verified_rca: bool = Field(
        default=True,
        description="True if a VerifiedClaim must exist before execution",
    )
    estimated_recovery_time: float = Field(
        default=60.0, description="Estimated seconds to recovery"
    )
    estimated_risk: float = Field(default=0.5, ge=0.0, le=1.0)
    estimated_resource_cost: float = Field(default=0.0)
    estimated_slo_damage: float = Field(default=0.0, ge=0.0, le=1.0)
    proposed_by: str
    rationale: str

    def score(self, alpha: float = 0.3, beta: float = 0.4,
              gamma: float = 0.15, delta: float = 0.15) -> float:
        """J(a) = α·T_recovery + β·Risk + γ·ResourceCost + δ·SLODamage"""
        return (
            alpha * self.estimated_recovery_time / 300  # Normalize to 5 min
            + beta * self.estimated_risk
            + gamma * self.estimated_resource_cost
            + delta * self.estimated_slo_damage
        )


class ActionResult(BaseModel):
    """Outcome after TNR executes an action."""

    action_id: str
    status: str = Field(description="committed | rolled_back | failed")
    committed: bool
    rolled_back: bool = False
    regression_detected: bool = False
    regression_details: str | None = None
    executed_at: datetime = Field(default_factory=datetime.utcnow)
    health_after: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Agent tracking
# ─────────────────────────────────────────────────────────────────────────────


class AgentRun(BaseModel):
    """Record of an agent invocation for audit and cost tracking."""

    agent_id: str
    agent_role: str
    model: str
    started_at: datetime
    completed_at: datetime | None = None
    tokens_used: int = 0
    tool_calls_used: int = 0
    observations_added: int = 0
    hypotheses_added: int = 0
    hypothesis_updates: int = 0
    tasks_created: int = 0
    patch_valid: bool = True
    policy_violations: list[str] = Field(default_factory=list)
    blind_mode: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Output types
# ─────────────────────────────────────────────────────────────────────────────


class Diagnosis(BaseModel):
    """Final diagnosis submitted to SREGym."""

    root_cause: str = Field(description="One-sentence root cause statement")
    affected_services: list[str]
    causal_path: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(description="Evidence IDs")
    verified_claim_id: str | None = Field(
        default=None,
        description="ID of VerifiedClaim that backs this diagnosis (if available)",
    )
    diagnosis_method: str = Field(
        description="How this was reached: verified|high_confidence_hypothesis|timeout_fallback"
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class MitigationResult(BaseModel):
    """Final mitigation result submitted to SREGym."""

    actions_executed: list[str] = Field(description="Action IDs")
    actions_rolled_back: list[str] = Field(description="Action IDs that were rolled back")
    final_health_status: str
    mitigation_successful: bool
    completed_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Budget tracking
# ─────────────────────────────────────────────────────────────────────────────


class BudgetState(BaseModel):
    """Tracks remaining resources. Enforced by the policy engine."""

    token_budget_total: int = 200_000
    token_budget_remaining: int = 200_000
    tool_calls_total: int = 100
    tool_calls_remaining: int = 100
    agent_calls_total: int = 10
    agent_calls_remaining: int = 10
    llm_cost_usd: float = 0.0

    def is_exhausted(self) -> bool:
        return self.token_budget_remaining <= 0 or self.tool_calls_remaining <= 0

    def consume(self, tokens: int = 0, tool_calls: int = 0, agent_calls: int = 0) -> "BudgetState":
        return self.model_copy(update={
            "token_budget_remaining": self.token_budget_remaining - tokens,
            "tool_calls_remaining": self.tool_calls_remaining - tool_calls,
            "agent_calls_remaining": self.agent_calls_remaining - agent_calls,
        })
