"""Execute and read-only-check the four fixed M5A recovery/replay diagnostics."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from agent_learning_loop.action_replay import ActionReplayResult, replay_actions
from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.durable_runtime import ControlledInterruption, execute_durable_task
from agent_learning_loop.durable_schemas import CheckpointingMode, DurableEvent, DurableResult
from agent_learning_loop.eval_bundle import canonical_json_text
from agent_learning_loop.eval_clock import DeterministicEvalClock
from agent_learning_loop.eval_schemas import RecoveryDiagnosticArtifact, RecoveryEvalCell
from agent_learning_loop.event_replay import validate_trajectory, workspace_digest
from agent_learning_loop.failure_schedules import load_failure_schedule
from agent_learning_loop.interruption_schedules import load_interruption_schedule
from agent_learning_loop.journal import read_and_validate_journal
from agent_learning_loop.runtime_schemas import RuntimeState
from agent_learning_loop.tasks import load_task


class RecoveryDiagnosticError(ValueError):
    """A fixed recovery/replay diagnostic failed or its evidence drifted."""


def _manifest(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            len(path.read_bytes()),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _write_diagnostic(path: Path, artifact: RecoveryDiagnosticArtifact) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json_text(artifact))
        stream.flush()
        os.fsync(stream.fileno())


def _fixed_resume(run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_learning_loop.eval_resume",
            "--run-dir",
            str(run_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=30,
    )


def _physical_usage(records: list[DurableEvent]) -> tuple[int, int]:
    attempts = [record for record in records if record.event_kind == "attempt_started"]
    hits = [record for record in records if record.event_kind == "idempotency_hit"]
    physical_executions = len(attempts) - len(hits)
    physical_writes = sum(
        record.payload.tool_name == "write_text" for record in attempts
    ) - sum(record.payload.tool_name == "write_text" for record in hits)
    if physical_executions < 0 or physical_writes < 0:
        raise RecoveryDiagnosticError("invalid_journal_physical_usage")
    return physical_executions, physical_writes


def _durable_result_matches_cell(
    result: DurableResult,
    cell: RecoveryEvalCell,
    *,
    run_id: str,
    resumed: bool,
) -> bool:
    return all(
        (
            result.run_id == run_id,
            result.task_id == cell.task_id,
            result.limitation == DurableResult.model_fields["limitation"].default,
            result.runtime_config == cell.runtime_config,
            result.identity.task_id == cell.task_id,
            result.identity.fixture_id == cell.fixture_id,
            result.identity.fixture_fingerprint == cell.fixture_fingerprint,
            result.identity.config_fingerprint
            == canonical_sha256(cell.runtime_config.model_dump(mode="json")),
            result.identity.failure_schedule_id == cell.schedule_id,
            result.identity.failure_schedule_fingerprint == cell.schedule_fingerprint,
            result.identity.interruption_schedule_id == cell.interruption_schedule_id,
            result.identity.interruption_schedule_fingerprint
            == cell.interruption_schedule_fingerprint,
            result.identity.checkpointing.value == cell.checkpointing,
            result.identity.seed == cell.seed,
            result.resumed is resumed,
        )
    )


def execute_recovery_suite(
    root: Path,
    cells: tuple[RecoveryEvalCell, ...],
) -> dict[str, str]:
    """Run all four cells because checkpoint/reference comparison is one fixed unit."""
    by_diagnostic = {cell.diagnostic: cell for cell in cells}
    if set(by_diagnostic) != {
        "checkpoint_off",
        "checkpoint_on",
        "reference",
        "action_replay",
    }:
        raise RecoveryDiagnosticError("incomplete_recovery_suite")
    fixture = load_task("workspace.fix-config")
    failure = load_failure_schedule("workspace.lost-write-result.v1")
    interruption = load_interruption_schedule("workspace.post-write-boundary.v1")
    raw_paths: dict[str, str] = {}

    off = by_diagnostic["checkpoint_off"]
    off_dir = root / "runs" / off.suite_id / off.cell_id
    off_source = off_dir / "source"
    try:
        execute_durable_task(
            fixture,
            off_source,
            run_id="eval-recovery-checkpoint-off",
            config=off.runtime_config,
            failure_schedule=failure,
            checkpointing=CheckpointingMode.OFF,
            interruption_schedule=interruption,
            clock=DeterministicEvalClock(),
        )
    except ControlledInterruption:
        pass
    else:
        raise RecoveryDiagnosticError("checkpoint_off_did_not_interrupt")
    before = _manifest(off_source)
    process = _fixed_resume(off_source)
    unchanged = before == _manifest(off_source)
    off_physical_executions, off_physical_writes = _physical_usage(
        read_and_validate_journal(off_source / "events.jsonl")
    )
    off_artifact = RecoveryDiagnosticArtifact(
        cell_id=off.cell_id,
        diagnostic=off.diagnostic,
        passed=process.returncode == 2 and unchanged,
        verifier_state_success="N/A",
        runtime_completion_success=False,
        terminal="CONTROLLED_INTERRUPTION_NO_CHECKPOINT",
        error_category="resume_refused",
        source_unchanged=unchanged,
        second_process_used=True,
        second_process_exit_code=process.returncode,
        physical_executions=off_physical_executions,
        physical_write_executions=off_physical_writes,
        duplicate_side_effects=0,
    )
    off_path = off_dir / "diagnostic.json"
    _write_diagnostic(off_path, off_artifact)
    raw_paths[off.cell_id] = _relative(root, off_path)

    on = by_diagnostic["checkpoint_on"]
    on_dir = root / "runs" / on.suite_id / on.cell_id
    on_source = on_dir / "source"
    try:
        execute_durable_task(
            fixture,
            on_source,
            run_id="eval-recovery-checkpoint-on",
            config=on.runtime_config,
            failure_schedule=failure,
            checkpointing=CheckpointingMode.ON,
            interruption_schedule=interruption,
            clock=DeterministicEvalClock(),
        )
    except ControlledInterruption:
        pass
    else:
        raise RecoveryDiagnosticError("checkpoint_on_did_not_interrupt")
    process = _fixed_resume(on_source)
    if process.returncode != 0:
        raise RecoveryDiagnosticError("checkpoint_on_second_process_failed")
    on_result = DurableResult.model_validate_json(
        (on_source / "result.json").read_text(encoding="utf-8")
    )

    reference = by_diagnostic["reference"]
    reference_dir = root / "runs" / reference.suite_id / reference.cell_id
    reference_source = reference_dir / "source"
    reference_result = execute_durable_task(
        fixture,
        reference_source,
        run_id="eval-recovery-reference",
        config=reference.runtime_config,
        failure_schedule=failure,
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
        clock=DeterministicEvalClock(),
    )
    reference_match = (
        on_result.verifier == reference_result.verifier
        and workspace_digest(on_source / "workspace")
        == workspace_digest(reference_source / "workspace")
    )
    on_artifact = RecoveryDiagnosticArtifact(
        cell_id=on.cell_id,
        diagnostic=on.diagnostic,
        passed=(
            on_result.terminal_state is RuntimeState.SUCCEEDED
            and on_result.verifier.passed
            and on_result.resumed
            and reference_match
        ),
        verifier_state_success=on_result.verifier.passed,
        runtime_completion_success=on_result.terminal_state is RuntimeState.SUCCEEDED,
        terminal=on_result.terminal_state.value,
        source_result=_relative(root, on_source / "result.json"),
        source_unchanged="N/A",
        second_process_used=True,
        second_process_exit_code=process.returncode,
        physical_executions=on_result.usage.physical_executions,
        physical_write_executions=on_result.usage.physical_write_executions,
        duplicate_side_effects=on_result.usage.duplicate_side_effects,
        reference_match=reference_match,
    )
    on_path = on_dir / "diagnostic.json"
    _write_diagnostic(on_path, on_artifact)
    raw_paths[on.cell_id] = _relative(root, on_path)

    reference_artifact = RecoveryDiagnosticArtifact(
        cell_id=reference.cell_id,
        diagnostic=reference.diagnostic,
        passed=(
            reference_result.terminal_state is RuntimeState.SUCCEEDED
            and reference_result.verifier.passed
            and reference_match
        ),
        verifier_state_success=reference_result.verifier.passed,
        runtime_completion_success=(
            reference_result.terminal_state is RuntimeState.SUCCEEDED
        ),
        terminal=reference_result.terminal_state.value,
        source_result=_relative(root, reference_source / "result.json"),
        source_unchanged="N/A",
        second_process_used=False,
        physical_executions=reference_result.usage.physical_executions,
        physical_write_executions=reference_result.usage.physical_write_executions,
        duplicate_side_effects=reference_result.usage.duplicate_side_effects,
        reference_match=reference_match,
    )
    reference_path = reference_dir / "diagnostic.json"
    _write_diagnostic(reference_path, reference_artifact)
    raw_paths[reference.cell_id] = _relative(root, reference_path)

    replay = by_diagnostic["action_replay"]
    replay_dir = root / "runs" / replay.suite_id / replay.cell_id
    replay_source = replay_dir / "source"
    replay_output = replay_dir / "replay"
    source_result = execute_durable_task(
        fixture,
        replay_source,
        run_id="eval-recovery-action-replay",
        config=replay.runtime_config,
        failure_schedule=failure,
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
        clock=DeterministicEvalClock(),
        record_actions=True,
    )
    source_before = _manifest(replay_source)
    replay_result = replay_actions(replay_source, replay_output)
    source_unchanged = source_before == _manifest(replay_source)
    replay_artifact = RecoveryDiagnosticArtifact(
        cell_id=replay.cell_id,
        diagnostic=replay.diagnostic,
        passed=(
            replay_result.vertical_slice_matches == replay_result.vertical_slice_total == 1
            and replay_result.policy_calls == 0
            and source_unchanged
        ),
        verifier_state_success=replay_result.verifier.passed,
        runtime_completion_success="N/A",
        terminal="MATCHED" if replay_result.vertical_slice_matches == 1 else "MISMATCHED",
        source_result=_relative(root, replay_source / "result.json"),
        replay_result=_relative(root, replay_output / "replay-result.json"),
        source_unchanged=source_unchanged,
        second_process_used=False,
        policy_calls=replay_result.policy_calls,
        physical_executions=replay_result.usage.physical_executions,
        physical_write_executions=replay_result.usage.physical_write_executions,
        duplicate_side_effects=replay_result.usage.duplicate_side_effects,
        vertical_slice_matches=replay_result.vertical_slice_matches,
        vertical_slice_total=replay_result.vertical_slice_total,
        reference_match=(source_result.verifier == replay_result.verifier),
    )
    replay_path = replay_dir / "diagnostic.json"
    _write_diagnostic(replay_path, replay_artifact)
    raw_paths[replay.cell_id] = _relative(root, replay_path)
    return raw_paths


def validate_recovery_evidence(
    root: Path,
    cells: tuple[RecoveryEvalCell, ...],
) -> None:
    """Recompute recovery claims only from persisted evidence; execute nothing."""
    by_diagnostic = {cell.diagnostic: cell for cell in cells}
    try:
        artifacts = {
            diagnostic: RecoveryDiagnosticArtifact.model_validate_json(
                (
                    root
                    / "runs"
                    / cell.suite_id
                    / cell.cell_id
                    / "diagnostic.json"
                ).read_text(encoding="utf-8")
            )
            for diagnostic, cell in by_diagnostic.items()
        }
        off_cell = by_diagnostic["checkpoint_off"]
        off_source = root / "runs" / off_cell.suite_id / off_cell.cell_id / "source"
        off = artifacts["checkpoint_off"]
        off_records = read_and_validate_journal(off_source / "events.jsonl")
        off_started = off_records[0]
        off_physical_executions, off_physical_writes = _physical_usage(off_records)
        if (
            validate_trajectory(off_source).status != "valid_partial"
            or off_started.run_id != "eval-recovery-checkpoint-off"
            or off_started.task_id != off_cell.task_id
            or off_started.payload.fixture_id != off_cell.fixture_id
            or off_started.payload.fixture_fingerprint != off_cell.fixture_fingerprint
            or off_started.payload.config_fingerprint
            != canonical_sha256(off_cell.runtime_config.model_dump(mode="json"))
            or off_started.payload.failure_schedule_id != off_cell.schedule_id
            or off_started.payload.failure_schedule_fingerprint
            != off_cell.schedule_fingerprint
            or off_started.payload.interruption_schedule_id
            != off_cell.interruption_schedule_id
            or off_started.payload.interruption_schedule_fingerprint
            != off_cell.interruption_schedule_fingerprint
            or off_started.payload.checkpointing != off_cell.checkpointing
            or off_started.payload.seed != off_cell.seed
            or (off_source / "checkpoint.json").exists()
            or (off_source / "result.json").exists()
            or not off.passed
            or not off.second_process_used
            or off.second_process_exit_code != 2
            or not off.source_unchanged
            or off.verifier_state_success != "N/A"
            or off.runtime_completion_success is not False
            or off.terminal != "CONTROLLED_INTERRUPTION_NO_CHECKPOINT"
            or off.error_category != "resume_refused"
            or off.physical_executions != off_physical_executions
            or off.physical_write_executions != off_physical_writes
            or off.duplicate_side_effects != max(off_physical_writes - 1, 0)
        ):
            raise RecoveryDiagnosticError("checkpoint_off_evidence")

        on_cell = by_diagnostic["checkpoint_on"]
        on_source = root / "runs" / on_cell.suite_id / on_cell.cell_id / "source"
        on_result = DurableResult.model_validate_json(
            (on_source / "result.json").read_text(encoding="utf-8")
        )
        reference_cell = by_diagnostic["reference"]
        reference_source = (
            root / "runs" / reference_cell.suite_id / reference_cell.cell_id / "source"
        )
        reference_result = DurableResult.model_validate_json(
            (reference_source / "result.json").read_text(encoding="utf-8")
        )
        reference_match = (
            validate_trajectory(on_source).status == "valid_completed"
            and validate_trajectory(reference_source).status == "valid_completed"
            and on_result.resumed
            and not reference_result.resumed
            and on_result.verifier == reference_result.verifier
            and workspace_digest(on_source / "workspace")
            == workspace_digest(reference_source / "workspace")
        )
        on = artifacts["checkpoint_on"]
        reference = artifacts["reference"]
        if (
            not reference_match
            or not _durable_result_matches_cell(
                on_result,
                on_cell,
                run_id="eval-recovery-checkpoint-on",
                resumed=True,
            )
            or not _durable_result_matches_cell(
                reference_result,
                reference_cell,
                run_id="eval-recovery-reference",
                resumed=False,
            )
            or not on.passed
            or not on.second_process_used
            or on.second_process_exit_code != 0
            or on.source_unchanged != "N/A"
            or on.reference_match is not True
            or on.verifier_state_success != on_result.verifier.passed
            or on.runtime_completion_success
            != (on_result.terminal_state is RuntimeState.SUCCEEDED)
            or on.terminal != on_result.terminal_state.value
            or on.physical_executions != on_result.usage.physical_executions
            or on.physical_write_executions
            != on_result.usage.physical_write_executions
            or on.duplicate_side_effects != on_result.usage.duplicate_side_effects
            or not reference.passed
            or reference.second_process_used
            or reference.second_process_exit_code is not None
            or reference.source_unchanged != "N/A"
            or reference.reference_match is not True
            or reference.verifier_state_success != reference_result.verifier.passed
            or reference.runtime_completion_success
            != (reference_result.terminal_state is RuntimeState.SUCCEEDED)
            or reference.terminal != reference_result.terminal_state.value
            or reference.physical_executions
            != reference_result.usage.physical_executions
            or reference.physical_write_executions
            != reference_result.usage.physical_write_executions
            or reference.duplicate_side_effects
            != reference_result.usage.duplicate_side_effects
            or on.source_result
            != _relative(root, on_source / "result.json")
            or reference.source_result
            != _relative(root, reference_source / "result.json")
        ):
            raise RecoveryDiagnosticError("checkpoint_reference_evidence")

        replay_cell = by_diagnostic["action_replay"]
        replay_root = root / "runs" / replay_cell.suite_id / replay_cell.cell_id
        replay_source = replay_root / "source"
        replay_output = replay_root / "replay"
        replay_result = ActionReplayResult.model_validate_json(
            (replay_output / "replay-result.json").read_text(encoding="utf-8")
        )
        source_result = DurableResult.model_validate_json(
            (replay_source / "result.json").read_text(encoding="utf-8")
        )
        replay_artifact = artifacts["action_replay"]
        if (
            validate_trajectory(replay_source).status != "valid_completed"
            or not _durable_result_matches_cell(
                source_result,
                replay_cell,
                run_id="eval-recovery-action-replay",
                resumed=False,
            )
            or not source_result.verifier.passed
            or replay_result.vertical_slice_matches
            != replay_result.vertical_slice_total
            or replay_result.policy_calls != 0
            or replay_result.limitation
            != ActionReplayResult.model_fields["limitation"].default
            or replay_artifact.vertical_slice_matches
            != replay_result.vertical_slice_matches
            or replay_artifact.vertical_slice_total != replay_result.vertical_slice_total
            or replay_artifact.policy_calls != 0
            or replay_artifact.verifier_state_success != replay_result.verifier.passed
            or replay_artifact.runtime_completion_success != "N/A"
            or replay_artifact.terminal != "MATCHED"
            or replay_artifact.physical_executions
            != replay_result.usage.physical_executions
            or replay_artifact.physical_write_executions
            != replay_result.usage.physical_write_executions
            or replay_artifact.duplicate_side_effects
            != replay_result.usage.duplicate_side_effects
            or replay_artifact.reference_match
            != (source_result.verifier == replay_result.verifier)
            or not replay_artifact.passed
            or not replay_artifact.source_unchanged
            or replay_artifact.source_result
            != _relative(root, replay_source / "result.json")
            or replay_artifact.replay_result
            != _relative(root, replay_output / "replay-result.json")
        ):
            raise RecoveryDiagnosticError("action_replay_evidence")
    except (KeyError, OSError, UnicodeError, ValidationError, ValueError) as exc:
        if isinstance(exc, RecoveryDiagnosticError):
            raise
        raise RecoveryDiagnosticError("invalid_recovery_evidence") from exc
