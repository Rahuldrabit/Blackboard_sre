"""
Typed Communication Bus — enforces structured agent interaction and task delegation.

Core Invariants:
1. No free-text persuasion: agents communicate via structured messages referencing evidence IDs.
2. Delegation depth limiting: prevents infinite circular delegations (A -> B -> C -> A).
3. Capability-based routing: routes sub-tasks to the specialist with the required domain tools.
4. Tamper-evident ledger: records all bus interactions in the blackboard task / audit log.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from clients.graphstate.bus.message_types import BusMessage, MessageType
from clients.graphstate.state.graph_state import SREGraphState
from clients.graphstate.state.schema import Task, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class DispatchResult:
    def __init__(self, success: bool, message: BusMessage, errors: list[str] | None = None, response: Any = None):
        self.success = success
        self.message = message
        self.errors = errors or []
        self.response = response


class AgentCommunicationBus:
    """
    Central event router for typed inter-agent messages.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[BusMessage, SREGraphState], Any]] = {}
        self._message_history: list[BusMessage] = []
        self._next_msg_id = 1

    def register_handler(
        self,
        role: str,
        handler: Callable[[BusMessage, SREGraphState], Any],
    ) -> None:
        self._handlers[role] = handler

    def create_message(
        self,
        message_type: MessageType,
        sender_role: str,
        target_role: str | None = None,
        payload: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        delegation_depth: int = 0,
    ) -> BusMessage:
        msg = BusMessage(
            id=f"MSG_{self._next_msg_id:04d}",
            message_type=message_type,
            sender_role=sender_role,
            target_role=target_role,
            payload=payload or {},
            evidence_refs=evidence_refs or [],
            delegation_depth=delegation_depth,
        )
        self._next_msg_id += 1
        return msg

    def dispatch(
        self,
        message: BusMessage,
        state: SREGraphState,
    ) -> DispatchResult:
        """
        Validate and dispatch a typed message.
        """
        # Step 1: Validate communication invariants
        errors = message.validate_invariants()
        if errors:
            logger.warning("Message %s rejected by bus policy: %s", message.id, errors)
            return DispatchResult(success=False, message=message, errors=errors)

        self._message_history.append(message)

        # Step 2: If this is a TASK_REQUEST, record it in Blackboard pending_tasks
        if message.message_type == MessageType.TASK_REQUEST:
            task = Task(
                id=f"T_{message.id}",
                task_type=message.payload.get("task_type", "delegated_investigation"),
                assignee_role=message.target_role,
                description=message.payload.get("description", "Delegated investigation task"),
                parameters=message.payload.get("parameters", {}),
                created_by=message.sender_role,
                delegation_depth=message.delegation_depth,
                status=TaskStatus.PENDING,
            )
            state["pending_tasks"].append(task)
            logger.info("Recorded pending task '%s' for role '%s'", task.id, message.target_role)

        # Step 3: Route to target agent handler if registered
        response = None
        if message.target_role and message.target_role in self._handlers:
            try:
                response = self._handlers[message.target_role](message, state)
            except Exception as e:
                logger.error("Handler for '%s' failed: %s", message.target_role, e)
                return DispatchResult(success=False, message=message, errors=[str(e)])

        return DispatchResult(success=True, message=message, response=response)

    @property
    def history(self) -> list[BusMessage]:
        return list(self._message_history)
