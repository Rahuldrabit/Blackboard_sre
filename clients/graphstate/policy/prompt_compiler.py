"""
PromptCompiler — builds stateless prompts from structured state, never from conversation history.

KEY DESIGN PRINCIPLE:
Every LLM call gets a fresh, structured prompt compiled from the current Blackboard/SREGraphState.
There is no "accumulated conversation" carrying context or hallucinated hypotheses.
The state IS the memory.
Each call to compile() produces a self-contained prompt that a cold LLM can act on.
This is the core mechanism for foundation-model independence and resistance to wrong-guess propagation.
"""

from __future__ import annotations

import json
from typing import Any

from clients.graphstate.state.schema import (
    DerivedFact,
    Hypothesis,
    Observation,
    VerifiedClaim,
)

ROLE_DESCRIPTIONS: dict[str, str] = {
    "primary_investigator": (
        "You are the Primary SRE Investigator. Your mission is broad triage: observe the system, "
        "identify anomalous components, and formulate initial candidate hypotheses grounded in evidence."
    ),
    "topology": (
        "You are Investigator A (Topology & Traces Specialist). Your focus is request propagation, "
        "service dependency graphs, trace spans, and network path bottlenecks. "
        "Identify where errors or latency first originated in the call chain."
    ),
    "telemetry": (
        "You are Investigator B (Telemetry Specialist). Your focus is Prometheus metrics, container logs, "
        "resource saturation (CPU, Memory, Disk, Network), and time-series anomaly correlation. "
        "Distinguish causal telemetry anomalies from secondary noise."
    ),
    "config_system": (
        "You are Investigator C (System & Configuration Specialist). Your focus is Kubernetes cluster state, "
        "deployments, services, ConfigMaps, Secrets, PVCs, probes, node status, and recent YAML/manifest changes. "
        "Verify if any misconfiguration or platform failure caused the incident."
    ),
    "falsifier": (
        "You are the Hypothesis Falsification Specialist. Your sole objective is to CRITIQUE and DISPROVE "
        "active candidate hypotheses. What diagnostic test or counter-evidence would demonstrate that "
        "a candidate hypothesis is WRONG?"
    ),
    "mitigation_planner": (
        "You are Agent D (Mitigation Specialist). Diagnosis is complete and verified. "
        "Your task is to design safe, least-destructive recovery actions that restore service health "
        "while strictly preserving durability invariants (e.g., surviving rollouts and restarts)."
    ),
}


