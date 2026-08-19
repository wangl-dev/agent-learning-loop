from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from agent_learning_loop.schemas import Event, WorkspaceTaskFixture


def valid_fixture_data() -> dict[str, object]:
    return {
        "task": {
            "schema_version": "1",
            "task_id": "workspace.example",
            "environment_kind": "workspace",
            "instruction": "Update the requested file.",
            "allowed_tools": ["read_text", "write_text"],
            "fixture_id": "workspace.example.v1",
            "provenance": "Project-authored synthetic M1 fixture.",
        },
        "private": {
            "setup": {"files": {"input.txt": "before\n"}},
            "expected": {
                "required_files": {"input.txt": "after\n"},
                "unchanged_files": [],
                "allowed_mutations": ["input.txt"],
                "forbidden_paths": ["secret.txt"],
            },
        },
    }


def test_workspace_task_fixture_round_trips_through_json() -> None:
    fixture = WorkspaceTaskFixture.model_validate(valid_fixture_data())

    restored = WorkspaceTaskFixture.model_validate_json(fixture.model_dump_json())

    assert restored == fixture


def remove_instruction(data: dict[str, object]) -> None:
    task = data["task"]
    assert isinstance(task, dict)
    task.pop("instruction")


def replace_allowed_tools_with_string(data: dict[str, object]) -> None:
    task = data["task"]
    assert isinstance(task, dict)
    task["allowed_tools"] = "read_text"


def add_unknown_task_field(data: dict[str, object]) -> None:
    task = data["task"]
    assert isinstance(task, dict)
    task["unexpected"] = True


@pytest.mark.parametrize(
    ("mutation", "location"),
    [
        (remove_instruction, "instruction"),
        (replace_allowed_tools_with_string, "allowed_tools"),
        (add_unknown_task_field, "unexpected"),
    ],
)
def test_workspace_task_fixture_rejects_missing_wrong_and_unknown_fields(
    mutation: Callable[[dict[str, object]], None],
    location: str,
) -> None:
    data = valid_fixture_data()
    mutation(data)

    with pytest.raises(ValidationError) as exc_info:
        WorkspaceTaskFixture.model_validate(data)

    assert location in str(exc_info.value)


def test_event_requires_run_task_and_step_correlation() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(
            {
                "schema_version": "1",
                "task_id": "workspace.example",
                "step_index": 0,
                "event_kind": "task_started",
                "payload": {},
            }
        )
