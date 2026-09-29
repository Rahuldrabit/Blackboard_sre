"""
Tool node and execution engine for Secure Blackboard SRE.

Responsibilities:
1. Receives tool call requests from agent specialists.
2. Checks QueryCache: if a fresh result exists, returns it immediately without cluster overhead.
3. If not cached, executes the tool (via SREGym Conductor HTTP client or local fallback).
4. Stores response in QueryCache.
5. Converts response into a structured Observation:
   - Sets knowledge_level = KnowledgeLevel.OBSERVED
   - Quarantines unparsed output in `raw_data`
   - Generates a concise `summary` safe for prompt compilation
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from clients.graphstate.state.knowledge_levels import KnowledgeLevel
from clients.graphstate.state.query_cache import QueryCache
from clients.graphstate.state.schema import Observation, ObservationType

logger = logging.getLogger(__name__)


class ToolExecutionNode:
    """Manages tool execution, result caching, and observation creation."""

    def __init__(
        self,
        query_cache: QueryCache | None = None,
        conductor_client: Any | None = None,
        mock_mode: bool = False,
    ):
        self.cache = query_cache or QueryCache()
        self.conductor = conductor_client
        self.mock_mode = mock_mode
        self._next_obs_id = 1

    def _generate_obs_id(self) -> str:
        obs_id = f"E{self._next_obs_id:03d}"
        self._next_obs_id += 1
        return obs_id

    async def execute_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        author_agent: str,
        service: str | None = None,
    ) -> Observation:
        """
        Execute tool with query caching and epistemic quarantine.
        """
        # 1. Check QueryCache
        cached_result = self.cache.get(tool_name, parameters)
        if cached_result is not None:
            logger.info("Cache hit for tool '%s' (params=%s)", tool_name, parameters)
            summary = cached_result.get("summary", f"Cached result from {tool_name}")
            raw = cached_result.get("raw_data")
            obs_type = cached_result.get("type", ObservationType.METRIC)
        else:
            # 2. Execute via Conductor or fallback mock
            raw_output, summary, obs_type = await self._dispatch_tool(tool_name, parameters)
            
            # Cache the result
            self.cache.set(
                tool_name=tool_name,
                params=parameters,
                result={"summary": summary, "raw_data": raw_output, "type": obs_type},
            )

        # 3. Create structured Observation
        obs = Observation(
            id=self._generate_obs_id(),
            timestamp=datetime.now(timezone.utc),
            source=tool_name,
            author_agent=author_agent,
            service=service or parameters.get("service") or parameters.get("service_or_pod"),
            observation_type=obs_type,
            summary=summary,
            raw_data=raw_output if isinstance(raw_output, str) else str(raw_output),
            tool_name=tool_name,
            tool_params=parameters,
            knowledge_level=KnowledgeLevel.OBSERVED,
        )

        return obs

    async def _dispatch_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> tuple[str, str, ObservationType]:
        """Dispatch tool call to backend."""
        if self.conductor and not self.mock_mode:
            try:
                # Real SREGym conductor invocation
                resp = await self.conductor.call_tool(tool_name, parameters)
                raw = str(resp)
                summary = self._summarize_raw_output(tool_name, raw)
                obs_type = self._infer_obs_type(tool_name)
                return raw, summary, obs_type
            except Exception as e:
                logger.error("Error calling conductor tool '%s': %s", tool_name, e)
                return str(e), f"Tool execution failed: {e}", ObservationType.K8S_EVENT

        # Mock / Fallback mode (for testing and local offline validation)
        return self._mock_tool_output(tool_name, parameters)

    def _infer_obs_type(self, tool_name: str) -> ObservationType:
        if "metric" in tool_name or "prometheus" in tool_name:
            return ObservationType.METRIC
        if "log" in tool_name:
            return ObservationType.LOG
        if "trace" in tool_name:
            return ObservationType.TRACE
        if "kubectl" in tool_name or "change" in tool_name or "resource" in tool_name:
            return ObservationType.CONFIG
        return ObservationType.TOPOLOGY

    def _summarize_raw_output(self, tool_name: str, raw: str) -> str:
        first_line = raw.strip().split("\n")[0][:120]
        return f"{tool_name} returned: {first_line}"

    def _mock_tool_output(
        self,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> tuple[str, str, ObservationType]:
        service = parameters.get("service", parameters.get("service_or_pod", "unknown"))
        if tool_name == "observe_service":
            raw = f"Status: Degraded. Pod {service}-7f9d8 in CrashLoopBackOff. Restarts: 4."
            summary = f"Service '{service}' degraded with CrashLoopBackOff (restarts: 4)"
            return raw, summary, ObservationType.CONFIG
        elif tool_name == "query_logs":
            raw = f"FATAL 2026-09-26T23:00:01Z [redis.go:42] connection refused to redis:6379"
            summary = f"Logs for '{service}' indicate connection refused to redis:6379"
            return raw, summary, ObservationType.LOG
        elif tool_name == "find_metric_anomalies":
            raw = f"checkoutservice latency p99 spiked to 4200ms (+450% from baseline 50ms)"
            summary = "checkoutservice latency p99 anomaly detected at 4200ms"
            return raw, summary, ObservationType.METRIC
        elif tool_name == "get_recent_changes":
            raw = f"Deployment '{service}' rolled out revision 4 with updated image tag v2.1-broken"
            summary = f"Deployment '{service}' recently updated to image v2.1-broken"
            return raw, summary, ObservationType.CONFIG
        else:
            raw = f"Result of {tool_name} with params {parameters}"
            summary = f"Tool {tool_name} executed successfully for {service}"
            return raw, summary, self._infer_obs_type(tool_name)