class PromptCompiler:
    """
    Stateless compiler that produces self-contained prompt strings from
    structured Blackboard / SREGraphState views.
    """

    def compile(
        self,
        role: str,
        task: str,
        state_view: dict[str, Any],
        rules: list[str],
        tools: list[dict[str, Any]] | list[str],
        delegated_task: str | None = None,
    ) -> str:
        """
        Assemble a complete, cold-start prompt for an LLM node.
        """
        role_desc = ROLE_DESCRIPTIONS.get(role, f"You are an SRE specialist acting in role: {role}.")

        sections: list[str] = [
            f"# ROLE DEFINITION\n{role_desc}",
            f"# PRIMARY OBJECTIVE\n{task}",
        ]

        if delegated_task:
            sections.append(f"# DELEGATED SUB-TASK\n{delegated_task}")

        # Deterministic Rules
        formatted_rules = "\n".join(f"- {rule}" for rule in rules)
        sections.append(f"# MANDATORY CONSTRAINTS & RULES\n{formatted_rules}")

        # Structured Blackboard View
        serialized_view = self._serialize_state_view(state_view)
        sections.append(f"# CURRENT BLACKBOARD STATE\n{serialized_view}")

        # Available Tools
        tools_desc = self._serialize_tools(tools)
        sections.append(f"# AVAILABLE TOOLS\n{tools_desc}")

        # Output Schema Instructions
        sections.append(self._output_format_instructions(role))

        return "\n\n".join(sections)

    def _serialize_state_view(self, view: dict[str, Any]) -> str:
        parts: list[str] = []

        # Service graph summary
        sg = view.get("service_graph")
        if sg:
            services = sg.get("services", [])
            deps = sg.get("dependencies", [])
            anomalous = sg.get("anomalous_services", [])
            parts.append(
                f"## Topology & Services ({len(services)} services, {len(deps)} dependencies):\n"
                f"- Anomalous / Impacted Services: {anomalous if anomalous else 'None detected yet'}\n"
                f"- Services List: {', '.join([s.get('name', str(s)) for s in services[:15]])}"
            )

        # Observations (OBSERVED)
        obs_list: list[Observation] = view.get("observations", [])
        if obs_list:
            obs_lines = [
                f"  [{obs.id}] ({obs.observation_type}) {obs.source}: {obs.summary}"
                for obs in obs_list[-20:]  # Cap to recent 20 for token budget
            ]
            parts.append(f"## Observations (OBSERVED):\n" + "\n".join(obs_lines))
        else:
            parts.append("## Observations (OBSERVED):\n  (No observations recorded yet)")

        # Derived facts (DERIVED)
        derived_list: list[DerivedFact] = view.get("derived_facts", [])
        if derived_list:
            df_lines = [
                f"  [{df.id}] {df.rule_name}: {df.fact} (from: {df.derived_from_ids})"
                for df in derived_list[-15:]
            ]
            parts.append(f"## Derived Facts (DERIVED):\n" + "\n".join(df_lines))

        # Hypotheses (HYPOTHESIZED) — Note: in blind mode, other agents' hypotheses are excluded
        hyp_list: list[Hypothesis] = view.get("hypotheses", [])
        if hyp_list:
            hyp_lines = []
            for h in hyp_list:
                hyp_lines.append(
                    f"  [{h.id}] Claim: {h.claim}\n"
                    f"       Causal Path: {' -> '.join(h.causal_path) if h.causal_path else 'N/A'}\n"
                    f"       Supporting Evidence: {h.supporting_evidence}\n"
                    f"       Contradictory Evidence: {h.contradictory_evidence}\n"
                    f"       Untested Predictions: {h.untested_predictions}\n"
                    f"       Confidence: {h.confidence:.2f} (Status: {h.status}, Author: {h.author_agent})"
                )
            parts.append(f"## Active Hypotheses (HYPOTHESIZED):\n" + "\n\n".join(hyp_lines))

        # Verified claims (VERIFIED)
        ver_list: list[VerifiedClaim] = view.get("verified_claims", [])
        if ver_list:
            ver_lines = [
                f"  [{v.id}] Claim: {v.claim}\n"
                f"       Method: {v.verification_method}\n"
                f"       Evidence: {v.verification_evidence}"
                for v in ver_list
            ]
            parts.append(f"## Verified Findings (VERIFIED):\n" + "\n".join(ver_lines))

        return "\n\n".join(parts) if parts else "(Blackboard is currently empty)"

    def _serialize_tools(self, tools: list[dict[str, Any]] | list[str]) -> str:
        if not tools:
            return "No tools provided."
        if isinstance(tools[0], str):
            return ", ".join(tools)
        tool_summaries = []
        for t in tools:
            name = t.get("name", "unknown")
            desc = t.get("description", "No description")
            tool_summaries.append(f"- `{name}`: {desc}")
        return "\n".join(tool_summaries)

    def _output_format_instructions(self, role: str) -> str:
        base = """# OUTPUT SPECIFICATION
You MUST respond with a valid JSON object matching the StatePatch specification:
```json
{
  "new_observations": [
    {
      "source": "<tool_name or agent_role>",
      "service": "<service_name or null>",
      "observation_type": "metric|log|trace|k8s_event|config",
      "summary": "<one-line factual summary of finding>",
      "raw_data": "<raw output if needed for evidence quarantine>"
    }
  ],
  "new_hypotheses": [
    {
      "claim": "<concise causal statement>",
      "affected_services": ["<service1>", "<service2>"],
      "causal_path": ["<root_service>", "<intermediate>", "<symptom>"],
      "supporting_evidence": ["<evidence_id_1>", "<evidence_id_2>"],
      "contradictory_evidence": [],
      "untested_predictions": ["<what test would falsify or confirm this>"],
      "confidence": 0.85
    }
  ],
  "hypothesis_updates": [],
  "new_tasks": [],
  "tool_calls_requested": [
    {
      "tool_name": "<tool_to_call>",
      "parameters": {"param1": "value1"}
    }
  ]
}
```
CRITICAL: Do NOT wrap the JSON with conversational pleasantries or explanations outside the JSON object.
"""
        if role == "mitigation_planner":
            base += """
For mitigation, include `proposed_actions`:
```json
{
  "proposed_actions": [
    {
      "action_type": "rollback_config|restart_workload|update_secret|apply_manifest",
      "target": "<target resource or service>",
      "params": {},
      "is_destructive": false,
      "estimated_risk": 0.1,
      "estimated_recovery_time": 30.0
    }
  ]
}
```
"""
        return base
