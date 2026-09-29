"""
Specialist agents package for Secure Blackboard SRE.

Exports:
  - BaseSpecialist: base class enforcing policy rules, blind mode, and patch validation
  - PrimaryInvestigator: broad triage agent (S0 baseline)
  - TopologySpecialist: Agent A (traces, dependencies, propagation)
  - TelemetrySpecialist: Agent B (metrics, logs, resource saturation)
  - ConfigSystemSpecialist: Agent C (K8s, config, storage, OS)
  - FalsifierAgent: attacks hypotheses with discriminating tests
  - MitigationAgent: Agent D (post-gate recovery and invariant preservation)
"""

from clients.graphstate.agents.base import BaseSpecialist
from clients.graphstate.agents.config_system import ConfigSystemSpecialist
from clients.graphstate.agents.falsifier import FalsifierAgent
from clients.graphstate.agents.mitigation import MitigationAgent
from clients.graphstate.agents.primary import PrimaryInvestigator
from clients.graphstate.agents.telemetry import TelemetrySpecialist
from clients.graphstate.agents.topology import TopologySpecialist

__all__ = [
    "BaseSpecialist",
    "ConfigSystemSpecialist",
    "FalsifierAgent",
    "MitigationAgent",
    "PrimaryInvestigator",
    "TelemetrySpecialist",
    "TopologySpecialist",
]
