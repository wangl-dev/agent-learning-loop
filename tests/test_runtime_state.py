from __future__ import annotations

import pytest

from agent_learning_loop.runtime_schemas import RuntimeState
from agent_learning_loop.runtime_state import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransitionError,
    RuntimeStateMachine,
    TerminalStateError,
)


def test_every_declared_runtime_transition_is_accepted() -> None:
    for source, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            machine = RuntimeStateMachine(state=source)
            transition = machine.transition(target)
            assert transition == (source, target)
            assert machine.state is target


def test_invalid_transition_keeps_the_previous_state() -> None:
    machine = RuntimeStateMachine()

    with pytest.raises(InvalidStateTransitionError) as caught:
        machine.transition(RuntimeState.EXECUTING_TOOL)

    assert caught.value.category == "internal_error"
    assert machine.state is RuntimeState.CREATED


@pytest.mark.parametrize(
    "terminal",
    [
        RuntimeState.SUCCEEDED,
        RuntimeState.FAILED,
        RuntimeState.REJECTED,
        RuntimeState.BUDGET_EXHAUSTED,
        RuntimeState.TIMED_OUT,
    ],
)
def test_terminal_state_rejects_further_execution(terminal: RuntimeState) -> None:
    machine = RuntimeStateMachine(state=terminal)

    with pytest.raises(TerminalStateError):
        machine.transition(RuntimeState.DECIDING)
