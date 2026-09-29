"""Synthetic protocol exercise. This does not simulate any SREGym fault or score."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from clients.graphstate.driver.artifacts import ArtifactLog
from clients.graphstate.driver.live import LiveRunner


async def exercise(model: str, output: Path, diagnosis_only=False):
    class Harness:
        stage = "diagnosis"
        changed = False
        checked_health = False
        submissions = []
        commands = []

        def http(self, request):
            if request.url.path == "/get_app":
                return httpx.Response(200, json={"app_name": "synthetic", "namespace": "dry-app", "descriptions": "Synthetic integration fixture"})
            if request.url.path == "/status":
                return httpx.Response(200, json={"stage": self.stage})
            if request.url.path == "/submit":
                data = json.loads(request.content)
                assert data["stage"] == self.stage
                self.submissions.append(data)
                if self.stage == "diagnosis":
                    assert "root_cause" in json.loads(data["solution"])
                    self.stage = "done" if diagnosis_only else "mitigation"
                else:
                    assert self.changed and self.checked_health
                    self.stage = "done"
                return httpx.Response(200, json={"status": "200", "stage": data["stage"], "message": "synthetic receipt, no grade"})
            raise AssertionError(f"Unexpected HTTP request: {request.url}")

        async def call_tool(self, name, arguments):
            assert name == "exec_kubectl_cmd_safely"
            command = arguments["cmd"]
            self.commands.append(command)
            if command.startswith("kubectl patch"):
                assert self.stage == "mitigation"
                self.changed = True
                text = "deployment patched"
            elif self.changed:
                self.checked_health = True
                text = "All service pods Ready, error rate 0 (synthetic data)"
            else:
                text = "Service pod CrashLoopBackOff, OOM failure (synthetic data)"
            return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)

    harness = Harness()

    class Backend:
        def inference(self, prompt):
            context = json.loads(prompt.split("LIVE RUNTIME CONTRACT:\n", 1)[1])
            evidence = context["untrusted_tool_evidence"]
            if context["phase"] == "diagnosis":
                if len(evidence) < 2:
                    command = "kubectl get pods -n dry-app" if not evidence else "kubectl describe deployment service -n dry-app"
                    data = {"tool_calls_requested": [{"tool_name": "exec_kubectl_cmd_safely", "parameters": {"cmd": command}}]}
                else:
                    data = {"stage_complete": True, "new_hypotheses": [{
                        "claim": "Synthetic memory configuration causes service OOM failure",
                        "affected_services": ["service"], "causal_path": ["service"],
                        "supporting_evidence": [evidence[0]["id"], evidence[1]["id"]], "confidence": 0.95,
                    }]}
            elif not harness.changed:
                data = {"tool_calls_requested": [{"tool_name": "exec_kubectl_cmd_safely", "parameters": {
                    "cmd": "kubectl patch deployment service -n dry-app --type merge -p '{\"spec\":{\"replicas\":2}}'"}}]}
            elif not harness.checked_health:
                data = {"tool_calls_requested": [{"tool_name": "exec_kubectl_cmd_safely", "parameters": {"cmd": "kubectl get pods -n dry-app"}}]}
            else:
                data = {"stage_complete": True}
            return SimpleNamespace(content=json.dumps(data), usage_metadata={"input_tokens": 70, "output_tokens": 30, "total_tokens": 100})

    async with httpx.AsyncClient(base_url="http://offline.invalid", transport=httpx.MockTransport(harness.http)) as http:
        artifacts = ArtifactLog(output, model)
        artifacts.record({"type": "dry_run", "synthetic": True, "warning": "No real model, fault, deployment or grading"})
        runner = LiveRunner(Backend(), http, {"exec_kubectl_cmd_safely": harness},
                            {"exec_kubectl_cmd_safely": {"parameters": {"cmd": "string"}}},
                            rounds=1, turns=5, artifacts=artifacts)
        await runner.run()
        artifacts.snapshot(runner.state)
        expected = ["diagnosis"] if diagnosis_only else ["diagnosis", "mitigation"]
        assert [s["stage"] for s in harness.submissions] == expected
        assert runner.state["total_tool_calls"] == (2 if diagnosis_only else 4)
        return {"protocol_passed": True, "stages": expected, "tool_calls": runner.state["total_tool_calls"],
                "synthetic": True, "real_task_success": None}
