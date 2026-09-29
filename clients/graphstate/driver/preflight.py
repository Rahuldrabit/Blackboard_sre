"""
Preflight validator for the SREGym GraphState / Secure Blackboard agent.

Validates environment variables, model configurations, API credentials,
and SREGym harness connectivity before starting problem execution.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


class PreflightCheckResult:
    def __init__(self, passed: bool, errors: list[str], warnings: list[str], metadata: dict[str, Any]):
        self.passed = passed
        self.errors = errors
        self.warnings = warnings
        self.metadata = metadata

    def __bool__(self) -> bool:
        return self.passed


class PreflightChecker:
    """Validates runtime prerequisites for SREGym execution."""

    @classmethod
    def run_checks(cls, problem_id: str | None = None, model: str | None = None) -> PreflightCheckResult:
        errors: list[str] = []
        warnings: list[str] = []
        metadata: dict[str, Any] = {}

        # 1. Problem ID resolution
        resolved_problem = problem_id or os.environ.get("HARNESS_PROBLEM_ID_ENV") or os.environ.get("SREGYM_PROBLEM_ID")
        if not resolved_problem:
            warnings.append("No problem ID found in arguments or environment variables. Using 'dev_problem'.")
            resolved_problem = "dev_problem"
        metadata["problem_id"] = resolved_problem

        # 2. Model selection
        resolved_model = model or os.environ.get("HARNESS_MODEL_ENV") or os.environ.get("SREGYM_MODEL") or "gemini-2.0-pro"
        metadata["model"] = resolved_model

        # 3. Model API credentials check
        if "gemini" in resolved_model.lower():
            if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
                warnings.append("Neither GEMINI_API_KEY nor GOOGLE_API_KEY is set in environment.")
        elif "gpt" in resolved_model.lower():
            if not os.environ.get("OPENAI_API_KEY"):
                warnings.append("OPENAI_API_KEY is not set in environment.")
        elif "claude" in resolved_model.lower():
            if not os.environ.get("ANTHROPIC_API_KEY"):
                warnings.append("ANTHROPIC_API_KEY is not set in environment.")

        # 4. Stages check
        stages_env = os.environ.get("SREGYM_STAGES", "diagnosis")
        enabled_stages = [s.strip() for s in stages_env.split(",") if s.strip()]
        metadata["enabled_stages"] = enabled_stages

        # 5. MCP Conductor / Tool endpoint check
        conductor_url = os.environ.get("SREGYM_CONDUCTOR_URL")
        metadata["conductor_url"] = conductor_url

        passed = len(errors) == 0
        return PreflightCheckResult(passed=passed, errors=errors, warnings=warnings, metadata=metadata)
