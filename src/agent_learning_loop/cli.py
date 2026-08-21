"""Command-line entry points for version and the bounded M1-M3A slices."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from agent_learning_loop import __version__


def build_parser() -> argparse.ArgumentParser:
    """Create the project command-line parser."""
    parser = argparse.ArgumentParser(prog="agent-learning-loop")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    workspace = subparsers.add_parser(
        "run-workspace",
        help="run one or all three fault-free M1 Workspace fixtures",
    )
    selection = workspace.add_mutually_exclusive_group()
    selection.add_argument(
        "--task",
        help="fixed task ID, or 'all' (the default)",
    )
    selection.add_argument(
        "--task-file",
        type=Path,
        help="local JSON fixture used for schema and failure-path checks",
    )
    workspace.add_argument(
        "--output-dir",
        type=Path,
        help="new or empty directory for per-task artifacts",
    )
    runtime = subparsers.add_parser(
        "run-runtime",
        help="run one fixed M2 Workspace failure scenario",
    )
    runtime.add_argument("--task", required=True, help="fixed M1 Workspace task ID")
    runtime.add_argument(
        "--mode",
        required=True,
        choices=("naive", "retry_only", "safeguarded"),
    )
    runtime.add_argument(
        "--failure-schedule",
        required=True,
        help="packaged M2 failure schedule ID",
    )
    runtime.add_argument("--output-dir", required=True, type=Path)
    runtime.add_argument("--max-steps", type=int, default=8)
    runtime.add_argument("--max-tool-calls", type=int, default=12)
    runtime.add_argument("--timeout-seconds", type=float, default=30.0)
    runtime.add_argument("--retry-backoff-seconds", type=float, default=0.0)
    durable = subparsers.add_parser(
        "run-durable",
        help="run the fixed M3A durable-journal experiment",
    )
    durable.add_argument("--task", required=True)
    durable.add_argument("--mode", required=True, choices=("safeguarded",))
    durable.add_argument("--failure-schedule", required=True)
    durable.add_argument("--interruption-schedule")
    durable.add_argument("--checkpointing", required=True, choices=("on", "off"))
    durable.add_argument("--output-dir", required=True, type=Path)
    durable.add_argument("--max-steps", type=int, default=8)
    durable.add_argument("--max-tool-calls", type=int, default=12)
    durable.add_argument("--timeout-seconds", type=float, default=30.0)
    durable.add_argument("--retry-backoff-seconds", type=float, default=0.0)
    resume = subparsers.add_parser(
        "resume-runtime",
        help="resume one validated M3A checkpoint in its existing run directory",
    )
    resume.add_argument("--run-dir", required=True, type=Path)
    validate = subparsers.add_parser(
        "validate-trajectory",
        help="read-only validation of an M3A journal/checkpoint/result",
    )
    validate.add_argument("--run-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a process exit code."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    if arguments.command == "run-workspace":
        return _run_workspace(arguments)
    if arguments.command == "run-runtime":
        return _run_runtime(arguments)
    if arguments.command == "run-durable":
        return _run_durable(arguments)
    if arguments.command == "resume-runtime":
        return _resume_runtime(arguments)
    if arguments.command == "validate-trajectory":
        return _validate_trajectory(arguments)
    return 0


def _run_workspace(arguments: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from agent_learning_loop.tasks import (
        TaskNotFoundError,
        load_all_tasks,
        load_task,
        load_task_file,
    )
    from agent_learning_loop.vertical_slice import OutputExistsError, execute_task

    try:
        if arguments.task_file is not None:
            fixtures = [load_task_file(arguments.task_file)]
        elif arguments.task in (None, "all"):
            fixtures = load_all_tasks()
        else:
            fixtures = [load_task(arguments.task)]
        output_root = arguments.output_dir
        if output_root is None:
            output_root = Path(tempfile.mkdtemp(prefix="agent-learning-loop-m1-"))
        output_root.mkdir(parents=True, exist_ok=True)
        results = []
        for fixture in fixtures:
            run_directory = output_root / fixture.task.task_id.replace(".", "_")
            result = execute_task(fixture, run_directory, run_id=uuid4().hex)
            results.append(result)
            print(
                f"{result.task_id}: {result.outcome} "
                f"(result: {run_directory / 'result.json'})"
            )
        return 0 if all(result.outcome == "passed" for result in results) else 1
    except (OSError, OutputExistsError, TaskNotFoundError, ValidationError) as exc:
        print(f"run-workspace validation error: {exc}", file=sys.stderr)
        return 2


def _run_runtime(arguments: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from agent_learning_loop.failure_schedules import (
        FailureScheduleMismatchError,
        FailureScheduleNotFoundError,
        load_failure_schedule,
    )
    from agent_learning_loop.runtime import execute_runtime_task
    from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode, RuntimeState
    from agent_learning_loop.tasks import TaskNotFoundError, load_task
    from agent_learning_loop.vertical_slice import OutputExistsError

    try:
        fixture = load_task(arguments.task)
        schedule = load_failure_schedule(arguments.failure_schedule)
        mode = RuntimeMode(arguments.mode)
        config = RuntimeConfig.for_mode(
            mode,
            schedule_id=schedule.schedule_id,
            seed=schedule.seed,
            max_steps=arguments.max_steps,
            max_tool_calls=arguments.max_tool_calls,
            timeout_seconds=arguments.timeout_seconds,
            retry_backoff_seconds=[arguments.retry_backoff_seconds],
        )
        result = execute_runtime_task(
            fixture,
            arguments.output_dir,
            run_id=uuid4().hex,
            config=config,
            schedule=schedule,
        )
        print(
            f"{result.task_id}: {result.terminal_state.value} "
            f"(attempts={result.usage.tool_calls}, result: {arguments.output_dir / 'result.json'})"
        )
        return {
            RuntimeState.SUCCEEDED: 0,
            RuntimeState.FAILED: 1,
            RuntimeState.REJECTED: 3,
            RuntimeState.BUDGET_EXHAUSTED: 4,
            RuntimeState.TIMED_OUT: 5,
        }[result.terminal_state]
    except (
        FailureScheduleMismatchError,
        FailureScheduleNotFoundError,
        OSError,
        OutputExistsError,
        TaskNotFoundError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"run-runtime validation error: {exc}", file=sys.stderr)
        return 2


def _run_durable(arguments: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from agent_learning_loop.durable_runtime import (
        ControlledInterruption,
        DurableValidationError,
        execute_durable_task,
    )
    from agent_learning_loop.durable_schemas import CheckpointingMode
    from agent_learning_loop.failure_schedules import (
        FailureScheduleMismatchError,
        FailureScheduleNotFoundError,
        load_failure_schedule,
    )
    from agent_learning_loop.interruption_schedules import (
        InterruptionScheduleMismatchError,
        InterruptionScheduleNotFoundError,
        load_interruption_schedule,
    )
    from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode, RuntimeState
    from agent_learning_loop.tasks import TaskNotFoundError, load_task
    from agent_learning_loop.vertical_slice import OutputExistsError

    try:
        fixture = load_task(arguments.task)
        failure = load_failure_schedule(arguments.failure_schedule)
        interruption = (
            load_interruption_schedule(arguments.interruption_schedule)
            if arguments.interruption_schedule is not None
            else None
        )
        config = RuntimeConfig.for_mode(
            RuntimeMode(arguments.mode),
            schedule_id=failure.schedule_id,
            seed=failure.seed,
            max_steps=arguments.max_steps,
            max_tool_calls=arguments.max_tool_calls,
            timeout_seconds=arguments.timeout_seconds,
            retry_backoff_seconds=[arguments.retry_backoff_seconds],
        )
        result = execute_durable_task(
            fixture,
            arguments.output_dir,
            run_id=uuid4().hex,
            config=config,
            failure_schedule=failure,
            checkpointing=CheckpointingMode(arguments.checkpointing),
            interruption_schedule=interruption,
        )
        print(
            f"{result.task_id}: {result.terminal_state.value} "
            f"(result: {arguments.output_dir / 'result.json'})"
        )
        return 0 if result.terminal_state is RuntimeState.SUCCEEDED else 1
    except ControlledInterruption as exc:
        print(f"run-durable interrupted: {exc}", file=sys.stderr)
        return 6
    except (
        DurableValidationError,
        FailureScheduleMismatchError,
        FailureScheduleNotFoundError,
        InterruptionScheduleMismatchError,
        InterruptionScheduleNotFoundError,
        OSError,
        OutputExistsError,
        TaskNotFoundError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"run-durable validation error: {exc}", file=sys.stderr)
        return 2


def _resume_runtime(arguments: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from agent_learning_loop.durable_runtime import (
        DurableValidationError,
        resume_durable_task,
    )
    from agent_learning_loop.runtime_schemas import RuntimeState

    try:
        result = resume_durable_task(arguments.run_dir)
        print(
            f"{result.task_id}: {result.terminal_state.value} resumed "
            f"(result: {arguments.run_dir / 'result.json'})"
        )
        return 0 if result.terminal_state is RuntimeState.SUCCEEDED else 1
    except (DurableValidationError, OSError, ValidationError, ValueError) as exc:
        print(f"resume-runtime validation error: {exc}", file=sys.stderr)
        return 2


def _validate_trajectory(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.event_replay import (
        TrajectoryValidationError,
        validate_trajectory,
    )

    try:
        result = validate_trajectory(arguments.run_dir)
        print(result.model_dump_json())
        return 0
    except (OSError, TrajectoryValidationError, ValueError) as exc:
        print(f"validate-trajectory validation error: {exc}", file=sys.stderr)
        return 2
