"""
Ablation Configurations and Feature Flags for SREGym evaluation.

Defines the 6 comparative architectures:
  S0: Strong Single SRE Agent (conversation-only history, STRATUS-style)
  M0: Conventional Free-Communication MAS (unstructured agent-to-agent chat)
  M1: Ordinary Shared-Blackboard MAS (untyped shared dictionary, no invariants)
  M2: Secure Blackboard + Deterministic Rules (epistemic partitions, policy engine)
  M3: M2 + Falsification & Hypothesis Registry
  M4: M3 + Bounded Jev Router + Adaptive Agent Invocation (OUR FULL ARCHITECTURE)
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class AblationConfig(BaseModel):
    """Architecture feature flags for controlled comparative benchmarking."""

    name: str
    multi_agent: bool
    use_secure_blackboard: bool
    blind_investigation_phase: bool
    enforce_epistemic_invariants: bool
    use_typed_bus: bool
    use_falsification: bool
    use_adaptive_invocation: bool
    use_jev_router: bool
    token_budget_target: int = 150_000

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# The 6 canonical configurations:
ABLATION_CONFIGS: dict[str, AblationConfig] = {
    "S0": AblationConfig(
        name="S0_single_agent",
        multi_agent=False,
        use_secure_blackboard=False,
        blind_investigation_phase=False,
        enforce_epistemic_invariants=False,
        use_typed_bus=False,
        use_falsification=False,
        use_adaptive_invocation=False,
        use_jev_router=False,
    ),
    "M0": AblationConfig(
        name="M0_free_communication_mas",
        multi_agent=True,
        use_secure_blackboard=False,
        blind_investigation_phase=False,
        enforce_epistemic_invariants=False,
        use_typed_bus=False,
        use_falsification=False,
        use_adaptive_invocation=False,
        use_jev_router=False,
    ),
    "M1": AblationConfig(
        name="M1_plain_blackboard_mas",
        multi_agent=True,
        use_secure_blackboard=True,
        blind_investigation_phase=False,
        enforce_epistemic_invariants=False,  # Unprotected: agents can self-verify
        use_typed_bus=False,
        use_falsification=False,
        use_adaptive_invocation=False,
        use_jev_router=False,
    ),
    "M2": AblationConfig(
        name="M2_secure_blackboard_rules",
        multi_agent=True,
        use_secure_blackboard=True,
        blind_investigation_phase=True,
        enforce_epistemic_invariants=True,   # HYPOTHESIS != FACT strictly enforced
        use_typed_bus=True,
        use_falsification=False,
        use_adaptive_invocation=False,
        use_jev_router=False,
    ),
    "M3": AblationConfig(
        name="M3_governed_blackboard_falsification",
        multi_agent=True,
        use_secure_blackboard=True,
        blind_investigation_phase=True,
        enforce_epistemic_invariants=True,
        use_typed_bus=True,
        use_falsification=True,              # Discriminating tests + expansion trigger
        use_adaptive_invocation=False,
        use_jev_router=False,
    ),
    "M4": AblationConfig(
        name="M4_full_secure_blackboard_sre",
        multi_agent=True,
        use_secure_blackboard=True,
        blind_investigation_phase=True,
        enforce_epistemic_invariants=True,
        use_typed_bus=True,
        use_falsification=True,
        use_adaptive_invocation=True,         # N = f(uncertainty)
        use_jev_router=True,                 # Bounded test & routing ranking
    ),
}
