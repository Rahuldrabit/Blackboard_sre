"""
Typed message schemas for the Agent Communication Bus.

Enforces structured, evidence-grounded communication between agents:
- Agents NEVER exchange free-form conversational chatter.
- All messages carry explicit message types, evidence references, and delegation depths.
- Prevents inter-agent persuasion attacks and circular delegation loops.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TASK_REQUEST = "task_request"
    TASK_RESULT = "task_result"
    EVIDENCE_FOUND = "evidence_found"
    HYPOTHESIS_CREATED = "hypothesis_created"
    HYPOTHESIS_CHALLENGED = "hypothesis_challenged"
    VERIFICATION_REQUEST = "verification_request"


class BusMessage(BaseModel):
    """A strongly-typed, schema-validated message on the communication bus."""

    id: str = Field(description="Unique message ID, e.g. 'MSG_042'")
    message_type: MessageType
    sender_role: str = Field(description="Originating specialist role")
    target_role: str | None = Field(
        default=None,
        description="Target specialist role (None = broadcast to blackboard)",
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Mandatory evidence IDs (Observations / DerivedFacts) grounding this message",
    )
    delegation_depth: int = Field(
        default=0,
        ge=0,
        description="Depth in delegation hierarchy. Blocked if > MAX_DELEGATION_DEPTH.",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def validate_invariants(self) -> list[str]:
        """Validate communication invariants deterministically."""
        errors: list[str] = []

        # Invariant 1: Evidence grounding for factual claims or challenges
        if self.message_type in (MessageType.HYPOTHESIS_CHALLENGED, MessageType.TASK_RESULT):
            if not self.evidence_refs and not self.payload.get("evidence_refs"):
                errors.append(
                    f"Message of type '{self.message_type}' must cite at least one evidence ID."
                )

        # Invariant 2: Delegation depth limit
        if self.delegation_depth > 3:
            errors.append(
                f"Delegation depth {self.delegation_depth} exceeds MAX_DELEGATION_DEPTH (3)."
            )

        # Invariant 3: Persuasion check — block direct conversational rhetoric
        for key in ("opinion", "guess", "persuasion", "chat"):
            if key in self.payload:
                errors.append(f"Payload field '{key}' violates evidence-not-conclusions policy.")

        return errors
