"""Load custom fixtures or tasks from the validated Workspace corpus."""

from __future__ import annotations

from pathlib import Path

from agent_learning_loop.corpus import validate_workspace_corpus
from agent_learning_loop.schemas import WorkspaceTaskFixture


class TaskNotFoundError(LookupError):
    """A requested fixed M1 task does not exist."""


def load_task_file(path: Path) -> WorkspaceTaskFixture:
    """Validate one local JSON fixture with the public M1 schema."""
    return WorkspaceTaskFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_all_tasks() -> list[WorkspaceTaskFixture]:
    """Return validated packaged Workspace fixtures in stable task-ID order."""
    return list(validate_workspace_corpus().fixtures)


def load_task(task_id: str) -> WorkspaceTaskFixture:
    """Load one packaged fixture by its public task ID."""
    for fixture in load_all_tasks():
        if fixture.task.task_id == task_id:
            return fixture
    raise TaskNotFoundError(f"unknown Workspace corpus task: {task_id}")
