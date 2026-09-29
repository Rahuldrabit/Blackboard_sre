"""
Automated Experiment Orchestrator for Secure Blackboard Multi-Agent SRE.

Enables running systematic evaluations on SREGym:
1. Ablation Campaign: S0, M0, M1, M2, M3, M4 across problems.
2. Cross-Model Campaign: ModelGap(M4) vs ModelGap(M0) across weak/frontier models.
3. Fault-Injection Campaign: Error Propagation Radius (EPR) & Recovery Rate (RR).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from clients.graphstate.driver.driver import run_investigation
from clients.graphstate.evaluation.ablation_configs import ABLATION_CONFIGS
from clients.graphstate.evaluation.metrics import SREMultiAgentMetrics
from clients.graphstate.fault_injection.injector import CommunicationFaultInjector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sregym.experiment_runner")

SREGYM_LITE_SAMPLE_PROBLEMS = [
    "cartservice_latency_spike",
    "network_policy_ingress_drop",
    "redis_oom_crashloop",
    "deployment_selector_mismatch",
    "pvc_storage_full",
]


async def run_ablation_experiment(
    problems: list[str],
    configs: list[str],
    model: str = "gemini-2.0-pro",
    stages: list[str] | None = None,
) -> dict[str, Any]:
    stages = stages or ["diagnosis", "mitigation"]
    results: dict[str, list[dict[str, Any]]] = {cfg: [] for cfg in configs}

    for cfg_name in configs:
        logger.info("=== Running Ablation Configuration: %s ===", cfg_name)
        for prob in problems:
            state = await run_investigation(
                problem_id=prob,
                model=model,
                stages=stages,
                config_name=cfg_name,
            )
            diag = state.get("diagnosis")
            mit = state.get("mitigation")

            entry = {
                "problem": prob,
                # The offline driver has no oracle. These fields describe lifecycle
                # completion only and must not be reported as benchmark accuracy.
                "diagnosis_produced": diag is not None,
                "diagnosis_gate_passed": (
                    None if cfg_name == "S0" else bool(
                        diag and diag.diagnosis_method != "fallback_top_candidate"
                    )
                ),
                "mitigation_committed": mit.mitigation_successful if mit else False,
                "tokens": state.get("total_tokens_used", 0),
                "tool_calls": state.get("total_tool_calls", 0),
            }
            results[cfg_name].append(entry)

    # Compute aggregate summary table
    summary_table = []
    for cfg_name, runs in results.items():
        total = len(runs)
        diag_rate = sum(1 for r in runs if r["diagnosis_produced"]) / total if total else 0.0
        gated = [r for r in runs if r["diagnosis_gate_passed"] is not None]
        gate_rate = (
            f"{sum(1 for r in gated if r['diagnosis_gate_passed']) / len(gated) * 100:.1f}%"
            if gated else "N/A"
        )
        commit_rate = sum(1 for r in runs if r["mitigation_committed"]) / total if total else 0.0
        avg_tokens = sum(r["tokens"] for r in runs) / total if total else 0.0

        summary_table.append({
            "Configuration": cfg_name,
            "DiagnosisProduced": f"{diag_rate*100:.1f}%",
            "DiagnosisGatePassed": gate_rate,
            "SyntheticMitigationCommitted": f"{commit_rate*100:.1f}%",
            "AvgTokens": int(avg_tokens),
        })

    return {
        "evaluation_type": "offline_simulation",
        "benchmark_scores": False,
        "warning": (
            "No real SREGym faults, model inference, grading, or oracle comparison occurred. "
            "Lifecycle rates are not diagnosis accuracy or mitigation success."
        ),
        "summary": summary_table,
        "runs": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SREGym Multi-Agent Experiments")
    parser.add_argument("--configs", nargs="+", default=["S0", "M2", "M4"], help="Configs to evaluate")
    parser.add_argument("--problems", nargs="+", default=SREGYM_LITE_SAMPLE_PROBLEMS[:2], help="Problems to run")
    parser.add_argument("--model", type=str, default="gemini-2.0-pro", help="Model name")
    parser.add_argument("--stages", type=str, default="diagnosis,mitigation", help="Stages to test")
    args = parser.parse_args()

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    experiment_results = asyncio.run(
        run_ablation_experiment(
            problems=args.problems,
            configs=args.configs,
            model=args.model,
            stages=stages,
        )
    )

    print("\n" + "=" * 60)
    print("           EXPERIMENTAL RESULTS SUMMARY")
    print("=" * 60)
    print("OFFLINE SIMULATION ONLY — these are not SREGym benchmark scores.")
    print(json.dumps(experiment_results["summary"], indent=2))


if __name__ == "__main__":
    main()
