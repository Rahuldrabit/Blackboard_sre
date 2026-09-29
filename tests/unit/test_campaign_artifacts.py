import importlib.util
import json
import sys
from pathlib import Path

import pytest

from clients.graphstate.driver.artifacts import ArtifactLog

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from install_sregym import CONFIGURED_RESULTS, ORIGINAL_RESULTS, install
from run_campaign import build_command, collect_feedback, lite_problems, slug
from offline_harness import exercise


def test_incremental_artifacts_separate_roles_and_survive_missing_final_snapshot(tmp_path):
    log = ArtifactLog(tmp_path, "openrouter/test/model")
    log.record({"type": "model_response", "role": "topology", "content": "first"})
    log.record({"type": "model_response", "role": "telemetry", "content": "second"})
    log.record({"type": "tool", "role": "topology", "output": "actual output"})
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 3
    topology = json.loads((tmp_path / "agents/topology.jsonl").read_text())
    assert topology["content"] == "first"
    assert topology["model"] == "openrouter/test/model"
    assert topology["timestamp"].endswith("+00:00")
    assert "second" not in (tmp_path / "agents/topology.jsonl").read_text()
    assert (tmp_path / "tools.jsonl").exists()


def test_feedback_preserves_judge_reasoning_and_failed_outcomes(tmp_path):
    path = tmp_path / "sregym/results.csv"
    path.parent.mkdir()
    path.write_text('problem_id,diagnosis.success,diagnosis.reasoning,mitigation.success\ntask,False,"Wrong component, needs evidence",False\n')
    assert collect_feedback(tmp_path)
    row = json.loads((tmp_path / "sregym_feedback.json").read_text())["files"]["sregym/results.csv"][0]
    assert row["diagnosis.success"] == "False"
    assert row["diagnosis.reasoning"] == "Wrong component, needs evidence"


def test_single_problem_command_avoids_mutually_exclusive_suite_flag(tmp_path):
    command = build_command(tmp_path, "task", "openrouter/qwen/model", "openrouter/qwen/judge", ["diagnosis"])
    assert "--problem" in command and "--suite" not in command
    assert command[command.index("--model") + 1] == "openrouter/qwen/model"
    assert "--force-build" not in command
    assert "/" not in slug("openrouter/qwen/model")


def test_installer_is_idempotent_and_rejects_destination_edits(tmp_path):
    (tmp_path / "sregym").mkdir()
    (tmp_path / "sregym/agent_registry.py").write_text("")
    (tmp_path / "agents.yaml").write_text('agents:\n  # keep this comment\n  - name: other\n')
    (tmp_path / "main.py").write_text(ORIGINAL_RESULTS)
    install(tmp_path)
    install(tmp_path)
    assert "# keep this comment" in (tmp_path / "agents.yaml").read_text()
    assert CONFIGURED_RESULTS in (tmp_path / "main.py").read_text()
    live = tmp_path / "clients/graphstate/driver/live.py"
    live.write_text(live.read_text() + '\n# manual edit\n')
    with pytest.raises(ValueError, match="untracked edits"):
        install(tmp_path)


def test_lite_suite_is_read_without_importing_problem_code(tmp_path):
    path = tmp_path / "sregym/conductor/problem_sets.py"
    path.parent.mkdir(parents=True)
    path.write_text('raise RuntimeError("Do not execute this file")\nSREGYM_LITE_PROBLEMS = ("a", "b")\n')
    assert lite_problems(tmp_path) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnosis_only", [False, True])
async def test_offline_lifecycle_uses_real_gate_and_no_provider(tmp_path, diagnosis_only):
    result = await exercise("synthetic-model", tmp_path, diagnosis_only)
    assert result["protocol_passed"] and result["real_task_success"] is None
    assert (tmp_path / "state.json").exists()
