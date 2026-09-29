"""
Agent C — System and Configuration Specialist (Kubernetes, Storage, OS, Network).

Focuses on:
  - Kubernetes objects (Deployments, Pods, Services, Endpoints, NetworkPolicies)
  - Secrets, ConfigMaps, and environment variables
  - PersistentVolumeClaims (PVC) and storage mounts
  - Probes (readiness, liveness, startup), resource limits/requests
  - Node health and OS-level issues (kubelet, iptables, disk pressure)

Critical for SREGym:
  Many SREGym benchmark scenarios (e.g. sregym-0508) involve exact system-level
  faults: selector mismatches, wrong port mappings, missing secret keys,
  OOMKilled pods, corrupted volume mounts, or network policy drops.
"""

from __future__ import annotations

import json
from typing import Any

from clients.graphstate.agents.base import BaseSpecialist


class ConfigSystemSpecialist(BaseSpecialist):
    """Specialist for Kubernetes manifests, system config, PVCs, and OS-level platform faults."""

    def __init__(self, **kwargs: Any):
        super().__init__(role="config_system", **kwargs)

    def get_task_description(self) -> str:
        return (
            "Analyze Kubernetes resources, platform configurations, and system events. "
            "Inspect Deployments, Services, ConfigMaps, Secrets, PVCs, NetworkPolicies, "
            "probes, and resource quotas to locate configuration drift or platform faults."
        )

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is not None:
            return await super()._call_llm(prompt)

        # Deterministic domain-specific mock for offline testing
        return json.dumps({
            "new_observations": [
                {
                    "source": "config_system",
                    "service": "cartservice",
                    "observation_type": "config",
                    "summary": "cartservice Deployment container memory limit was set to 64Mi (baseline 512Mi)",
                    "raw_data": "resources.limits.memory: 64Mi (revision 3 applied 18m ago)",
                }
            ],
            "new_hypotheses": [
                {
                    "claim": "Cartservice deployment misconfigured with insufficient container memory limit (64Mi)",
                    "affected_services": ["cartservice"],
                    "causal_path": ["cartservice"],
                    "supporting_evidence": ["E003"],
                    "contradictory_evidence": [],
                    "untested_predictions": ["Restoring memory limit to 512Mi resolves the OOM crash"],
                    "confidence": 0.94,
                }
            ],
            "hypothesis_updates": [],
            "new_tasks": [],
            "tool_calls_requested": [],
        })
