"""Command-line entry points for the bounded M1-M7C-A slices."""

from __future__ import annotations

import argparse
import subprocess
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
        help="run one or all ten validated Workspace corpus tasks",
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
    incident = subparsers.add_parser(
        "run-incident",
        help="run one or all ten validated Incident simulation tasks",
    )
    incident.add_argument("--task", default="all", help="fixed Incident task ID or 'all'")
    incident.add_argument("--output-dir", required=True, type=Path)
    dataops = subparsers.add_parser(
        "run-dataops",
        help="run one or all ten validated DataOps SQLite tasks",
    )
    dataops.add_argument("--task", default="all", help="fixed DataOps task ID or 'all'")
    dataops.add_argument("--output-dir", required=True, type=Path)
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
    durable.add_argument("--record-actions", choices=("on", "off"), default="off")
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
    replay = subparsers.add_parser(
        "replay-actions",
        help="replay validated M3B action refs in a new Workspace",
    )
    replay.add_argument("--source-run-dir", required=True, type=Path)
    replay.add_argument("--output-dir", required=True, type=Path)
    corpus = subparsers.add_parser(
        "validate-corpus",
        help="validate packaged corpus identities without executing a task",
    )
    corpus.add_argument(
        "--environment",
        choices=("workspace", "incident", "dataops", "all"),
        default="workspace",
    )
    run_eval = subparsers.add_parser(
        "run-eval",
        help="run one pre-registered M5A Eval suite into a new bundle directory",
    )
    run_eval.add_argument(
        "--suite",
        required=True,
        choices=(
            "system-correctness",
            "runtime-reliability",
            "recovery-replay",
            "all",
        ),
    )
    run_eval.add_argument("--source-commit", required=True)
    run_eval.add_argument("--output-dir", required=True, type=Path)
    run_eval.add_argument(
        "--environment", choices=("workspace", "incident", "dataops")
    )
    run_eval.add_argument("--split", choices=("train", "validation", "test"))
    run_eval.add_argument("--tag")
    run_eval.add_argument("--pair")
    validate_eval = subparsers.add_parser(
        "validate-eval",
        help="validate an M5A bundle read-only without executing tasks",
    )
    validate_eval.add_argument("--run-dir", required=True, type=Path)
    run_case = subparsers.add_parser(
        "run-fde-case",
        help="run the fixed simulated Incident copilot acceptance case",
    )
    run_case.add_argument("--case", required=True)
    run_case.add_argument("--source-commit", required=True)
    run_case.add_argument("--output-dir", required=True, type=Path)
    validate_case = subparsers.add_parser(
        "validate-fde-case",
        help="validate a simulated FDE case bundle without executing tasks",
    )
    validate_case.add_argument("--run-dir", required=True, type=Path)
    export_sft = subparsers.add_parser(
        "export-sft-candidates",
        help="export the fixed train-only scripted-oracle SFT development candidate",
    )
    export_sft.add_argument("--eval-bundle", required=True, type=Path)
    export_sft.add_argument("--output-dir", required=True, type=Path)
    validate_sft = subparsers.add_parser(
        "validate-sft-candidates",
        help="validate an SFT candidate against its source Eval without execution",
    )
    validate_sft.add_argument("--bundle", required=True, type=Path)
    validate_sft.add_argument("--eval-bundle", required=True, type=Path)
    run_probe = subparsers.add_parser(
        "run-model-probe",
        help="predict validation next actions without executing model output",
    )
    run_probe.add_argument("--eval-bundle", required=True, type=Path)
    run_probe.add_argument("--output-dir", required=True, type=Path)
    run_probe.add_argument("--backend", required=True, choices=("fake", "qwen3"))
    run_probe.add_argument(
        "--model-id",
        choices=("Qwen/Qwen3-0.6B", "Qwen/Qwen3-1.7B"),
    )
    run_probe.add_argument("--snapshot-dir", type=Path)
    validate_probe = subparsers.add_parser(
        "validate-model-probe",
        help="reconstruct a model probe read-only without model or tool execution",
    )
    validate_probe.add_argument("--bundle", required=True, type=Path)
    validate_probe.add_argument("--eval-bundle", required=True, type=Path)
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
    if arguments.command == "run-incident":
        return _run_incident(arguments)
    if arguments.command == "run-dataops":
        return _run_dataops(arguments)
    if arguments.command == "run-runtime":
        return _run_runtime(arguments)
    if arguments.command == "run-durable":
        return _run_durable(arguments)
    if arguments.command == "resume-runtime":
        return _resume_runtime(arguments)
    if arguments.command == "validate-trajectory":
        return _validate_trajectory(arguments)
    if arguments.command == "replay-actions":
        return _replay_actions(arguments)
    if arguments.command == "validate-corpus":
        return _validate_corpus(arguments.environment)
    if arguments.command == "run-eval":
        return _run_eval(arguments)
    if arguments.command == "validate-eval":
        return _validate_eval(arguments)
    if arguments.command == "run-fde-case":
        return _run_fde_case(arguments)
    if arguments.command == "validate-fde-case":
        return _validate_fde_case(arguments)
    if arguments.command == "export-sft-candidates":
        return _export_sft_candidates(arguments)
    if arguments.command == "validate-sft-candidates":
        return _validate_sft_candidates(arguments)
    if arguments.command == "run-model-probe":
        return _run_model_probe(arguments)
    if arguments.command == "validate-model-probe":
        return _validate_model_probe(arguments)
    return 0


