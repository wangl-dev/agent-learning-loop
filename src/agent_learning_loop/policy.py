"""Deterministic M1 actions used only as a system-correctness oracle."""

from __future__ import annotations

from agent_learning_loop.action_catalog import load_all_action_catalogs
from agent_learning_loop.schemas import Action, Observation, Task


class ScriptedPolicy:
    """Choose a fixed action sequence without reading private verifier state."""

    def __init__(self) -> None:
        self._actions = {
            catalog.task_id: tuple(entry.action for entry in catalog.actions)
            for catalog in load_all_action_catalogs()
        }

    def decide(self, task: Task, observation: Observation) -> Action | None:
        actions = self._actions.get(task.task_id, ())
        if observation.step_index >= len(actions):
            return None
        return actions[observation.step_index]
