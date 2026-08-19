"""Command-line entry points for version and the M1 Workspace slice."""

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
