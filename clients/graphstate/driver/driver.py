"""
SREGym Driver for Secure Blackboard / GraphState Multi-Agent SRE.

Entry point:
    python -m clients.graphstate.driver.driver

Supports both single-agent baseline (S0) and full governed multi-agent architecture (M4):
- Phase 1: Blind Independent Investigation (Agents A, B, C with zero cross-hypothesis leakage)
- Phase 2: Cross-Examination & Falsification (differentiating tests, contradiction penalties, expansion trigger)
- Phase 3: Diagnosis Gate (multi-condition verification before SREGym submission)
- Phase 4: Safe Mitigation with Test-and-Rollback (TNR) & Durability checks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from clients.graphstate.agents.config_system import ConfigSystemSpecialist
from clients.graphstate.agents.falsifier import FalsifierAgent
from clients.graphstate.agents.mitigation import MitigationAgent
from clients.graphstate.agents.primary import PrimaryInvestigator
from clients.graphstate.agents.telemetry import TelemetrySpecialist
from clients.graphstate.agents.topology import TopologySpecialist
from clients.graphstate.driver.preflight import PreflightChecker
from clients.graphstate.evaluation.ablation_configs import ABLATION_CONFIGS, AblationConfig
from clients.graphstate.evidence.causal_verifier import CausalVerifier
from clients.graphstate.evidence.diagnosis_gate import DiagnosisGate
from clients.graphstate.evidence.falsification import FalsificationEngine
from clients.graphstate.evidence.hypothesis_manager import HypothesisManager
from clients.graphstate.mitigation.tnr import TestAndRollback
from clients.graphstate.policy.engine import PolicyEngine
from clients.graphstate.routing.adaptive_invoker import AdaptiveAgentInvoker, UncertaintyLevel
from clients.graphstate.routing.jev_router import JevRouter
from clients.graphstate.state.graph_state import SREGraphState, initial_state
from clients.graphstate.state.schema import Diagnosis, MitigationResult
from clients.graphstate.tools.semantic_tools import SEMANTIC_TOOL_SCHEMAS
from clients.graphstate.tools.tool_node import ToolExecutionNode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("sregym.graphstate.driver")


async def run_investigation(
    problem_id: str,
    model: str,
    stages: list[str],
    config_name: str = "M4",
    token_budget: int = 150_000,
) -> SREGraphState:
    """
    Executes an SREGym investigation conforming to the specified ablation config (S0..M4).
    """
    cfg: AblationConfig = ABLATION_CONFIGS.get(config_name, ABLATION_CONFIGS["M4"])
    logger.info("Initializing Secure Blackboard for '%s' | Config: %s | Model: %s | Stages: %s",
                problem_id, cfg.name, model, stages)

    state = initial_state(
        incident_id=f"inc_{problem_id}_{int(datetime.now(timezone.utc).timestamp())}",
        problem_id=problem_id,
        enabled_stages=stages,
        flags={**cfg.to_dict(), "model": model, "token_budget_total": token_budget},
        token_budget=token_budget,
    )

    policy = PolicyEngine()
    tool_node = ToolExecutionNode(mock_mode=True)
    hyp_mgr = HypothesisManager()
    verifier = CausalVerifier()
    falsifier_engine = FalsificationEngine()
    jev = JevRouter()
    gate = DiagnosisGate(causal_verifier=verifier, hypothesis_manager=hyp_mgr, jev_router=jev)
    tnr = TestAndRollback()

    # Step 0: Ingest initial telemetry
    initial_obs = await tool_node.execute_tool(
        tool_name="observe_service",
        parameters={"service": "cartservice"},
        author_agent="driver",
    )
    state["observations"].append(initial_obs)

    # ─────────────────────────────────────────────────────────────────────────
    # S0: Single-Agent Baseline
    # ─────────────────────────────────────────────────────────────────────────
    if not cfg.multi_agent:
        logger.info("Running S0 Single-Agent Investigation...")
        primary = PrimaryInvestigator(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)
        state = await primary.run(state)
        # Synthesize simple diagnosis
        active_hyps = [h for h in state.get("hypotheses", []) if h.status == "active"]
        if active_hyps:
            top_h = max(active_hyps, key=lambda h: h.confidence)
            state["diagnosis"] = Diagnosis(
                root_cause=top_h.claim,
                affected_services=top_h.affected_services,
                causal_path=top_h.causal_path,
                confidence=top_h.confidence,
                supporting_evidence=top_h.supporting_evidence,
                diagnosis_method="s0_single_agent",
            )
            state["submitted"] = True
        return state

    # ─────────────────────────────────────────────────────────────────────────
    # Multi-Agent Investigation (M2..M4)
    # ─────────────────────────────────────────────────────────────────────────
    agent_a = TopologySpecialist(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)
    agent_b = TelemetrySpecialist(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)
    agent_c = ConfigSystemSpecialist(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)

    # Phase 1: Blind Investigation Phase
    # Each agent investigates independently without seeing peers' hypotheses
    blind = cfg.blind_investigation_phase
    logger.info("Phase 1: Blind Independent Investigation (blind_mode=%s)...", blind)
    state = await agent_a.run(state, blind_mode=blind)
    state = await agent_b.run(state, blind_mode=blind)
    state = await agent_c.run(state, blind_mode=blind)

    logger.info("Phase 1 Complete. Formulated %d independent hypotheses across investigators.",
                len(state.get("hypotheses", [])))

    # Phase 2: Cross-Examination & Falsification
    if cfg.use_falsification:
        logger.info("Phase 2: Cross-Examination and Falsification...")
        falsifier = FalsifierAgent(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)
        state = await falsifier.run(state, blind_mode=False)

        # Check hypothesis expansion trigger: if all candidates are weak, expand!
        should_expand, reason = hyp_mgr.should_trigger_expansion(state)
        if should_expand:
            logger.info("Hypothesis expansion triggered (%s). Spawning primary investigator for new candidates.", reason)
            primary = PrimaryInvestigator(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)
            state = await primary.run(state, blind_mode=False)

    # Phase 3: Diagnosis Gate
    logger.info("Phase 3: Evaluating Diagnosis Gate...")
    gate_result = gate.evaluate_submission(state)
    if gate_result.approved and gate_result.diagnosis:
        state["diagnosis"] = gate_result.diagnosis
        state["submitted"] = True
        logger.info("Diagnosis Gate PASSED: %s (confidence=%.2f)",
                    gate_result.diagnosis.root_cause, gate_result.diagnosis.confidence)
    else:
        logger.warning("Diagnosis Gate held submission. Fallback to top scored candidate. Reasons: %s",
                       gate_result.reasons)
        ranked = hyp_mgr.rank_hypotheses(state)
        if ranked:
            top_h, _ = ranked[0]
            state["diagnosis"] = Diagnosis(
                root_cause=top_h.claim,
                affected_services=top_h.affected_services,
                causal_path=top_h.causal_path,
                confidence=top_h.confidence,
                supporting_evidence=list(top_h.supporting_evidence),
                diagnosis_method="fallback_top_candidate",
            )
            state["submitted"] = True

    # Phase 4: Mitigation Phase with TNR (if enabled)
    if "mitigation" in stages and state.get("diagnosis"):
        logger.info("Phase 4: Entering Mitigation Phase...")
        state["phase"] = "mitigation"
        mitigator = MitigationAgent(model=model, policy_engine=policy, tools=SEMANTIC_TOOL_SCHEMAS)
        state = await mitigator.run(state, blind_mode=False)

        actions = state.get("proposed_actions", [])
        if actions:
            action_to_apply = actions[0]
            logger.info("Executing mitigation action %s via TNR...", action_to_apply.id)
            result = await tnr.execute_action(action_to_apply, state, tool_node=tool_node)
            state["executed_actions"].append(result)
            state["mitigation"] = MitigationResult(
                actions_executed=[action_to_apply.id],
                actions_rolled_back=[action_to_apply.id] if result.rolled_back else [],
                final_health_status="healthy" if result.committed else "degraded",
                mitigation_successful=result.committed,
            )
            logger.info("Mitigation execution complete. Status: %s (committed=%s)",
                        result.status, result.committed)

    state["phase"] = "complete"
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Secure Blackboard SRE Agent Driver")
    parser.add_argument("--problem", type=str, default=None, help="SREGym problem ID")
    parser.add_argument("--model", type=str, default=None, help="LLM model name")
    parser.add_argument("--stages", type=str, default="diagnosis,mitigation", help="Stages (diagnosis, mitigation)")
    parser.add_argument("--config", type=str, default="M4", choices=["S0", "M0", "M1", "M2", "M3", "M4"], help="Ablation config")
    parser.add_argument("--token-budget", type=int, default=150_000, help="Total token budget")
    args = parser.parse_args()

    preflight = PreflightChecker.run_checks(problem_id=args.problem, model=args.model)
    if not preflight.passed:
        logger.error("Preflight checks failed: %s", preflight.errors)
        sys.exit(1)

    problem_id = preflight.metadata["problem_id"]
    model = preflight.metadata["model"]
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    try:
        final_state = asyncio.run(
            run_investigation(
                problem_id=problem_id,
                model=model,
                stages=stages,
                config_name=args.config,
                token_budget=args.token_budget,
            )
        )
        diag = final_state.get("diagnosis")
        mit = final_state.get("mitigation")

        output = {
            "status": "success" if diag else "failed",
            "problem_id": problem_id,
            "config": args.config,
            "diagnosis": {
                "root_cause": diag.root_cause if diag else None,
                "affected_services": diag.affected_services if diag else [],
                "confidence": diag.confidence if diag else 0.0,
                "supporting_evidence": diag.supporting_evidence if diag else [],
                "method": diag.diagnosis_method if diag else None,
            } if diag else None,
            "mitigation": {
                "successful": mit.mitigation_successful if mit else False,
                "actions_executed": mit.actions_executed if mit else [],
                "actions_rolled_back": mit.actions_rolled_back if mit else [],
            } if mit else None,
            "tokens_used": final_state.get("total_tokens_used", 0),
            "tool_calls": final_state.get("total_tool_calls", 0),
        }
        print(json.dumps(output, indent=2))
    except Exception as e:
        logger.exception("Error executing SREGym GraphState driver: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
