from __future__ import annotations

import pytest

from agent_learning_loop.schemas import WorkspaceExpectedState, WorkspaceSnapshot
from agent_learning_loop.verifier import WorkspaceStateVerifier


def expected_state() -> WorkspaceExpectedState:
    return WorkspaceExpectedState(
        required_files={"target.txt": "after\n"},
        unchanged_files=["keep.txt"],
        allowed_mutations=["target.txt"],
        forbidden_paths=["forbidden.txt"],
    )


def initial_state() -> WorkspaceSnapshot:
    return WorkspaceSnapshot(files={"target.txt": "before\n", "keep.txt": "same\n"})


def test_verifier_passes_matching_state_without_side_effects() -> None:
    final = WorkspaceSnapshot(files={"target.txt": "after\n", "keep.txt": "same\n"})

    result = WorkspaceStateVerifier().verify(initial_state(), final, expected_state())

    assert result.passed is True
    assert result.score == 1.0
    assert all(check.passed for check in result.checks)


@pytest.mark.parametrize(
    "final",
    [
        WorkspaceSnapshot(files={"target.txt": "wrong\n", "keep.txt": "same\n"}),
        WorkspaceSnapshot(
            files={
                "target.txt": "after\n",
                "keep.txt": "same\n",
                "forbidden.txt": "side effect\n",
            }
        ),
        WorkspaceSnapshot(
            files={"target.txt": "after\n", "keep.txt": "changed\n"}
        ),
    ],
)
def test_verifier_fails_wrong_target_or_forbidden_side_effect(
    final: WorkspaceSnapshot,
) -> None:
    result = WorkspaceStateVerifier().verify(initial_state(), final, expected_state())

    assert result.passed is False
    assert result.score == 0.0
    assert any(not check.passed for check in result.checks)
