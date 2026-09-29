import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from clients.graphstate.agents.topology import TopologySpecialist
from clients.graphstate.driver.live import LiveRunner, parse_response, validate_command


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
async def test_missing_usage_fails_without_tool_execution():
    run = runner()
    run.backend.inference = lambda prompt: SimpleNamespace(content="{}", usage_metadata=None)
    with pytest.raises(RuntimeError, match="no token usage"):
        await run.turn(TopologySpecialist())
    assert not run.state["observations"]
    await run.http.aclose()
