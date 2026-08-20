"""Central legal-transition table for the M2 Runtime state machine."""

from __future__ import annotations

from dataclasses import dataclass

from agent_learning_loop.runtime_schemas import TERMINAL_STATES, RuntimeState

ALLOWED_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    RuntimeState.CREATED: frozenset(
        {RuntimeState.RESETTING, RuntimeState.FAILED, RuntimeState.REJECTED}
    ),
    RuntimeState.RESETTING: frozenset({RuntimeState.READY, RuntimeState.FAILED}),
    RuntimeState.READY: frozenset(
        {RuntimeState.DECIDING, RuntimeState.BUDGET_EXHAUSTED, RuntimeState.TIMED_OUT}
    ),
    RuntimeState.DECIDING: frozenset(
        {
            RuntimeState.VALIDATING_ACTION,
            RuntimeState.VERIFYING,
            RuntimeState.FAILED,
            RuntimeState.BUDGET_EXHAUSTED,
            RuntimeState.TIMED_OUT,
        }
    ),
    RuntimeState.VALIDATING_ACTION: frozenset(
        {
            RuntimeState.EXECUTING_TOOL,
            RuntimeState.REJECTED,
            RuntimeState.FAILED,
        }
    ),
    RuntimeState.EXECUTING_TOOL: frozenset(
        {
            RuntimeState.OBSERVING,
            RuntimeState.FAILED,
            RuntimeState.REJECTED,
            RuntimeState.BUDGET_EXHAUSTED,
            RuntimeState.TIMED_OUT,
        }
    ),
    RuntimeState.OBSERVING: frozenset(
        {
            RuntimeState.DECIDING,
            RuntimeState.FAILED,
            RuntimeState.BUDGET_EXHAUSTED,
            RuntimeState.TIMED_OUT,
        }
    ),
    RuntimeState.VERIFYING: frozenset(
        {RuntimeState.SUCCEEDED, RuntimeState.FAILED, RuntimeState.TIMED_OUT}
    ),
    **{terminal: frozenset() for terminal in TERMINAL_STATES},
}


class InvalidStateTransitionError(RuntimeError):
    category = "internal_error"


class TerminalStateError(InvalidStateTransitionError):
    pass


@dataclass
class RuntimeStateMachine:
    state: RuntimeState = RuntimeState.CREATED

    def transition(self, target: RuntimeState) -> tuple[RuntimeState, RuntimeState]:
        source = self.state
        if source in TERMINAL_STATES:
            raise TerminalStateError("terminal Runtime state cannot transition")
        if target not in ALLOWED_TRANSITIONS[source]:
            raise InvalidStateTransitionError("illegal Runtime state transition")
        self.state = target
        return source, target
