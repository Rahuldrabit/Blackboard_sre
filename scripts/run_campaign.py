"""Serial cloud campaigns with date/model/task logs, or a network-free dry run."""
from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import fcntl
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from install_sregym import CONFIGURED_RESULTS

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "qwen": "openrouter/qwen/qwen3.5-flash-02-23",
    "deepseek": "openrouter/deepseek/deepseek-v4-flash",
}
AGENT_NAME = "blackboard-sre"


def slug(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, default=str) + "\n")
    temporary.replace(path)


def lite_problems(checkout):
    tree = ast.parse((checkout / "sregym/conductor/problem_sets.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SREGYM_LITE_PROBLEMS" for t in node.targets):
            result = list(ast.literal_eval(node.value))
            if not result or len(result) != len(set(result)):
                raise ValueError("Lite suite is empty or has duplicate task IDs")
            return result
    raise ValueError("Cannot locate upstream SREGym Lite suite")


def static_checks(checkout, problems):
    tree = ast.parse((checkout / "sregym/conductor/problems/registry.py").read_text())
    registered = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "PROBLEM_REGISTRY" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                registered.update(k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))
    if set(problems) - registered:
        raise ValueError(f"Unregistered tasks: {set(problems) - registered}")
    registrations = yaml.safe_load((checkout / "agents.yaml").read_text())["agents"]
    agent = next((a for a in registrations if a["name"] == AGENT_NAME), None)
    if not agent or agent.get("kickoff_command") != "python -m clients.graphstate.driver.live" or agent.get("container_isolation") is not True:
        raise ValueError(f"Live isolated {AGENT_NAME} adapter is not installed")
    main_text = (checkout / "main.py").read_text()
    if CONFIGURED_RESULTS not in main_text:
        raise ValueError("Run scripts/install_sregym.py to enable separated result directories")
    compile(main_text, str(checkout / "main.py"), "exec")
    for source in (ROOT / "clients/graphstate").rglob("*.py"):
        target = checkout / "clients/graphstate" / source.relative_to(ROOT / "clients/graphstate")
        if not target.exists() or target.read_bytes() != source.read_bytes():
            raise ValueError(f"Installed agent is outdated: {target}; reinstall it")
        compile(target.read_text(), str(target), "exec")
    applications = checkout / "SREGym-applications"
    for directory in ("hotelReservation", "socialNetwork", "astronomy-shop"):
        if not (applications / directory).is_dir():
            raise ValueError(f"Missing applications submodule content: {directory}")
    return {"registered_tasks": len(problems), "installed_source_matches": True,
            "result_directory_support": True, "isolation_enabled": True, "application_submodule_present": True}


def build_command(checkout, problem, model, judge, stages, *, force_build=False, timeout=1800, profile="full"):
    command = ["uv", "run", "main.py", "--problem", problem, "--agent", AGENT_NAME,
               "--model", model, "--judge-model", judge, "--stages", *stages,
               "--profile", profile, "--agent-timeout", str(timeout),
               "--allow-agent-endpoint", "https://openrouter.ai"]
    if force_build:
        command.append("--force-build")
    return command


def collect_feedback(task_dir):
    files = {}
    for path in sorted((task_dir / "sregym").rglob("*.csv")):
        with path.open(newline="") as handle:
            files[str(path.relative_to(task_dir))] = list(csv.DictReader(handle))
    result = {"source": "native_sregym_csv", "files": files,
              "note": "CSV values are preserved as strings, including judge reasoning and mitigation feedback."}
    write_json(task_dir / "sregym_feedback.json", result)
    return bool(files)


def feedback_run_statuses(task_dir):
    """Return distinct native run statuses from the collected SREGym CSV rows."""
    path = task_dir / "sregym_feedback.json"
    if not path.exists():
        return []
    feedback = json.loads(path.read_text())
    return sorted({row["run_status"] for rows in feedback.get("files", {}).values()
                   for row in rows if row.get("run_status")})


def prior_complete_task(base, model, problem):
    """Find a prior operationally complete run for resume mode."""
    root = base / slug(model) / problem
    for outcome in sorted(root.glob("*/outcome.json"), reverse=True):
        try:
            if json.loads(outcome.read_text()).get("operationally_complete") is True:
                return outcome.parent
        except (OSError, ValueError):
            continue
    return None


