"""Procedural, fault-free M1 execution from task fixture to result files."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue

from agent_learning_loop.policy import ScriptedPolicy
from agent_learning_loop.protocols import ToolProtocol
from agent_learning_loop.schemas import (
    Event,
    EventKind,
    RunResult,
    TerminalOutcome,
    ToolResult,
    VerifierCheck,
    VerifierResult,
    WorkspaceTaskFixture,
)
from agent_learning_loop.verifier import WorkspaceStateVerifier
from agent_learning_loop.workspace import WorkspaceEnvironment, WorkspaceError
from agent_learning_loop.workspace_tools import ListFilesTool, ReadTextTool, WriteTextTool


class OutputExistsError(RuntimeError):
    """The caller selected a run directory that already contains artifacts."""


def execute_task(
    fixture: WorkspaceTaskFixture,
    run_directory: Path,
    *,
    run_id: str,
) -> RunResult:
    """Execute one finite scripted task and write JSONL plus terminal JSON."""
    policy = ScriptedPolicy()
    if run_directory.exists() and any(run_directory.iterdir()):
        raise OutputExistsError(f"run directory is not empty: {run_directory}")
    run_directory.mkdir(parents=True, exist_ok=True)
    environment = WorkspaceEnvironment(run_directory / "workspace")
    verifier = WorkspaceStateVerifier()
    tools: dict[str, ToolProtocol] = {
        "list_files": ListFilesTool(),
        "read_text": ReadTextTool(),
        "write_text": WriteTextTool(),
    }
    events: list[Event] = []
    task = fixture.task

    def emit(
        step_index: int,
        event_kind: EventKind,
        payload: dict[str, JsonValue],
    ) -> None:
        events.append(
            Event(
                run_id=run_id,
                task_id=task.task_id,
                step_index=step_index,
                event_kind=event_kind,
                payload=payload,
            )
        )

    emit(
        0,
        "task_started",
        {
            "instruction": task.instruction,
            "fixture_id": task.fixture_id,
            "environment_kind": task.environment_kind,
            "allowed_tools": cast(JsonValue, list(task.allowed_tools)),
        },
    )
    step_index = 0
    execution_error: str | None = None
    verifier_result: VerifierResult
    outcome: TerminalOutcome = "error"
    try:
        observation = environment.reset(fixture.private.setup, task_id=task.task_id)
        initial = environment.snapshot()
        while True:
            action = policy.decide(task, observation)
            if action is None:
                break
            emit(step_index, "action_selected", action.model_dump(mode="json"))
            if action.tool_name not in task.allowed_tools:
                tool_result = ToolResult(
                    status="error",
                    payload={"message": "scripted action used a tool outside the task allowlist"},
                )
            else:
                tool_result = tools[action.tool_name].execute(environment, action)
            emit(step_index, "tool_completed", tool_result.model_dump(mode="json"))
            if tool_result.status == "error":
                execution_error = "a validated Workspace tool rejected the action"
                break
            step_index += 1
            observation = environment.observe(
                task_id=task.task_id,
                step_index=step_index,
                last_tool_result=tool_result,
            )
        if execution_error is None:
            final = environment.snapshot()
            verifier_result = verifier.verify(initial, final, fixture.private.expected)
            outcome = "passed" if verifier_result.passed else "failed"
        else:
            verifier_result = _execution_failure_verifier()
    except (WorkspaceError, OSError, UnicodeError):
        execution_error = "Workspace setup or file access was rejected"
        verifier_result = _execution_failure_verifier()
    finally:
        environment.close()

    if execution_error is not None:
        emit(step_index, "execution_failed", {"message": execution_error})
    emit(
        step_index,
        "run_finished",
        {"outcome": outcome, "verifier_passed": verifier_result.passed},
    )
    run_result = RunResult(
        run_id=run_id,
        task_id=task.task_id,
        outcome=outcome,
        verifier=verifier_result,
    )
    _write_outputs(run_directory, events, run_result)
    return run_result


def _execution_failure_verifier() -> VerifierResult:
    return VerifierResult(
        passed=False,
        score=0.0,
        checks=[
            VerifierCheck(
                name="execution_completed",
                passed=False,
                detail="execution ended before state verification",
            )
        ],
    )


def _write_outputs(run_directory: Path, events: list[Event], result: RunResult) -> None:
    event_text = "".join(f"{event.model_dump_json()}\n" for event in events)
    (run_directory / result.events_file).write_text(event_text, encoding="utf-8")
    (run_directory / "result.json").write_text(
        result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
