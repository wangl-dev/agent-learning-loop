"""Deterministic M1 actions used only as a system-correctness oracle."""

from __future__ import annotations

from agent_learning_loop.schemas import Action, Observation, Task


class ScriptedPolicy:
    """Choose a fixed action sequence without reading private verifier state."""

    _actions: dict[str, tuple[Action, ...]] = {
        "workspace.fix-config": (
            Action(tool_name="read_text", arguments={"path": "app.conf"}),
            Action(
                tool_name="write_text",
                arguments={"path": "app.conf", "content": "mode=production\nport=8080\n"},
            ),
        ),
        "workspace.build-summary": (
            Action(tool_name="read_text", arguments={"path": "input/title.txt"}),
            Action(tool_name="read_text", arguments={"path": "input/items.txt"}),
            Action(
                tool_name="write_text",
                arguments={
                    "path": "output/summary.txt",
                    "content": "Release: M1\nItems:\n- schema\n- workspace\n",
                },
            ),
        ),
        "workspace.update-status": (
            Action(tool_name="list_files", arguments={"path": "."}),
            Action(tool_name="read_text", arguments={"path": "state/status.txt"}),
            Action(
                tool_name="write_text",
                arguments={"path": "state/status.txt", "content": "ready\n"},
            ),
        ),
    }

    def decide(self, task: Task, observation: Observation) -> Action | None:
        actions = self._actions.get(task.task_id, ())
        if observation.step_index >= len(actions):
            return None
        return actions[observation.step_index]