def prior_deploy_failed_task(base, model, problem):
    """Find a prior run that failed before deployment completed."""
    root = base / slug(model) / problem
    for outcome in sorted(root.glob("*/outcome.json"), reverse=True):
        try:
            if json.loads(outcome.read_text()).get("native_run_statuses") == []:
                feedback = outcome.parent / "sregym_feedback.json"
                if "deploy_failed" in feedback.read_text() if feedback.exists() else False:
                    return outcome.parent
        except (OSError, ValueError):
            continue
    return None


def run_live(checkout, task_dir, command, model, judge):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY locally before a live run")
    env = {**os.environ, "AGENT_API_KEY": key, "JUDGE_API_KEY": key,
           "AGENT_API_BASE": "https://openrouter.ai/api/v1", "JUDGE_API_BASE": "https://openrouter.ai/api/v1",
           "SREGYM_RESULTS_DIR": str(task_dir / "sregym"), "AGENT_LOGS_DIR": str(task_dir / "host_logs"),
           "LLM_USAGE_LOG_PATH": str(task_dir / "judge_usage.jsonl"), "PYTHONUNBUFFERED": "1"}
    with (task_dir / "console.log").open("w") as log:
        process = subprocess.Popen(command, cwd=checkout, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            return_code = process.wait()
        except BaseException:
            # Give the harness a chance to clean up; do not start another task.
            process.send_signal(2)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            raise
        finally:
            feedback_present = collect_feedback(task_dir)
    statuses = feedback_run_statuses(task_dir)
    return {"exit_code": return_code, "native_feedback_present": feedback_present,
            "native_run_statuses": statuses,
            "operationally_complete": bool(statuses) and "incomplete" not in statuses,
            "model": model, "judge_model": judge, "real_task_success": "see sregym_feedback.json"}


def revision(checkout):
    return subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sregym", type=Path, required=True)
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--problem", choices=None, help="One registered Lite task; default is the entire suite")
    parser.add_argument("--stages", nargs="+", choices=["diagnosis", "mitigation"], default=["diagnosis", "mitigation"])
    parser.add_argument("--judge-model", default=os.environ.get("BLACKBOARD_JUDGE_MODEL", MODELS["qwen"]))
    parser.add_argument("--results-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip tasks with a prior operationally complete result")
    parser.add_argument("--skip-deploy-failed", action="store_true", help="Skip tasks previously blocked before deployment")
    parser.add_argument("--continue-on-failure", action="store_true", help="Continue after a task has a native incomplete result")
    parser.add_argument("--skip-astronomy", action="store_true", help="Skip Astronomy Shop tasks when its external images are unavailable")
    parser.add_argument("--agent-timeout", type=int, default=1800)
    parser.add_argument("--profile", choices=["full", "svelte"], default="full")
    args = parser.parse_args()
    if args.stages not in (["diagnosis"], ["mitigation"], ["diagnosis", "mitigation"]):
        parser.error("Stages must be diagnosis then mitigation, or a single stage")
    if args.agent_timeout <= 0:
        parser.error("--agent-timeout must be positive")
    checkout = args.sregym.resolve()
    problems = lite_problems(checkout)
    if args.problem:
        if args.problem not in problems:
            parser.error("--problem must belong to the selected checkout's Lite suite")
        problems = [args.problem]
    checks = static_checks(checkout, problems)
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%H%M%S") + "_" + uuid.uuid4().hex[:8]
    category = "dry-run" if args.dry_run else "live"
    base = args.results_dir.resolve() / category / now.strftime("%Y-%m-%d")
    summary_path = base / f"campaign_{run_id}.json"
    summary = {"dry_run": args.dry_run, "started_at": now.isoformat(), "timezone": "UTC", "checks": checks,
               "sregym_revision": revision(checkout), "applications_revision": revision(checkout / "SREGym-applications"),
               "judge_model": args.judge_model, "runs": [],
               "limits": "Dry runs use synthetic evidence and model outputs. They do not test fault deployment, model quality, recovery or oracle scores."}
    write_json(summary_path, summary)
    # Serialize campaigns sharing the same checkout/cluster.
    with (checkout / ".graphstate-campaign.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for alias in args.models:
            model = MODELS[alias]
            for problem in problems:
                if args.skip_astronomy and "astronomy_shop" in problem:
                    record = {"model": model, "problem": problem, "skipped": True,
                              "operationally_complete": False, "skip_reason": "astronomy_images_unavailable",
                              "started_at": datetime.now(timezone.utc).isoformat(),
                              "finished_at": datetime.now(timezone.utc).isoformat()}
                    summary["runs"].append(record)
                    write_json(summary_path, summary)
                    print(f"SKIP astronomy images unavailable: {problem}", flush=True)
                    continue
                if args.resume and not args.dry_run:
                    prior = prior_complete_task(base, model, problem)
                    if prior:
                        record = {"model": model, "problem": problem, "skipped": True,
                                  "resumed_from": str(prior), "operationally_complete": True,
                                  "started_at": datetime.now(timezone.utc).isoformat(),
                                  "finished_at": datetime.now(timezone.utc).isoformat()}
                        summary["runs"].append(record)
                        write_json(summary_path, summary)
                        print(f"RESUME skip {alias}: {problem} ← {prior}", flush=True)
                        continue
                if args.skip_deploy_failed and not args.dry_run:
                    prior = prior_deploy_failed_task(base, model, problem)
                    if prior:
                        record = {"model": model, "problem": problem, "skipped": True,
                                  "resumed_from": str(prior), "operationally_complete": False,
                                  "skip_reason": "prior_deploy_failed",
                                  "started_at": datetime.now(timezone.utc).isoformat(),
                                  "finished_at": datetime.now(timezone.utc).isoformat()}
                        summary["runs"].append(record)
                        write_json(summary_path, summary)
                        print(f"RESUME skip deploy failure {alias}: {problem} ← {prior}", flush=True)
                        continue
                task_dir = base / slug(model) / problem / run_id
                task_dir.mkdir(parents=True, exist_ok=False)
                # Each task is a separate SREGym process so its artifacts can be
                # isolated. Force the local agent image for every process;
                # otherwise later tasks try to pull the release digest instead
                # of reusing the locally installed Blackboard driver.
                command = build_command(checkout, problem, model, args.judge_model, args.stages,
                                        force_build=True, timeout=args.agent_timeout, profile=args.profile)
                record = {"model": model, "problem": problem, "output": str(task_dir), "command": command,
                          "started_at": datetime.now(timezone.utc).isoformat()}
                write_json(task_dir / "manifest.json", {**record, "dry_run": args.dry_run,
                           "sregym_revision": summary["sregym_revision"], "judge_model": args.judge_model})
                try:
                    if args.dry_run:
                        from offline_harness import exercise
                        if args.stages == ["mitigation"]:
                            raise ValueError("Synthetic dry run currently requires diagnosis or both stages")
                        record.update(asyncio.run(exercise(model, task_dir / "agent", args.stages == ["diagnosis"])))
                    else:
                        record.update(run_live(checkout, task_dir, command, model, args.judge_model))
                except BaseException as exc:
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["finished_at"] = datetime.now(timezone.utc).isoformat()
                    summary["runs"].append(record)
                    write_json(task_dir / "outcome.json", record)
                    write_json(summary_path, summary)
                    raise
                record["finished_at"] = datetime.now(timezone.utc).isoformat()
                summary["runs"].append(record)
                write_json(task_dir / "outcome.json", record)
                write_json(summary_path, summary)
                print(f"{'DRY-RUN' if args.dry_run else 'LIVE'} {alias}: {problem} → {task_dir}", flush=True)
                if (not args.dry_run and (record["exit_code"] != 0 or not record["operationally_complete"])
                        and not args.continue_on_failure):
                    raise SystemExit(f"SREGym failed; campaign stopped to avoid running the next task on an unclean cluster. Logs: {task_dir}")
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(summary_path, summary)
    print(f"Campaign report: {summary_path}")


if __name__ == "__main__":
    main()
