import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from clients.graphstate.agents.topology import TopologySpecialist
from clients.graphstate.driver.live import LiveRunner, parse_response, validate_command
from clients.graphstate.state.schema import Hypothesis


@pytest.mark.parametrize("command", [
    'kubectl delete pod x', 'kubectl exec x -- sh', 'kubectl get pods; touch /tmp/x',
    'kubectl get --raw=/api', 'kubectl get pods --kubeconfig=x', 'kubectl get pods -f x',
    'kubectl get pods $(whoami)', 'kubectl patch deployment x -p {}',
])
def test_diagnosis_rejects_mutation_and_shell(command):
    with pytest.raises(ValueError):
        validate_command(command, False)


def test_scoped_mutation_and_reads():
    validate_command('kubectl get pods -n example -o json', False)
    validate_command('kubectl patch deployment x -n example --type merge -p \'{"spec":{"replicas":2}}\'', True)


def runner(response=None, handler=None):
    class Backend:
        def inference(self, prompt):
            self.prompt = prompt
            return SimpleNamespace(content=json.dumps(response or {}), usage_metadata={"total_tokens": 31})
    async def default_handler(request):
        return httpx.Response(200, json={"status": "200", "stage": "diagnosis"})
    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler or default_handler))
    session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        content=[SimpleNamespace(text="CrashLoopBackOff: OOM failure")], isError=False)))
    name = "exec_kubectl_cmd_safely"
    return LiveRunner(Backend(), client, {name: session}, {name: {}})


@pytest.mark.asyncio
async def test_actual_tools_create_evidence_and_usage_is_measured():
    run = runner({"new_observations": [{"summary": "invented"}], "tokens_used": 999,
                  "tool_calls_requested": [{"tool_name": "exec_kubectl_cmd_safely",
                                             "parameters": {"cmd": "kubectl get pods"}}]})
    await run.turn(TopologySpecialist())
    assert len(run.state["observations"]) == 1
    assert "OOM" in run.state["observations"][0].raw_data
    assert run.state["total_tokens_used"] == 31
    assert run.state["total_tool_calls"] == 1
    await run.http.aclose()


@pytest.mark.asyncio
async def test_tool_failures_do_not_create_observations():
    run = runner()
    run.sessions["exec_kubectl_cmd_safely"].call_tool.return_value.isError = True
    with pytest.raises(RuntimeError, match="Tool failed"):
        await run.execute("exec_kubectl_cmd_safely", {"cmd": "kubectl get pods"}, "topology")
    assert not run.state["observations"]
    await run.http.aclose()


@pytest.mark.asyncio
async def test_tool_timeout_is_recorded_without_observation():
    run = runner()
    run.tool_timeout = 0.01

    async def never_returns(*args, **kwargs):
        await __import__("asyncio").sleep(1)

    run.sessions["exec_kubectl_cmd_safely"].call_tool.side_effect = never_returns
    with pytest.raises(RuntimeError, match="Tool timed out"):
        await run.execute("exec_kubectl_cmd_safely", {"cmd": "kubectl get pods"}, "topology")
    assert not run.state["observations"]
    assert any(event["type"] == "tool_error" and "timed out" in event["error"] for event in run.events)
    await run.http.aclose()


@pytest.mark.asyncio
async def test_mcp_transport_error_becomes_recoverable_runtime_error():
    run = runner()
    run.sessions["exec_kubectl_cmd_safely"].call_tool.side_effect = OSError("session closed")
    with pytest.raises(RuntimeError, match="MCP tool call failed"):
        await run.execute("exec_kubectl_cmd_safely", {"cmd": "kubectl get pods"}, "topology")
    assert not run.state["observations"]
    await run.http.aclose()


@pytest.mark.asyncio
async def test_submission_protocol_and_rejection():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "rejected"})
    run = runner(handler=handler)
    with pytest.raises(RuntimeError, match="Submission rejected"):
        await run.submit("diagnosis", "answer")
    assert requests == [{"stage": "diagnosis", "solution": "answer"}]
    assert not any(event["type"] == "submission" for event in run.events)
    await run.http.aclose()


@pytest.mark.asyncio
async def test_gate_failure_never_submits_or_mitigates():
    run = runner()
    run.rounds = run.turns = 1
    run.api = AsyncMock(side_effect=[{"namespace": "test"}, {"stage": "diagnosis"}])
    run.submit = AsyncMock()
    with pytest.raises(RuntimeError, match="Diagnosis gate did not pass"):
        await run.run()
    run.submit.assert_not_awaited()
    assert run.state["phase"] == "diagnosis"
    await run.http.aclose()


@pytest.mark.asyncio
async def test_hallucinated_evidence_is_not_accepted():
    run = runner({"new_hypotheses": [{"claim": "failure", "supporting_evidence": ["fake"]}]})
    await run.turn(TopologySpecialist())
    assert not run.state["hypotheses"]
    await run.http.aclose()


@pytest.mark.asyncio
async def test_uppercase_hypothesis_status_does_not_crash_turn():
    run = runner({"hypothesis_updates": [{
        "hypothesis_id": "missing", "confidence_delta": -0.1, "reason": "test",
        "new_supporting_evidence": [], "new_contradictory_evidence": [], "status_update": "ACTIVE",
    }]})
    await run.turn(TopologySpecialist())
    await run.http.aclose()


