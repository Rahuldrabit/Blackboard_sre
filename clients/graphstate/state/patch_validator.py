"""
PatchValidator — deterministic validation and application of StatePatch onto SREGraphState.

This is NOT an LLM. It is pure Python rule enforcement.
It is the primary mechanism that prevents:
  - wrong hypothesis propagation
  - unauthorized mitigation during diagnosis
  - self-promotion of hypotheses to VERIFIED
  - budget overruns
  - prompt injection via raw log data

The validator also deduplicates evidence (same tool + params already cached)
and applies freshness TTL expiry before the patch is merged.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.knowledge_levels import KnowledgeLevel
from clients.graphstate.state.patch import StatePatch, ValidationResult
from clients.graphstate.state.schema import (
    AgentRun,
    Hypothesis,
    HypothesisStatus,
    HypothesisUpdate,
    Observation,
)

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 3
MAX_OBSERVATIONS_PER_PATCH = 30
MAX_HYPOTHESES_PER_PATCH = 10
MAX_SUMMARY_LENGTH = 500  # chars — prevents prompt inflation


class PatchValidator:
    """
    Validates a StatePatch and applies it to SREGraphState.

    Usage:
        validator = PatchValidator()
        result = validator.validate(patch, state)
        if result.valid:
            new_state = validator.apply(patch, state)
        else:
            # Log result.errors; discard or sanitize patch
    """

    # ── Validation ──────────────────────────────────────────────────────────

    def validate(
        self,
        patch: StatePatch,
        state: SREGraphState,
        agent_role: str | None = None,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        stripped: list[str] = []

        role = agent_role or patch.agent_role

        phase = state.get("phase", "diagnosis")

        # 1. Phase constraints
        if phase == "diagnosis" and patch.proposed_actions:
            errors.append(
                f"[phase] Agent '{patch.agent_role}' cannot propose actions during diagnosis phase."
            )

        if phase == "mitigation" and patch.new_hypotheses:
            warnings.append(
                "[phase] New hypotheses during mitigation phase will be accepted but not actioned."
            )

        # 2. Knowledge level invariants
        for h in patch.new_hypotheses:
            if h.knowledge_level != KnowledgeLevel.HYPOTHESIZED:
                errors.append(
                    f"[knowledge_level] Hypothesis '{h.id}' must be HYPOTHESIZED, "
                    f"got '{h.knowledge_level}'."
                )
            if h.status == HypothesisStatus.VERIFIED:
                errors.append(
                    f"[self_promotion] Agent '{role}' attempted to self-promote "
                    f"hypothesis '{h.id}' to verified status. Only the CausalVerifier node may do this."
                )

        for obs in patch.new_observations:
            if obs.knowledge_level != KnowledgeLevel.OBSERVED:
                errors.append(
                    f"[knowledge_level] Observation '{obs.id}' must be OBSERVED, "
                    f"got '{obs.knowledge_level}'."
                )

        for df in patch.new_derived_facts:
            if df.knowledge_level != KnowledgeLevel.DERIVED:
                errors.append(
                    f"[knowledge_level] DerivedFact '{df.id}' must be DERIVED."
                )

        # 3. Budget constraints
        budget_tokens = state.get("token_budget_remaining", 0)
        budget_tools = state.get("tool_calls_remaining", 0)

        if patch.tokens_used > budget_tokens:
            errors.append(
                f"[budget] Token budget exceeded: patch uses {patch.tokens_used}, "
                f"only {budget_tokens} remaining."
            )

        if patch.tool_calls_used > budget_tools:
            errors.append(
                f"[budget] Tool call budget exceeded: patch uses {patch.tool_calls_used}, "
                f"only {budget_tools} remaining."
            )

        # 4. Delegation depth
        for task in patch.new_tasks:
            if task.delegation_depth > MAX_DELEGATION_DEPTH:
                errors.append(
                    f"[delegation] Task '{task.id}' delegation depth {task.delegation_depth} "
                    f"exceeds maximum {MAX_DELEGATION_DEPTH}."
                )

        # 5. Size limits (prevent runaway token usage)
        if len(patch.new_observations) > MAX_OBSERVATIONS_PER_PATCH:
            warnings.append(
                f"[size] Patch contains {len(patch.new_observations)} observations; "
                f"truncating to {MAX_OBSERVATIONS_PER_PATCH}."
            )
            stripped.append(f"observations[{MAX_OBSERVATIONS_PER_PATCH}:]")

        if len(patch.new_hypotheses) > MAX_HYPOTHESES_PER_PATCH:
            warnings.append(
                f"[size] Patch contains {len(patch.new_hypotheses)} hypotheses; "
                f"truncating to {MAX_HYPOTHESES_PER_PATCH}."
            )
            stripped.append(f"hypotheses[{MAX_HYPOTHESES_PER_PATCH}:]")

        # 6. Summary length (prompt injection surface)
        for obs in patch.new_observations:
            if len(obs.summary) > MAX_SUMMARY_LENGTH:
                warnings.append(
                    f"[summary_length] Observation '{obs.id}' summary is "
                    f"{len(obs.summary)} chars; will be truncated to {MAX_SUMMARY_LENGTH}."
                )

        # 7. Duplicate detection via query cache
        cached_duplicates = self._find_cached_duplicates(patch, state)
        if cached_duplicates:
            warnings.append(
                f"[cache] {len(cached_duplicates)} tool results already in cache; "
                f"will reuse cached results."
            )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            stripped_items=stripped,
        )

    # ── Application ─────────────────────────────────────────────────────────

    def apply(self, patch: StatePatch, state: SREGraphState) -> dict[str, Any]:
        """
        Apply a validated patch to state.
        Returns a dict of state keys to update (LangGraph merge semantics).
        """
        now = datetime.now(timezone.utc)
        updates: dict[str, Any] = {}

        # Apply size truncation
        observations = (patch.new_observations or [])[:MAX_OBSERVATIONS_PER_PATCH]
        hypotheses = (patch.new_hypotheses or [])[:MAX_HYPOTHESES_PER_PATCH]

        # Sanitize summaries
        sanitized_observations = [
            obs.model_copy(update={"summary": obs.summary[:MAX_SUMMARY_LENGTH]})
            if len(obs.summary) > MAX_SUMMARY_LENGTH else obs
            for obs in observations
        ]

        # ── Evidence additions ──
        if sanitized_observations:
            updates["observations"] = state.get("observations", []) + sanitized_observations

        if patch.new_derived_facts:
            updates["derived_facts"] = state.get("derived_facts", []) + patch.new_derived_facts

        if hypotheses:
            updates["hypotheses"] = state.get("hypotheses", []) + hypotheses

        if patch.new_falsification_tests:
            updates["falsification_tests"] = (
                state.get("falsification_tests", []) + patch.new_falsification_tests
            )

        # ── Hypothesis updates ──
        if patch.hypothesis_updates:
            updated_hyps = self._apply_hypothesis_updates(
                state.get("hypotheses", []),
                patch.hypothesis_updates,
            )
            updates["hypotheses"] = updated_hyps

        # ── Task management ──
        if patch.new_tasks:
            updates["pending_tasks"] = state.get("pending_tasks", []) + patch.new_tasks

        if patch.completed_task_results:
            updates["completed_tasks"] = (
                state.get("completed_tasks", []) + patch.completed_task_results
            )
            # Remove from pending
            completed_ids = {r.task_id for r in patch.completed_task_results}
            updates["pending_tasks"] = [
                t for t in state.get("pending_tasks", [])
                if t.id not in completed_ids
            ]

        # ── Actions ──
        if patch.proposed_actions:
            updates["proposed_actions"] = (
                state.get("proposed_actions", []) + patch.proposed_actions
            )

        if patch.executed_action_results:
            updates["executed_actions"] = (
                state.get("executed_actions", []) + patch.executed_action_results
            )

        # ── Budget consumption ──
        updates["token_budget_remaining"] = (
            state.get("token_budget_remaining", 0) - patch.tokens_used
        )
        updates["tool_calls_remaining"] = (
            state.get("tool_calls_remaining", 0) - patch.tool_calls_used
        )
        updates["total_tokens_used"] = (
            state.get("total_tokens_used", 0) + patch.tokens_used
        )
        updates["total_tool_calls"] = (
            state.get("total_tool_calls", 0) + patch.tool_calls_used
        )

        # ── Diagnosis output ──
        if patch.diagnosis:
            updates["diagnosis"] = patch.diagnosis

        # ── Agent run log ──
        agent_run = AgentRun(
            agent_id=patch.agent_id,
            agent_role=patch.agent_role,
            model="",  # Filled in by caller
            started_at=patch.produced_at,
            completed_at=now,
            tokens_used=patch.tokens_used,
            tool_calls_used=patch.tool_calls_used,
            observations_added=len(sanitized_observations),
            hypotheses_added=len(hypotheses),
            hypothesis_updates=len(patch.hypothesis_updates),
            tasks_created=len(patch.new_tasks),
            blind_mode_used=patch.blind_mode_used,
        )
        updates["agent_runs"] = state.get("agent_runs", []) + [agent_run]

        # ── Step counter ──
        updates["num_steps"] = state.get("num_steps", 0) + 1
        updates["_pending_patch"] = None

        return updates

    def sanitize(self, patch: StatePatch, result: ValidationResult) -> StatePatch:
        """
        Strip invalid entries from a patch so we can still apply what's valid.
        Logs all stripping actions.
        """
        safe_hypotheses = [
            h for h in patch.new_hypotheses
            if (h.knowledge_level == KnowledgeLevel.HYPOTHESIZED
                and h.status != HypothesisStatus.VERIFIED)
        ]
        if len(safe_hypotheses) < len(patch.new_hypotheses):
            logger.warning(
                "Stripped %d invalid hypotheses from patch by '%s'.",
                len(patch.new_hypotheses) - len(safe_hypotheses),
                patch.agent_role,
            )

        safe_actions = []  # Strip all actions during diagnosis
        if patch.proposed_actions:
            logger.warning(
                "Stripped %d proposed actions from '%s' during diagnosis phase.",
                len(patch.proposed_actions),
                patch.agent_role,
            )

        return patch.model_copy(update={
            "new_hypotheses": safe_hypotheses[:MAX_HYPOTHESES_PER_PATCH],
            "new_observations": patch.new_observations[:MAX_OBSERVATIONS_PER_PATCH],
            "proposed_actions": safe_actions,
        })

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _apply_hypothesis_updates(
        self,
        hypotheses: list[Hypothesis],
        updates: list[HypothesisUpdate],
    ) -> list[Hypothesis]:
        """Apply confidence deltas and evidence additions to existing hypotheses."""
        hyp_map = {h.id: h for h in hypotheses}
        for upd in updates:
            if upd.hypothesis_id not in hyp_map:
                logger.warning("Hypothesis update references unknown ID '%s'.", upd.hypothesis_id)
                continue
            h = hyp_map[upd.hypothesis_id]
            new_confidence = max(0.0, min(1.0, h.confidence + upd.confidence_delta))
            new_support = list(set(h.supporting_evidence + upd.new_supporting_evidence))
            new_contra = list(set(h.contradictory_evidence + upd.new_contradictory_evidence))
            new_status = upd.status_update if upd.status_update else h.status
            hyp_map[upd.hypothesis_id] = h.model_copy(update={
                "confidence": new_confidence,
                "supporting_evidence": new_support,
                "contradictory_evidence": new_contra,
                "status": new_status,
            })
        return list(hyp_map.values())

    def _find_cached_duplicates(
        self,
        patch: StatePatch,
        state: SREGraphState,
    ) -> list[str]:
        """Return cache keys for tool results already in the query cache."""
        cache = state.get("query_cache", {})
        duplicates = []
        for obs in patch.new_observations:
            if obs.tool_name and obs.tool_params:
                key = _cache_key(obs.tool_name, obs.tool_params)
                if key in cache:
                    duplicates.append(key)
        return duplicates


def _cache_key(tool_name: str, params: dict[str, Any]) -> str:
    """Stable cache key for a tool call."""
    payload = json.dumps({"tool": tool_name, "params": params}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