def _run_workspace(arguments: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from agent_learning_loop.corpus import (
        CorpusValidationError,
        validate_workspace_corpus,
    )
    from agent_learning_loop.tasks import (
        TaskNotFoundError,
        load_all_tasks,
        load_task,
        load_task_file,
    )
    from agent_learning_loop.vertical_slice import OutputExistsError, execute_task

    try:
        validate_workspace_corpus()
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
            print(f"{result.task_id}: {result.outcome} (result: {run_directory / 'result.json'})")
        return 0 if all(result.outcome == "passed" for result in results) else 1
    except (
        CorpusValidationError,
        OSError,
        OutputExistsError,
        TaskNotFoundError,
        ValidationError,
    ) as exc:
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
            record_actions=arguments.record_actions == "on",
        )
        if arguments.record_actions == "on":
            print(
                f"{result.task_id}: {result.terminal_state.value} "
                "(result: result.json, actions: actions.jsonl)"
            )
        else:
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


def _replay_actions(arguments: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from agent_learning_loop.action_replay import (
        ActionReplayValidationError,
        replay_actions,
    )

    try:
        result = replay_actions(arguments.source_run_dir, arguments.output_dir)
        print("replay result: replay-result.json")
        print(f"{result.vertical_slice_matches}/{result.vertical_slice_total} vertical-slice smoke")
        return 0 if result.action_replay_match_rate == 1.0 else 1
    except (ActionReplayValidationError, OSError, ValidationError, ValueError) as exc:
        print(f"replay-actions validation error: {exc}", file=sys.stderr)
        return 2


def _run_incident(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.incident_corpus import validate_incident_corpus
    from agent_learning_loop.incident_runner import run_all_incident_tasks, run_incident_task
    from agent_learning_loop.vertical_slice import OutputExistsError

    try:
        corpus = validate_incident_corpus()
        if arguments.task == "all":
            results = run_all_incident_tasks(arguments.output_dir)
        else:
            results = [
                run_incident_task(
                    corpus, arguments.task, arguments.output_dir, run_id="incident-cli"
                )
            ]
        for result in results:
            print(f"{result.task_id}: {result.outcome} (result: result.json)")
        return 0 if all(result.outcome == "passed" for result in results) else 1
    except (OSError, OutputExistsError, ValueError) as exc:
        print(f"run-incident validation error: {exc}", file=sys.stderr)
        return 2


def _run_dataops(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.dataops_corpus import validate_dataops_corpus
    from agent_learning_loop.dataops_runner import run_all_dataops_tasks, run_dataops_task
    from agent_learning_loop.vertical_slice import OutputExistsError

    try:
        corpus = validate_dataops_corpus()
        if arguments.task == "all":
            results = run_all_dataops_tasks(arguments.output_dir)
        else:
            results = [
                run_dataops_task(corpus, arguments.task, arguments.output_dir, run_id="dataops-cli")
            ]
        for result in results:
            print(
                f"{result.task_id}: {result.outcome} "
                f"({result.terminal_state}, attempted={result.attempted_row_count}, "
                f"committed={result.committed_row_count}, result: result.json)"
            )
        return 0 if all(result.outcome == "passed" for result in results) else 1
    except (OSError, OutputExistsError, ValueError) as exc:
        print(f"run-dataops validation error: {exc}", file=sys.stderr)
        return 2


def _validate_corpus(environment: str) -> int:
    from agent_learning_loop.corpus import (
        CorpusValidationError,
        validate_workspace_corpus,
    )

    try:
        if environment == "workspace":
            print(validate_workspace_corpus().summary.model_dump_json())
            return 0
        from agent_learning_loop.dataops_corpus import (
            validate_all_corpora,
            validate_dataops_corpus,
        )

        if environment == "dataops":
            print(validate_dataops_corpus().summary.model_dump_json())
            return 0
        if environment == "all":
            print(validate_all_corpora().model_dump_json())
            return 0
        from agent_learning_loop.incident_corpus import validate_incident_corpus

        print(validate_incident_corpus().summary.model_dump_json())
        return 0
    except (CorpusValidationError, OSError, ValueError) as exc:
        print(f"validate-corpus validation error: {exc}", file=sys.stderr)
        return 2


def _run_eval(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.eval_runner import EvalRunError, run_eval

    try:
        outcome = run_eval(
            arguments.suite,
            arguments.source_commit,
            arguments.output_dir,
            environment=arguments.environment,
            split=arguments.split,
            tag=arguments.tag,
            pair=arguments.pair,
        )
        print(
            f"Eval bundle: {arguments.output_dir} "
            f"(selected denominator={outcome.manifest.selection.selected_total}/"
            f"{outcome.manifest.selection.candidate_total}, exit={outcome.exit_code})"
        )
        return outcome.exit_code
    except (
        EvalRunError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"run-eval validation error: {exc}", file=sys.stderr)
        return 2


def _validate_eval(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.eval_validator import (
        EvalBundleValidationError,
        validate_eval_bundle,
    )

    try:
        result = validate_eval_bundle(arguments.run_dir)
        print(result.model_dump_json())
        return 0
    except (EvalBundleValidationError, OSError, ValueError) as exc:
        print(f"validate-eval validation error: {exc}", file=sys.stderr)
        return 2


def _run_fde_case(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.fde_case_runner import FdeCaseRunError, run_fde_case

    try:
        outcome = run_fde_case(
            arguments.case,
            arguments.source_commit,
            arguments.output_dir,
        )
        print(
            f"FDE case bundle: {arguments.output_dir} "
            f"(case={outcome.manifest.case_id}, acceptance={outcome.acceptance.overall}, "
            f"exit={outcome.exit_code})"
        )
        return outcome.exit_code
    except (FdeCaseRunError, OSError, RuntimeError, ValueError) as exc:
        print(f"run-fde-case validation error: {exc}", file=sys.stderr)
        return 2


def _validate_fde_case(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.fde_case_validator import (
        FdeCaseValidationError,
        validate_fde_case,
    )

    try:
        result = validate_fde_case(arguments.run_dir)
        print(result.model_dump_json())
        return 0 if result.overall == "accepted" else 1
    except (FdeCaseValidationError, OSError, ValueError) as exc:
        print(f"validate-fde-case validation error: {exc}", file=sys.stderr)
        return 2


def _export_sft_candidates(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.sft_exporter import (
        SftExportError,
        SftExportInfrastructureError,
        export_sft_candidates,
    )

    try:
        outcome = export_sft_candidates(arguments.eval_bundle, arguments.output_dir)
        print(
            f"SFT candidate: {arguments.output_dir} "
            f"(eligible={outcome.manifest.sample_count}, "
            "workspace/incident/dataops=6/6/6, held-out-excluded=12, files=4)"
        )
        return 0
    except SftExportError as exc:
        print(f"export-sft-candidates validation error: {exc}", file=sys.stderr)
        return 1
    except (SftExportInfrastructureError, OSError, RuntimeError, ValueError) as exc:
        print(f"export-sft-candidates infrastructure error: {exc}", file=sys.stderr)
        return 2


def _validate_sft_candidates(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.sft_validator import (
        SftCandidateInfrastructureError,
        SftCandidateValidationError,
        validate_sft_candidates,
    )

    try:
        result = validate_sft_candidates(arguments.bundle, arguments.eval_bundle)
        print(result.model_dump_json())
        return 0
    except SftCandidateInfrastructureError as exc:
        print(f"validate-sft-candidates infrastructure error: {exc}", file=sys.stderr)
        return 2
    except SftCandidateValidationError as exc:
        print(f"validate-sft-candidates validation error: {exc}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"validate-sft-candidates infrastructure error: {exc}", file=sys.stderr)
        return 2


def _run_model_probe(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.model_probe_backend import ModelProbeBackendError
    from agent_learning_loop.model_probe_runner import ModelProbeRunError, run_model_probe

    try:
        outcome = run_model_probe(
            arguments.eval_bundle,
            arguments.output_dir,
            backend_kind=arguments.backend,
            seed=17,
            model_id=arguments.model_id,
            snapshot_dir=arguments.snapshot_dir,
        )
        print(
            f"Model probe: {arguments.output_dir} "
            f"(status={outcome.status}, tasks={outcome.summary.task_total}, "
            f"prefixes={outcome.summary.prefix_total}, executions=0)"
        )
        return 0
    except (ModelProbeBackendError, ModelProbeRunError, OSError, ValueError) as exc:
        print(f"run-model-probe validation error: {exc}", file=sys.stderr)
        return 2


def _validate_model_probe(arguments: argparse.Namespace) -> int:
    from agent_learning_loop.model_probe_validator import (
        ModelProbeValidationError,
        validate_model_probe,
    )

    try:
        result = validate_model_probe(arguments.bundle, arguments.eval_bundle)
        print(result.model_dump_json())
        return 0
    except (ModelProbeValidationError, OSError, ValueError) as exc:
        print(f"validate-model-probe validation error: {exc}", file=sys.stderr)
        return 2