@pytest.mark.asyncio
async def test_diagnosis_only_exits_after_harness_done():
    run = runner({"stage_complete": True})
    run.api = AsyncMock(side_effect=[{"namespace": "test"}, {"stage": "diagnosis"}, {"stage": "done"}])
    diagnosis = SimpleNamespace(model_dump=lambda **kw: {"root_cause": "test"})
    run.gate = lambda: SimpleNamespace(approved=True, diagnosis=diagnosis)
    run.submit = AsyncMock()
    await run.run()
    run.submit.assert_awaited_once_with("diagnosis", '{"root_cause": "test"}')
    assert run.state["phase"] == "diagnosis"
    await run.http.aclose()


def test_invalid_json_fails_instead_of_mocking():
    with pytest.raises(json.JSONDecodeError):
        parse_response("provider failed")
    assert parse_response('```json\n{"stage_complete": true}\n```')["stage_complete"]


def test_qwen_reasoning_before_fenced_json_is_accepted():
    content = 'analysis before output\n</think>\n```json\n{"stage_complete": false}\n```'
    assert parse_response(content) == {"stage_complete": False}


def test_qwen_reasoning_before_unfenced_json_is_accepted():
    content = 'analysis before output\n</think>\n{"tool_calls_requested": []}'
    assert parse_response(content) == {"tool_calls_requested": []}


def test_live_state_uses_harness_artifact_identity(monkeypatch):
    monkeypatch.setenv("SREGYM_ARTIFACT_ID", "anon_test_identity")
    run = runner()
    assert run.state["problem_id"] == "anon_test_identity"


@pytest.mark.asyncio
async def test_stages_submit_once_in_order_without_claiming_success():
    run = runner({"stage_complete": True})
    run.api = AsyncMock(side_effect=[{"namespace": "test"}, {"stage": "diagnosis"}, {"stage": "mitigation"}])
    diagnosis = SimpleNamespace(model_dump=lambda **kw: {"root_cause": "test"})
    run.gate = lambda: SimpleNamespace(approved=True, diagnosis=diagnosis)
    run.submit = AsyncMock()
    await run.run()
    assert [call.args[0] for call in run.submit.await_args_list] == ["diagnosis", "mitigation"]
    assert run.state["mitigation"] is None  # Only the harness can grade success.
    await run.http.aclose()


@pytest.mark.asyncio
async def test_mitigation_budget_exhaustion_is_submitted_for_harness_grading():
    run = runner({"stage_complete": True})
    run.api = AsyncMock(side_effect=[{"namespace": "test"}, {"stage": "diagnosis"}, {"stage": "mitigation"}])
    diagnosis = SimpleNamespace(model_dump=lambda **kw: {"root_cause": "test"})
    run.gate = lambda: SimpleNamespace(approved=True, diagnosis=diagnosis, reasons=[])
    run.submit = AsyncMock()
    run.turn = AsyncMock(return_value=(True, ""))
    run.mitigation_token_budget = 0
    await run.run()
    assert [call.args[0] for call in run.submit.await_args_list] == ["diagnosis", "mitigation"]
    assert any(event["type"] == "mitigation_fallback" for event in run.events)
    await run.http.aclose()


@pytest.mark.asyncio
async def test_missing_usage_fails_without_tool_execution():
    run = runner()
    run.backend.inference = lambda prompt: SimpleNamespace(content="{}", usage_metadata=None)
    with pytest.raises(RuntimeError, match="no token usage"):
        await run.turn(TopologySpecialist())
    assert not run.state["observations"]
    await run.http.aclose()


@pytest.mark.asyncio
async def test_response_crossing_token_budget_is_processed_before_stopping():
    run = runner({"stage_complete": True})
    run.state["token_budget_remaining"] = 1
    complete, _ = await run.turn(TopologySpecialist())
    assert complete is True
    assert run.state["token_budget_remaining"] == 0
    assert any(event["type"] == "model" for event in run.events)
    await run.http.aclose()


def test_live_runner_uses_bounded_turns_for_expensive_remote_models():
    run = runner()
    assert run.turns == 2
    assert run.token_reserve == 15_000
    assert run.mitigation_token_budget == 50_000


def test_gate_fallback_uses_best_active_hypothesis_for_harness_grading():
    run = runner()
    run.state["hypotheses"] = [Hypothesis(
        id="H1", timestamp=datetime.now(timezone.utc),
        author_agent="topology", claim="service dependency is unavailable",
        affected_services=["frontend"], causal_path=["frontend"],
        supporting_evidence=["E1", "E2"], contradictory_evidence=[],
        untested_predictions=[], confidence=0.6,
    )]
    gate = SimpleNamespace(reasons=["confidence below threshold"])
    diagnosis = run.best_effort_diagnosis(gate)
    assert diagnosis.root_cause == "service dependency is unavailable"
    assert diagnosis.diagnosis_method == "best_effort_gate_fallback"
    assert run.events[-1]["type"] == "gate_fallback"
