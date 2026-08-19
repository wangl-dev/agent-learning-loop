"""Load the three packaged M1 Workspace task fixtures."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from agent_learning_loop.schemas import WorkspaceTaskFixture


class TaskNotFoundError(LookupError):
    """A requested fixed M1 task does not exist."""


def load_task_file(path: Path) -> WorkspaceTaskFixture:
    """Validate one local JSON fixture with the public M1 schema."""
    return WorkspaceTaskFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_all_tasks() -> list[WorkspaceTaskFixture]:
    """Return all packaged Workspace fixtures in stable task-ID order."""
    fixture_root = files("agent_learning_loop").joinpath("task_fixtures", "workspace")
    fixtures = [
        WorkspaceTaskFixture.model_validate_json(resource.read_text(encoding="utf-8"))
        for resource in fixture_root.iterdir()
        if resource.name.endswith(".json")
    ]
    return sorted(fixtures, key=lambda fixture: fixture.task.task_id)


def load_task(task_id: str) -> WorkspaceTaskFixture:
    """Load one packaged fixture by its public task ID."""
    for fixture in load_all_tasks():
        if fixture.task.task_id == task_id:
            return fixture
    raise TaskNotFoundError(f"unknown M1 Workspace task: {task_id}")
