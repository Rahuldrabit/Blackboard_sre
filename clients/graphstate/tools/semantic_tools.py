"""
Semantic tools layer for Secure Blackboard SRE.

Provides high-level, token-efficient semantic tool abstractions over raw SREGym MCP tools:
- observe_service: returns consolidated service health (pods, endpoints, error rate)
- get_dependency_graph: extracts service dependencies from cluster topology or traces
- find_metric_anomalies: detects >3σ departures from baseline in latency/errors/resources
- get_recent_changes: returns recent rollouts, config modifications, pod restarts
- inspect_resource_pressure: OOMKilled pods, CPU throttling, disk/PVC saturation
- trace_failure_path: upstream-to-downstream error propagation chain
- probe_dependency: synthetic TCP/HTTP probe between two microservices

Raw fallback tools are also supported:
- query_prometheus
- query_logs
- query_trace
- kubectl_read
- readonly_exec
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Tool schema definitions for prompt compilation and MCP registration
SEMANTIC_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "observe_service",
        "description": "Observe high-level health, pod statuses, and recent error rates for a service.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Target service name, e.g. 'cartservice'"},
                "namespace": {"type": "string", "description": "Kubernetes namespace (default 'default')", "default": "default"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_dependency_graph",
        "description": "Get the upstream and downstream service dependency graph for an incident or target service.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Optional service to center on"},
                "depth": {"type": "integer", "description": "Traversal depth (default 2)", "default": 2},
            },
        },
    },
    {
        "name": "find_metric_anomalies",
        "description": "Scan metrics for anomalous spikes (CPU, memory, latency, HTTP 5xx errors).",
        "parameters": {
            "type": "object",
            "properties": {
                "time_window_minutes": {"type": "integer", "description": "Window to check (default 15)", "default": 15},
                "threshold_sigma": {"type": "number", "description": "Anomaly threshold in standard deviations", "default": 3.0},
            },
        },
    },
    {
        "name": "get_recent_changes",
        "description": "Retrieve recent deployments, config updates, image revisions, or pod restarts.",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace", "default": "default"},
                "since_minutes": {"type": "integer", "description": "Lookback duration in minutes", "default": 30},
            },
        },
    },
    {
        "name": "inspect_resource_pressure",
        "description": "Inspect CPU throttling, memory limits, OOMKilled events, and PVC capacity.",
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Namespace", "default": "default"},
                "service": {"type": "string", "description": "Optional service filter"},
            },
        },
    },
    {
        "name": "trace_failure_path",
        "description": "Trace request propagation chain to locate the earliest point of failure or latency.",
        "parameters": {
            "type": "object",
            "properties": {
                "root_service": {"type": "string", "description": "Service reporting errors (e.g. 'frontend')"},
                "sample_size": {"type": "integer", "description": "Number of error traces to inspect", "default": 5},
            },
            "required": ["root_service"],
        },
    },
    {
        "name": "probe_dependency",
        "description": "Simulate network, DNS, or auth probe between source and destination services.",
        "parameters": {
            "type": "object",
            "properties": {
                "source_service": {"type": "string", "description": "Source pod/service"},
                "target_service": {"type": "string", "description": "Target pod/service"},
                "port": {"type": "integer", "description": "Target port", "default": 80},
                "probe_type": {"type": "string", "enum": ["dns", "tcp", "http", "auth"], "default": "tcp"},
            },
            "required": ["source_service", "target_service"],
        },
    },
]

RAW_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "query_prometheus",
        "description": "Execute a raw PromQL query against the cluster Prometheus instance.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL query string"},
                "step": {"type": "string", "description": "Resolution step, e.g. '15s'", "default": "15s"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_logs",
        "description": "Fetch container logs for a pod or deployment.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_or_pod": {"type": "string", "description": "Pod name or deployment name"},
                "lines": {"type": "integer", "description": "Max lines to return", "default": 50},
                "filter_pattern": {"type": "string", "description": "Regex or keyword to filter logs"},
            },
            "required": ["service_or_pod"],
        },
    },
    {
        "name": "kubectl_read",
        "description": "Execute read-only kubectl commands (get, describe, logs). Modifications are forbidden.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "kubectl subcommand arguments, e.g. 'get pods -n default'"},
            },
            "required": ["command"],
        },
    },
]
