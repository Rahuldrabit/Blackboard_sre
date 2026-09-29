"""
BaseSpecialist — the base class for all LLM specialist nodes.

Every specialist follows the same strict 7-step sequence:
  1. Policy rules compilation (pure deterministic Python).
  2. State view projection (blind mode: excludes other agents' hypotheses during blind phase).
  3. Stateless prompt compilation (stateless, zero accumulated conversation history).
  4. LLM call with structured JSON output.
  5. StatePatch parsing and structural sanitization.
  6. Deterministic PatchValidator validation (enforces HYPOTHESIS ≠ FACT, budget, permissions).
  7. Return validated state update dictionary for LangGraph.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from clients.graphstate.policy.engine import PolicyEngine
from clients.graphstate.policy.prompt_compiler import PromptCompiler
from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.patch import StatePatch, ValidationResult
from clients.graphstate.state.patch_validator import PatchValidator
from clients.graphstate.state.schema import (
    Action,
    ActionType,
    Hypothesis,
    HypothesisStatus,
    Observation,
    ObservationType,
)

logger = logging.getLogger(__name__)


class BaseSpecialist:
    """
    Shared foundation for all LLM investigator and planner nodes.
    """

    def __init__(
        self,
        role: str,
        model: str = "gemini-2.0-pro",
        policy_engine: PolicyEngine | None = None,
        prompt_compiler: PromptCompiler | None = None,
        patch_validator: PatchValidator | None = None,
        tools: list[dict[str, Any]] | None = None,
        llm_client: Any | None = None,
    ):
        self.role = role
        self.model = model
        self.policy = policy_engine or PolicyEngine()
        self.compiler = prompt_compiler or PromptCompiler()
        self.validator = patch_validator or PatchValidator()
        self.tools = tools or []
        self.llm = llm_client

    def get_task_description(self) -> str:
        """Override in subclasses to provide role-specific objective."""
        return f"Investigate the incident from the perspective of {self.role}."

    def get_state_view(self, state: SREGraphState, blind_mode: bool = False) -> dict[str, Any]:
        """
        Build a projected view of the blackboard for prompt compilation.
        If blind_mode is True, only hypotheses authored by this agent are included!
        Other agents' hypotheses are hidden to prevent wrong-guess propagation.
        """
        active_obs = self.policy.get_active_observations(state)

        view: dict[str, Any] = {
            "observations": active_obs,
            "derived_facts": state.get("derived_facts", []),
            "service_graph": state.get("service_graph", {}),
            "verified_claims": state.get("verified_claims", []),
        }

        all_hypotheses = state.get("hypotheses", [])
        if blind_mode:
            # Blind investigation: exclude other agents' hypotheses!
            view["hypotheses"] = [
                h for h in all_hypotheses
                if h.author_agent == self.role
            ]
        else:
            view["hypotheses"] = all_hypotheses

        return view

    async def run(self, state: SREGraphState, blind_mode: bool = False) -> dict[str, Any]:
        """
        Executes one turn of the specialist node.
        """
        # Step 1: Deterministic policy rules
        rules = self.policy.compile_rules_for_agent(self.role, state.get("phase", "diagnosis"))

        # Step 2: Build state view
        view = self.get_state_view(state, blind_mode=blind_mode)

        # Step 3: Compile stateless prompt
        task_desc = self.get_task_description()
        prompt = self.compiler.compile(
            role=self.role,
            task=task_desc,
            state_view=view,
            rules=rules,
            tools=self.tools,
        )

        # Step 4: Call LLM
        response_text = await self._call_llm(prompt)

        # Step 5: Parse to StatePatch
        patch = self._parse_response(response_text, state)

        # Step 6: Validate patch deterministically
        validation = self.validator.validate(patch, state, agent_role=self.role)
        if not validation.valid:
            logger.warning("Patch from '%s' had validation errors: %s", self.role, validation.errors)
            patch = self.validator.sanitize(patch, validation)

        # Step 7: Apply patch and return merged state
        updates = self.validator.apply(patch, state)
        return {**state, **updates}

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM client or fallback to structured mock for testing."""
        if self.llm is not None:
            try:
                resp = await self.llm.ainvoke(prompt)
                return resp.content if hasattr(resp, "content") else str(resp)
            except Exception as e:
                logger.error("LLM call failed: %s", e)

        # Deterministic mock response for offline testing
        return json.dumps({
            "new_observations": [
                {
                    "source": self.role,
                    "service": "cartservice",
                    "observation_type": "metric",
                    "summary": f"Observed abnormal latency spike in cartservice via {self.role}",
                    "raw_data": "p99=4500ms baseline=50ms",
                }
            ],
            "new_hypotheses": [
                {
                    "claim": f"Service cartservice degraded due to memory pressure identified by {self.role}",
                    "affected_services": ["cartservice"],
                    "causal_path": ["cartservice"],
                    "supporting_evidence": ["E001"],
                    "contradictory_evidence": [],
                    "untested_predictions": ["Check pod OOM events"],
                    "confidence": 0.78,
                }
            ],
            "hypothesis_updates": [],
            "new_tasks": [],
            "tool_calls_requested": [],
        })

    def _parse_response(self, text: str, state: SREGraphState) -> StatePatch:
        """Parse LLM JSON response safely into a StatePatch."""
        try:
            # Strip markdown code blocks if present
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
        except Exception as e:
            logger.warning("Failed to parse JSON from LLM: %s. Using empty patch.", e)
            data = {}

        obs_models: list[Observation] = []
        for raw_obs in data.get("new_observations", []):
            try:
                obs_models.append(
                    Observation(
                        id=f"E_{self.role[:3]}_{len(state.get('observations', [])) + len(obs_models) + 1}",
                        timestamp=datetime.now(timezone.utc),
                        source=raw_obs.get("source", self.role),
                        author_agent=self.role,
                        service=raw_obs.get("service"),
                        observation_type=ObservationType(raw_obs.get("observation_type", "metric")),
                        summary=raw_obs.get("summary", "Observation recorded"),
                        raw_data=raw_obs.get("raw_data"),
                    )
                )
            except Exception as ex:
                logger.warning("Skipping malformed observation: %s", ex)

        hyp_models: list[Hypothesis] = []
        for raw_h in data.get("new_hypotheses", []):
            try:
                hyp_models.append(
                    Hypothesis(
                        id=f"H_{self.role[:3]}_{len(state.get('hypotheses', [])) + len(hyp_models) + 1}",
                        timestamp=datetime.now(timezone.utc),
                        author_agent=self.role,
                        claim=raw_h.get("claim", "Unspecified claim"),
                        affected_services=raw_h.get("affected_services", ["unknown"]),
                        causal_path=raw_h.get("causal_path", ["unknown"]),
                        supporting_evidence=raw_h.get("supporting_evidence", []),
                        contradictory_evidence=raw_h.get("contradictory_evidence", []),
                        untested_predictions=raw_h.get("untested_predictions", []),
                        confidence=float(raw_h.get("confidence", 0.5)),
                        status=HypothesisStatus.ACTIVE,
                    )
                )
            except Exception as ex:
                logger.warning("Skipping malformed hypothesis: %s", ex)

        action_models: list[Action] = []
        for raw_a in data.get("proposed_actions", []):
            try:
                action_models.append(
                    Action(
                        id=raw_a.get("id", f"ACT_{len(state.get('proposed_actions', [])) + len(action_models) + 1}"),
                        action_type=ActionType(raw_a.get("action_type", "apply_manifest")),
                        target_service=raw_a.get("target_service", "unknown"),
                        target_resource=raw_a.get("target_resource"),
                        params=raw_a.get("params", {}),
                        is_destructive=bool(raw_a.get("is_destructive", False)),
                        requires_verified_rca=bool(raw_a.get("requires_verified_rca", True)),
                        estimated_recovery_time=float(raw_a.get("estimated_recovery_time", 60.0)),
                        estimated_risk=float(raw_a.get("estimated_risk", 0.5)),
                        proposed_by=self.role,
                        rationale=raw_a.get("rationale", "Mitigation proposed by agent"),
                    )
                )
            except Exception as ex:
                logger.warning("Skipping malformed action: %s", ex)

        return StatePatch(
            agent_id=f"agent-{self.role}",
            agent_role=self.role,
            new_observations=obs_models,
            new_hypotheses=hyp_models,
            proposed_actions=action_models,
            tokens_used=data.get("tokens_used", 1200),
            tool_calls_used=data.get("tool_calls_used", 1),
        )
