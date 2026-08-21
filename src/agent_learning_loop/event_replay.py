"""Read-only structural validation for M3A event journals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from agent_learning_loop.checkpoint import CheckpointIdentityError, read_checkpoint
from agent_learning_loop.durable_schemas import (
    Checkpoint,
    DurableEvent,
    DurableResult,
    durable_result_summary_digest,
    verifier_summary_digest,
)
from agent_learning_loop.journal import JournalValidationError, read_and_validate_journal
from agent_learning_loop.runtime_schemas import TERMINAL_STATES, RuntimeState
from agent_learning_loop.runtime_state import ALLOWED_TRANSITIONS
from agent_learning_loop.schemas import StrictModel


class TrajectoryValidationError(ValueError):
    """The persisted trajectory is not a valid M3A partial or completed run."""


class TrajectoryValidationResult(StrictModel):
    schema_version: Literal["1"] = "1"
    status: Literal["valid_partial", "valid_completed"]
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    record_count: int = Field(ge=1)
    final_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_count: int = Field(ge=1)
    terminal_state: RuntimeState | None = None
    verifier_passed: bool | None = None
    checkpoint_id: str | None = None
    action_replay_match_rate: Literal["N/A"] = "N/A"


def workspace_digest(root: Path) -> str:
    """Hash relative names and file bytes without exposing either file contents or host paths."""
    entries: list[dict[str, str]] = []
    try:
        if root.is_symlink():
            raise TrajectoryValidationError("Workspace root cannot be a symbolic link")
        resolved_root = root.resolve(strict=True)
        candidates = sorted(root.rglob("*"))
        for path in candidates:
            if path.is_symlink():
                raise TrajectoryValidationError("Workspace cannot contain symbolic links")
            if not path.is_file():
                continue
            path.resolve(strict=True).relative_to(resolved_root)
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    except (OSError, ValueError) as exc:
        raise TrajectoryValidationError("Workspace digest could not be computed") from exc
    canonical = json.dumps(
        entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_trajectory(run_directory: Path) -> TrajectoryValidationResult:
    """Validate journal/checkpoint/result relationships without executing any component."""
    try:
        records = read_and_validate_journal(run_directory / "events.jsonl")
        return _validate_records(run_directory, records)
    except TrajectoryValidationError:
        raise
    except (JournalValidationError, OSError, UnicodeError, ValidationError) as exc:
        raise TrajectoryValidationError("trajectory failed strict M3A validation") from exc


def _validate_records(
    run_directory: Path, records: list[DurableEvent]
) -> TrajectoryValidationResult:
    if not records or records[0].event_kind != "run_started":
        raise TrajectoryValidationError("trajectory must begin with run_started")
    if sum(record.event_kind == "run_started" for record in records) != 1:
        raise TrajectoryValidationError("trajectory must contain one run_started record")

    state = RuntimeState.CREATED
    for record in records:
        if record.event_kind != "runtime_state_changed":
            continue
        try:
            source = RuntimeState(record.payload.from_state or "")
            target = RuntimeState(record.payload.to_state or "")
        except ValueError as exc:
            raise TrajectoryValidationError("state event names an unknown state") from exc
        if source is not state or target not in ALLOWED_TRANSITIONS[state]:
            raise TrajectoryValidationError("state events are not a legal continuous path")
        state = target

    interruption_indexes = [
        index
        for index, record in enumerate(records)
        if record.event_kind == "interruption_injected"
    ]
    resume_indexes = [
        index for index, record in enumerate(records) if record.event_kind == "run_resumed"
    ]
    finish_indexes = [
        index for index, record in enumerate(records) if record.event_kind == "run_finished"
    ]
    commit_indexes = [
        index
        for index, record in enumerate(records)
        if record.event_kind == "checkpoint_committed"
    ]
    verification_indexes = [
        index
        for index, record in enumerate(records)
        if record.event_kind == "verification_finished"
    ]
    checkpoint = _validate_checkpoint_if_present(run_directory, records)
    result_path = run_directory / "result.json"

    if not finish_indexes:
        if result_path.exists():
            raise TrajectoryValidationError("a partial trajectory cannot have a result")
        if len(interruption_indexes) != 1 or interruption_indexes[0] != len(records) - 1:
            raise TrajectoryValidationError("partial trajectory must end at one interruption")
        if resume_indexes or state is not RuntimeState.OBSERVING:
            raise TrajectoryValidationError(
                "partial trajectory cannot claim resume or terminal state"
            )
        _validate_interruption(records[-1], checkpoint, started=records[0])
        _validate_checkpoint_commit_order(
            records, interruption_indexes[0], commit_indexes, checkpoint
        )
        if any(record.segment != 0 for record in records):
            raise TrajectoryValidationError("partial trajectory must stay in segment zero")
        return _summary("valid_partial", records, checkpoint=checkpoint)

    if finish_indexes != [len(records) - 1] or state not in TERMINAL_STATES:
        raise TrajectoryValidationError("completed trajectory must end once in a terminal state")
    if interruption_indexes:
        if len(interruption_indexes) != 1 or len(resume_indexes) != 1:
            raise TrajectoryValidationError("resumed trajectory needs one interruption and resume")
        if resume_indexes[0] != interruption_indexes[0] + 1:
            raise TrajectoryValidationError("resume must immediately follow interruption")
        if checkpoint is None:
            raise TrajectoryValidationError("resumed trajectory needs a committed checkpoint")
        _validate_interruption(
            records[interruption_indexes[0]], checkpoint, started=records[0]
        )
        _validate_checkpoint_commit_order(
            records, interruption_indexes[0], commit_indexes, checkpoint
        )
        resumed = records[resume_indexes[0]]
        if (
            resumed.segment != 1
            or resumed.step_index != checkpoint.resume_step
            or resumed.payload.checkpoint_id != checkpoint.checkpoint_id
            or resumed.payload.checkpoint_step != checkpoint.resume_step
            or resumed.payload.resume_target != checkpoint.resume_target.value
            or any(
                record.segment != 0
                for record in records[: interruption_indexes[0] + 1]
            )
            or any(record.segment != 1 for record in records[resume_indexes[0] :])
        ):
            raise TrajectoryValidationError("resumed trajectory has invalid segment semantics")
    elif (
        resume_indexes
        or checkpoint is not None
        or commit_indexes
        or records[0].payload.checkpointing != "off"
        or records[0].payload.interruption_schedule_id is not None
        or records[0].payload.interruption_schedule_fingerprint is not None
        or any(record.segment != 0 for record in records)
    ):
        raise TrajectoryValidationError("uninterrupted trajectory cannot claim interruption state")

    if verification_indexes != [len(records) - 3]:
        raise TrajectoryValidationError("completed trajectory needs one terminal verification")
    verification = records[verification_indexes[0]]
    terminal_transition = records[-2]
    last = records[-1]
    if (
        terminal_transition.event_kind != "runtime_state_changed"
        or terminal_transition.payload.to_state != state.value
        or last.payload.terminal_state != state.value
        or verification.payload.verifier_passed != last.payload.verifier_passed
        or verification.payload.verifier_summary_digest is None
        or verification.payload.verifier_summary_digest
        != last.payload.verifier_summary_digest
        or last.payload.result_summary_digest is None
    ):
        raise TrajectoryValidationError("terminal events are not internally consistent")

    try:
        result = DurableResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TrajectoryValidationError("completed trajectory result is invalid") from exc
    if (
        result.run_id != last.run_id
        or result.task_id != last.task_id
        or result.terminal_state is not state
        or result.journal_record_count != len(records)
        or result.journal_final_hash != last.record_hash
        or result.segment_count != last.segment + 1
        or result.resumed != bool(resume_indexes)
        or result.verifier.passed != last.payload.verifier_passed
        or not _result_identity_matches_start(result, records[0])
        or verification.payload.verifier_summary_digest
        != verifier_summary_digest(result.verifier)
        or last.payload.result_summary_digest
        != durable_result_summary_digest(result.summary())
    ):
        raise TrajectoryValidationError("result does not match the completed journal")
    if checkpoint is not None and (
        result.checkpoint_id != checkpoint.checkpoint_id
        or result.identity != checkpoint.identity
        or result.runtime_config != checkpoint.runtime_config
    ):
        raise TrajectoryValidationError("result checkpoint identity changed")
    return _summary(
        "valid_completed",
        records,
        checkpoint=checkpoint,
        terminal_state=state,
        verifier_passed=result.verifier.passed,
    )


def _validate_checkpoint_if_present(
    run_directory: Path, records: list[DurableEvent]
) -> Checkpoint | None:
    path = run_directory / "checkpoint.json"
    committed = [record for record in records if record.event_kind == "checkpoint_committed"]
    if not path.exists():
        if committed:
            raise TrajectoryValidationError("journal claims a missing checkpoint")
        return None
    try:
        checkpoint = read_checkpoint(path)
    except (CheckpointIdentityError, OSError, UnicodeError, ValidationError) as exc:
        raise TrajectoryValidationError("checkpoint failed strict validation") from exc
    if len(committed) != 1:
        raise TrajectoryValidationError("checkpoint must have one commit record")
    if checkpoint.journal_record_count > len(records):
        raise TrajectoryValidationError("checkpoint journal prefix is out of range")
    prefix = records[checkpoint.journal_record_count - 1]
    event = committed[0]
    started = records[0]
    config_fingerprint = hashlib.sha256(
        json.dumps(
            checkpoint.runtime_config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        checkpoint.run_id != started.run_id
        or checkpoint.identity.task_id != started.task_id
        or checkpoint.identity.fixture_id != started.payload.fixture_id
        or checkpoint.identity.fixture_fingerprint != started.payload.fixture_fingerprint
        or checkpoint.identity.config_fingerprint != started.payload.config_fingerprint
        or checkpoint.identity.failure_schedule_id != started.payload.failure_schedule_id
        or checkpoint.identity.failure_schedule_fingerprint
        != started.payload.failure_schedule_fingerprint
        or checkpoint.identity.interruption_schedule_id
        != started.payload.interruption_schedule_id
        or checkpoint.identity.interruption_schedule_fingerprint
        != started.payload.interruption_schedule_fingerprint
        or checkpoint.identity.seed != started.payload.seed
        or checkpoint.identity.checkpointing.value != started.payload.checkpointing
        or config_fingerprint != checkpoint.identity.config_fingerprint
        or checkpoint.journal_final_hash != prefix.record_hash
        or event.payload.checkpoint_id != checkpoint.checkpoint_id
        or event.payload.checkpoint_record_count != checkpoint.journal_record_count
        or event.payload.checkpoint_final_hash != checkpoint.journal_final_hash
        or event.payload.checkpoint_step != checkpoint.resume_step
        or event.sequence != checkpoint.journal_record_count
        or workspace_digest(run_directory / "workspace") != checkpoint.workspace_digest
    ):
        raise TrajectoryValidationError("checkpoint does not match journal or Workspace")
    return checkpoint


def _validate_checkpoint_commit_order(
    records: list[DurableEvent],
    interruption_index: int,
    commit_indexes: list[int],
    checkpoint: Checkpoint | None,
) -> None:
    if checkpoint is None:
        if commit_indexes:
            raise TrajectoryValidationError("checkpoint-off trajectory cannot commit")
        return
    if commit_indexes != [interruption_index - 1]:
        raise TrajectoryValidationError(
            "checkpoint commit must immediately precede interruption"
        )
    committed = records[commit_indexes[0]]
    interrupted = records[interruption_index]
    if committed.segment != 0 or interrupted.segment != 0:
        raise TrajectoryValidationError("checkpoint and interruption belong to segment zero")


def _validate_interruption(
    record: DurableEvent, checkpoint: Checkpoint | None, *, started: DurableEvent
) -> None:
    if record.step_index != 2 or record.payload.boundary != "post_observation":
        raise TrajectoryValidationError("interruption is not the fixed safe boundary")
    if (
        record.payload.interruption_schedule_id
        != started.payload.interruption_schedule_id
        or record.payload.interruption_schedule_fingerprint
        != started.payload.interruption_schedule_fingerprint
    ):
        raise TrajectoryValidationError("interruption identity changed")
    checkpointing = record.payload.checkpointing
    if (
        checkpointing != started.payload.checkpointing
        or (checkpointing == "on") != (checkpoint is not None)
        or (
            checkpoint is not None
            and record.payload.checkpoint_id != checkpoint.checkpoint_id
        )
        or (checkpoint is None and record.payload.checkpoint_id is not None)
    ):
        raise TrajectoryValidationError("interruption checkpoint mode is inconsistent")


def _result_identity_matches_start(
    result: DurableResult, started: DurableEvent
) -> bool:
    config_fingerprint = hashlib.sha256(
        json.dumps(
            result.runtime_config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    identity = result.identity
    return (
        identity.task_id == started.task_id
        and identity.fixture_id == started.payload.fixture_id
        and identity.fixture_fingerprint == started.payload.fixture_fingerprint
        and identity.config_fingerprint == started.payload.config_fingerprint
        and identity.failure_schedule_id == started.payload.failure_schedule_id
        and identity.failure_schedule_fingerprint
        == started.payload.failure_schedule_fingerprint
        and identity.interruption_schedule_id
        == started.payload.interruption_schedule_id
        and identity.interruption_schedule_fingerprint
        == started.payload.interruption_schedule_fingerprint
        and identity.checkpointing.value == started.payload.checkpointing
        and identity.seed == started.payload.seed
        and identity.config_fingerprint == config_fingerprint
    )


def _summary(
    status: Literal["valid_partial", "valid_completed"],
    records: list[DurableEvent],
    *,
    checkpoint: Checkpoint | None,
    terminal_state: RuntimeState | None = None,
    verifier_passed: bool | None = None,
) -> TrajectoryValidationResult:
    return TrajectoryValidationResult(
        status=status,
        run_id=records[0].run_id,
        task_id=records[0].task_id,
        record_count=len(records),
        final_hash=records[-1].record_hash,
        segment_count=records[-1].segment + 1,
        terminal_state=terminal_state,
        verifier_passed=verifier_passed,
        checkpoint_id=checkpoint.checkpoint_id if checkpoint is not None else None,
    )
