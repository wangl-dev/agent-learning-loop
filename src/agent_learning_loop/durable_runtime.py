"""Fixed M3A durable Runtime slice with safe-boundary checkpoint and resume."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_learning_loop.checkpoint import (
    checkpoint_id,
    read_checkpoint,
    write_checkpoint_atomic,
)
from agent_learning_loop.clock import ClockProtocol, SystemClock
from agent_learning_loop.durable_schemas import (
    Checkpoint,
    CheckpointingMode,
    DurableEvent,
    DurableEventPayload,
    DurableResult,
    DurableResultSummary,
    DurableUsage,
    ExperimentIdentity,
    FailureTrackerState,
    IdempotencyEntry,
    ToolOccurrence,
    durable_result_summary_digest,
    verifier_summary_digest,
)
from agent_learning_loop.event_replay import (
    TrajectoryValidationError,
    validate_trajectory,
    workspace_digest,
)
from agent_learning_loop.failure_schedules import (
    FailureSchedule,
    FailureScheduleMismatchError,
    fingerprint_schedule,
    load_failure_schedule,
    validate_schedule_for_task,
)
from agent_learning_loop.interruption_schedules import (
    InterruptionSchedule,
    fingerprint_interruption_schedule,
    load_interruption_schedule,
    validate_interruption_schedule,
)
from agent_learning_loop.journal import AppendOnlyJournal, read_and_validate_journal
from agent_learning_loop.policy import ScriptedPolicy
from agent_learning_loop.runtime import idempotency_key, request_fingerprint
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode, RuntimeState
from agent_learning_loop.runtime_state import RuntimeStateMachine
from agent_learning_loop.schemas import (
    Action,
    ToolName,
    ToolResult,
    WorkspaceSnapshot,
    WorkspaceTaskFixture,
)
from agent_learning_loop.tasks import load_task
from agent_learning_loop.verifier import WorkspaceStateVerifier
from agent_learning_loop.vertical_slice import OutputExistsError
from agent_learning_loop.workspace import WorkspaceEnvironment
from agent_learning_loop.workspace_tools import ReadTextTool, WriteTextTool


class ControlledInterruption(RuntimeError):
    """The one project-authored M3A interruption fired after a safe boundary."""


class DurableValidationError(ValueError):
    """A durable run identity or persisted resume precondition is invalid."""


@dataclass
class _Counters:
    steps: int = 0
    tool_calls: int = 0
    physical_executions: int = 0
    physical_write_executions: int = 0
    side_effect_executions: int = 0
    duplicate_side_effects: int = 0
    retries: int = 0
    idempotency_hits: int = 0
    backoff_seconds: float = 0.0
    elapsed_seconds: float = 0.0

    @classmethod
    def from_public(cls, usage: DurableUsage) -> _Counters:
        return cls(**usage.model_dump(exclude={"schema_version"}))

    def public(self, *, segment_elapsed: float = 0.0) -> DurableUsage:
        return DurableUsage(
            **{
                **self.__dict__,
                "elapsed_seconds": max(self.elapsed_seconds + segment_elapsed, 0.0),
            }
        )


@dataclass
class _FailureTracker:
    occurrences: dict[ToolName, int]
    injection_count: int = 0

    def begin(self, tool_name: ToolName) -> int:
        occurrence = self.occurrences.get(tool_name, 0) + 1
        self.occurrences[tool_name] = occurrence
        return occurrence

    def public(self) -> FailureTrackerState:
        return FailureTrackerState(
            occurrences=[
                ToolOccurrence(tool_name=name, count=count)
                for name, count in sorted(self.occurrences.items())
            ],
            injection_count=self.injection_count,
        )


class _IdempotencyStore:
    def __init__(self, entries: list[IdempotencyEntry] | None = None) -> None:
        self.entries = {entry.key: entry for entry in entries or []}

    def save(self, key: str, fingerprint: str, result: ToolResult) -> None:
        path = result.payload.get("path")
        bytes_written = result.payload.get("bytes_written")
        if not isinstance(path, str) or not isinstance(bytes_written, int):
            raise DurableValidationError("write result cannot form a safe idempotency entry")
        self.entries[key] = IdempotencyEntry(
            key=key,
            request_fingerprint=fingerprint,
            tool_name="write_text",
            result_path=path,
            bytes_written=bytes_written,
        )

    def lookup(self, key: str, fingerprint: str) -> IdempotencyEntry | None:
        entry = self.entries.get(key)
        if entry is not None and entry.request_fingerprint != fingerprint:
            raise DurableValidationError("idempotency identity conflict")
        return entry

    def public(self) -> list[IdempotencyEntry]:
        return [self.entries[key] for key in sorted(self.entries)]


def execute_durable_task(
    fixture: WorkspaceTaskFixture,
    run_directory: Path,
    *,
    run_id: str,
    config: RuntimeConfig,
    failure_schedule: FailureSchedule,
    checkpointing: CheckpointingMode,
    interruption_schedule: InterruptionSchedule | None,
    clock: ClockProtocol | None = None,
) -> DurableResult:
    """Execute the one M3A slice; a configured interruption deliberately raises."""
    if run_directory.exists() and any(run_directory.iterdir()):
        raise OutputExistsError(f"run directory is not empty: {run_directory}")
    effective_config, identity = _validated_identity(
        fixture,
        config,
        failure_schedule,
        checkpointing,
        interruption_schedule,
    )
    run_directory.mkdir(parents=True, exist_ok=True)
    journal = AppendOnlyJournal.create(
        run_directory / "events.jsonl", run_id=run_id, task_id=fixture.task.task_id
    )
    environment: WorkspaceEnvironment | None = None
    runtime_clock = clock or SystemClock()
    segment_start = runtime_clock.monotonic()
    try:
        journal.append(
            "run_started",
            step_index=0,
            attempt=0,
            payload=_identity_payload(identity, mode="safeguarded"),
        )
        environment = WorkspaceEnvironment(run_directory / "workspace")
        policy = ScriptedPolicy()
        verifier = WorkspaceStateVerifier()
        read_tool = ReadTextTool()
        write_tool = WriteTextTool()
        machine = RuntimeStateMachine()
        counters = _Counters()
        tracker = _FailureTracker({})
        store = _IdempotencyStore()

        _transition(machine, RuntimeState.RESETTING, journal, counters.steps)
        observation = environment.reset(
            fixture.private.setup, task_id=fixture.task.task_id
        )
        initial = environment.snapshot()
        _require_deadline(counters, effective_config, segment_start, runtime_clock)
        _transition(machine, RuntimeState.READY, journal, counters.steps)

        while True:
            _require_decision_budget(
                counters, effective_config, segment_start, runtime_clock
            )
            counters.steps += 1
            _transition(machine, RuntimeState.DECIDING, journal, counters.steps)
            action = policy.decide(fixture.task, observation)
            _require_deadline(counters, effective_config, segment_start, runtime_clock)
            if action is None:
                return _finish_success(
                    fixture=fixture,
                    run_directory=run_directory,
                    journal=journal,
                    environment=environment,
                    verifier=verifier,
                    initial=initial,
                    machine=machine,
                    counters=counters,
                    identity=identity,
                    config=effective_config,
                    resumed=False,
                    checkpoint=None,
                    segment_start=segment_start,
                    clock=runtime_clock,
                )
            _transition(machine, RuntimeState.VALIDATING_ACTION, journal, counters.steps)
            if action.tool_name not in fixture.task.allowed_tools:
                raise DurableValidationError("scripted action is outside the task allowlist")
            _transition(machine, RuntimeState.EXECUTING_TOOL, journal, counters.steps)
            result, attempt = _execute_action(
                action=action,
                task_id=fixture.task.task_id,
                step_index=counters.steps,
                config=effective_config,
                failure_schedule=failure_schedule,
                environment=environment,
                read_tool=read_tool,
                write_tool=write_tool,
                tracker=tracker,
                store=store,
                counters=counters,
                journal=journal,
                clock=runtime_clock,
                segment_start=segment_start,
            )
            _transition(machine, RuntimeState.OBSERVING, journal, counters.steps)
            observation = environment.observe(
                task_id=fixture.task.task_id,
                step_index=counters.steps,
                last_tool_result=result,
            )
            _require_deadline(counters, effective_config, segment_start, runtime_clock)
            if interruption_schedule is not None and counters.steps == 2:
                _require_deadline(
                    counters, effective_config, segment_start, runtime_clock
                )
                checkpoint = None
                if checkpointing is CheckpointingMode.ON:
                    checkpoint = _make_checkpoint(
                        run_id=run_id,
                        identity=identity,
                        config=effective_config,
                        counters=counters,
                        tracker=tracker,
                        store=store,
                        workspace_root=run_directory / "workspace",
                        journal=journal,
                        segment_start=segment_start,
                        clock=runtime_clock,
                    )
                    _require_deadline(
                        counters, effective_config, segment_start, runtime_clock
                    )
                    checkpoint_path = run_directory / "checkpoint.json"
                    write_checkpoint_atomic(checkpoint_path, checkpoint)
                    try:
                        _require_deadline(
                            counters, effective_config, segment_start, runtime_clock
                        )
                    except DurableValidationError:
                        checkpoint_path.unlink(missing_ok=True)
                        raise
                    journal.append(
                        "checkpoint_committed",
                        step_index=counters.steps,
                        attempt=attempt,
                        payload=DurableEventPayload(
                            checkpoint_id=checkpoint.checkpoint_id,
                            checkpoint_step=checkpoint.resume_step,
                            checkpoint_record_count=checkpoint.journal_record_count,
                            checkpoint_final_hash=checkpoint.journal_final_hash,
                        ),
                    )
                _require_deadline(
                    counters, effective_config, segment_start, runtime_clock
                )
                journal.append(
                    "interruption_injected",
                    step_index=counters.steps,
                    attempt=attempt,
                    payload=DurableEventPayload(
                        checkpointing=checkpointing.value,
                        interruption_schedule_id=interruption_schedule.schedule_id,
                        interruption_schedule_fingerprint=fingerprint_interruption_schedule(
                            interruption_schedule
                        ),
                        boundary=interruption_schedule.boundary,
                        checkpoint_id=(checkpoint.checkpoint_id if checkpoint else None),
                    ),
                )
                raise ControlledInterruption(
                    "controlled M3A interruption after the step-2 Observation boundary"
                )
    finally:
        if environment is not None:
            environment.close()
        journal.close()


def resume_durable_task(
    run_directory: Path, *, clock: ClockProtocol | None = None
) -> DurableResult:
    """Validate a committed fixed checkpoint, then continue in a new journal segment."""
    try:
        validation = validate_trajectory(run_directory)
    except TrajectoryValidationError as exc:
        raise DurableValidationError(
            "trajectory or Workspace validation failed before resume"
        ) from exc
    if validation.status != "valid_partial":
        raise DurableValidationError("only a valid partial trajectory can resume")
    checkpoint_path = run_directory / "checkpoint.json"
    if not checkpoint_path.exists():
        raise DurableValidationError("resume requires a committed checkpoint")
    try:
        checkpoint = read_checkpoint(checkpoint_path)
        records = read_and_validate_journal(run_directory / "events.jsonl")
        fixture = load_task(checkpoint.identity.task_id)
        failure_schedule = load_failure_schedule(checkpoint.identity.failure_schedule_id)
        if checkpoint.identity.interruption_schedule_id is None:
            raise DurableValidationError("checkpoint has no interruption identity")
        interruption_schedule = load_interruption_schedule(
            checkpoint.identity.interruption_schedule_id
        )
        effective_config, expected_identity = _validated_identity(
            fixture,
            checkpoint.runtime_config,
            failure_schedule,
            CheckpointingMode.ON,
            interruption_schedule,
        )
        _validate_resume_snapshot(
            checkpoint,
            expected_identity=expected_identity,
            records=records,
            run_id=validation.run_id,
            fixture=fixture,
        )
    except DurableValidationError:
        raise
    except Exception as exc:
        raise DurableValidationError("checkpoint identity validation failed") from exc

    # No Environment, Policy, Tool, or Verifier is constructed before this point.
    runtime_clock = clock or SystemClock()
    environment = WorkspaceEnvironment(run_directory / "workspace")
    policy = ScriptedPolicy()
    verifier = WorkspaceStateVerifier()
    machine = RuntimeStateMachine(state=checkpoint.runtime_state)
    counters = _Counters.from_public(checkpoint.usage)
    segment_start = runtime_clock.monotonic()
    journal = AppendOnlyJournal.resume(
        run_directory / "events.jsonl", records=records, segment=checkpoint.segment + 1
    )
    try:
        journal.append(
            "run_resumed",
            step_index=checkpoint.resume_step,
            attempt=0,
            payload=DurableEventPayload(
                checkpoint_id=checkpoint.checkpoint_id,
                checkpoint_step=checkpoint.resume_step,
                resume_target=checkpoint.resume_target.value,
            ),
        )
        _require_decision_budget(counters, effective_config, segment_start, runtime_clock)
        counters.steps += 1
        _transition(machine, RuntimeState.DECIDING, journal, counters.steps)
        observation = environment.observe(
            task_id=fixture.task.task_id,
            step_index=checkpoint.resume_step,
            last_tool_result=ToolResult(
                status="ok",
                payload={
                    "path": checkpoint.idempotency_entries[0].result_path,
                    "bytes_written": checkpoint.idempotency_entries[0].bytes_written,
                },
            ),
        )
        _require_deadline(counters, effective_config, segment_start, runtime_clock)
        action = policy.decide(fixture.task, observation)
        _require_deadline(counters, effective_config, segment_start, runtime_clock)
        if action is not None:
            raise DurableValidationError("fixed resume point did not reach verification")
        initial = WorkspaceSnapshot(files=dict(fixture.private.setup.files))
        return _finish_success(
            fixture=fixture,
            run_directory=run_directory,
            journal=journal,
            environment=environment,
            verifier=verifier,
            initial=initial,
            machine=machine,
            counters=counters,
            identity=expected_identity,
            config=effective_config,
            resumed=True,
            checkpoint=checkpoint,
            segment_start=segment_start,
            clock=runtime_clock,
        )
    finally:
        environment.close()
        journal.close()


def _validated_identity(
    fixture: WorkspaceTaskFixture,
    config: RuntimeConfig,
    failure_schedule: FailureSchedule,
    checkpointing: CheckpointingMode,
    interruption_schedule: InterruptionSchedule | None,
) -> tuple[RuntimeConfig, ExperimentIdentity]:
    if fixture.task.task_id != "workspace.fix-config":
        raise DurableValidationError("M3A supports only workspace.fix-config")
    if config.mode is not RuntimeMode.SAFEGUARDED:
        raise DurableValidationError("M3A supports only safeguarded mode")
    validate_schedule_for_task(failure_schedule, fixture.task.task_id)
    schedule_fingerprint = fingerprint_schedule(failure_schedule)
    if (
        failure_schedule.schedule_id != "workspace.lost-write-result.v1"
        or config.schedule_id != failure_schedule.schedule_id
        or config.seed != failure_schedule.seed
        or (
            config.schedule_fingerprint is not None
            and config.schedule_fingerprint != schedule_fingerprint
        )
    ):
        raise FailureScheduleMismatchError("M3A config/failure identity mismatch")
    if config.max_steps < 3 or config.max_tool_calls < 3:
        raise DurableValidationError("M3A fixed experiment needs three steps/tool calls")
    if interruption_schedule is not None:
        validate_interruption_schedule(
            interruption_schedule, task_id=fixture.task.task_id
        )
    elif checkpointing is CheckpointingMode.ON:
        raise DurableValidationError("checkpointing on requires the fixed interruption")
    effective_config = RuntimeConfig.model_validate(
        {**config.model_dump(), "schedule_fingerprint": schedule_fingerprint}
    )
    interruption_fingerprint = (
        fingerprint_interruption_schedule(interruption_schedule)
        if interruption_schedule is not None
        else None
    )
    identity = ExperimentIdentity(
        task_id=fixture.task.task_id,
        fixture_id=fixture.task.fixture_id,
        fixture_fingerprint=_fingerprint(fixture.model_dump(mode="json")),
        config_fingerprint=_fingerprint(effective_config.model_dump(mode="json")),
        failure_schedule_id=failure_schedule.schedule_id,
        failure_schedule_fingerprint=schedule_fingerprint,
        interruption_schedule_id=(
            interruption_schedule.schedule_id if interruption_schedule is not None else None
        ),
        interruption_schedule_fingerprint=interruption_fingerprint,
        checkpointing=checkpointing,
        seed=failure_schedule.seed,
    )
    return effective_config, identity


def _identity_payload(
    identity: ExperimentIdentity, *, mode: Literal["safeguarded"]
) -> DurableEventPayload:
    return DurableEventPayload(
        mode=mode,
        checkpointing=identity.checkpointing.value,
        fixture_id=identity.fixture_id,
        fixture_fingerprint=identity.fixture_fingerprint,
        config_fingerprint=identity.config_fingerprint,
        failure_schedule_id=identity.failure_schedule_id,
        failure_schedule_fingerprint=identity.failure_schedule_fingerprint,
        interruption_schedule_id=identity.interruption_schedule_id,
        interruption_schedule_fingerprint=identity.interruption_schedule_fingerprint,
        seed=identity.seed,
    )


def _execute_action(
    *,
    action: Action,
    task_id: str,
    step_index: int,
    config: RuntimeConfig,
    failure_schedule: FailureSchedule,
    environment: WorkspaceEnvironment,
    read_tool: ReadTextTool,
    write_tool: WriteTextTool,
    tracker: _FailureTracker,
    store: _IdempotencyStore,
    counters: _Counters,
    journal: AppendOnlyJournal,
    clock: ClockProtocol,
    segment_start: float,
) -> tuple[ToolResult, int]:
    fingerprint = request_fingerprint(action)
    key = idempotency_key(task_id, step_index, fingerprint)
    for attempt in range(1, config.max_attempts + 1):
        _require_deadline(counters, config, segment_start, clock)
        if counters.tool_calls >= config.max_tool_calls:
            raise DurableValidationError("tool-call budget exhausted")
        counters.tool_calls += 1
        occurrence = tracker.begin(action.tool_name)
        journal.append(
            "attempt_started",
            step_index=step_index,
            attempt=attempt,
            payload=DurableEventPayload(
                tool_name=action.tool_name, occurrence=occurrence
            ),
        )
        if action.tool_name == "write_text" and config.idempotency_enabled:
            cached = store.lookup(key, fingerprint)
            if cached is not None:
                counters.idempotency_hits += 1
                journal.append(
                    "idempotency_hit",
                    step_index=step_index,
                    attempt=attempt,
                    payload=DurableEventPayload(tool_name="write_text"),
                )
                result = ToolResult(
                    status="ok",
                    payload={
                        "path": cached.result_path,
                        "bytes_written": cached.bytes_written,
                    },
                )
                journal.append(
                    "attempt_completed",
                    step_index=step_index,
                    attempt=attempt,
                    payload=DurableEventPayload(tool_name="write_text", status="ok"),
                )
                _require_deadline(counters, config, segment_start, clock)
                return result, attempt

        counters.physical_executions += 1
        if action.tool_name == "read_text":
            result = read_tool.execute(environment, action)
        elif action.tool_name == "write_text":
            counters.physical_write_executions += 1
            result = write_tool.execute(environment, action)
        else:
            raise DurableValidationError("M3A action selected an unsupported tool")
        _require_deadline(counters, config, segment_start, clock)
        if result.status != "ok":
            raise DurableValidationError("fixed Workspace tool action failed")
        if action.tool_name == "write_text":
            counters.side_effect_executions += 1
            if counters.side_effect_executions > 1:
                counters.duplicate_side_effects += 1
            store.save(key, fingerprint, result)

        inject = (
            action.tool_name == failure_schedule.rule.target_tool
            and occurrence == failure_schedule.rule.occurrence
            and failure_schedule.rule.injection_phase == "after_success"
        )
        if inject:
            tracker.injection_count += 1
            journal.append(
                "failure_injected",
                step_index=step_index,
                attempt=attempt,
                payload=DurableEventPayload(
                    tool_name=action.tool_name,
                    occurrence=occurrence,
                    failure_kind="result_lost",
                    error_category="tool_transient",
                    retryable=True,
                ),
            )
            journal.append(
                "attempt_completed",
                step_index=step_index,
                attempt=attempt,
                payload=DurableEventPayload(tool_name=action.tool_name, status="error"),
            )
            if attempt >= config.max_attempts:
                raise DurableValidationError("fixed result-loss retry was unavailable")
            delay = config.retry_backoff_seconds[attempt - 1]
            remaining = config.timeout_seconds - _elapsed_seconds(
                counters, segment_start, clock
            )
            if delay >= remaining:
                raise DurableValidationError(
                    "retry backoff would exhaust remaining deadline"
                )
            counters.retries += 1
            counters.backoff_seconds += delay
            journal.append(
                "retry_scheduled",
                step_index=step_index,
                attempt=attempt,
                payload=DurableEventPayload(
                    tool_name=action.tool_name,
                    failed_attempt=attempt,
                    next_attempt=attempt + 1,
                    backoff_seconds=delay,
                ),
            )
            if delay:
                clock.sleep(delay)
            _require_deadline(counters, config, segment_start, clock)
            continue
        journal.append(
            "attempt_completed",
            step_index=step_index,
            attempt=attempt,
            payload=DurableEventPayload(tool_name=action.tool_name, status="ok"),
        )
        return result, attempt
    raise DurableValidationError("fixed retry loop ended unexpectedly")


def _make_checkpoint(
    *,
    run_id: str,
    identity: ExperimentIdentity,
    config: RuntimeConfig,
    counters: _Counters,
    tracker: _FailureTracker,
    store: _IdempotencyStore,
    workspace_root: Path,
    journal: AppendOnlyJournal,
    segment_start: float,
    clock: ClockProtocol,
) -> Checkpoint:
    current_workspace_digest = workspace_digest(workspace_root)
    draft = Checkpoint(
        checkpoint_id="0" * 64,
        run_id=run_id,
        identity=identity,
        runtime_config=config,
        resume_step=counters.steps,
        runtime_state=RuntimeState.OBSERVING,
        resume_target=RuntimeState.DECIDING,
        usage=counters.public(segment_elapsed=clock.monotonic() - segment_start),
        failure_tracker=tracker.public(),
        idempotency_entries=store.public(),
        workspace_digest=current_workspace_digest,
        journal_record_count=journal.record_count,
        journal_final_hash=journal.final_hash,
        segment=journal.segment,
    )
    return draft.model_copy(update={"checkpoint_id": checkpoint_id(draft)})


def _validate_resume_snapshot(
    checkpoint: Checkpoint,
    *,
    expected_identity: ExperimentIdentity,
    records: Sequence[DurableEvent],
    run_id: str,
    fixture: WorkspaceTaskFixture,
) -> None:
    if checkpoint.run_id != run_id or checkpoint.identity != expected_identity:
        raise DurableValidationError("run/task/config/schedule identity mismatch")
    if checkpoint.resume_step != 2 or checkpoint.segment != 0:
        raise DurableValidationError("checkpoint is not the fixed M3A resume point")
    _validate_persisted_counters(checkpoint, records)
    action = Action(
        tool_name="write_text",
        arguments={"path": "app.conf", "content": "mode=production\nport=8080\n"},
    )
    fingerprint = request_fingerprint(action)
    key = idempotency_key(fixture.task.task_id, 2, fingerprint)
    expected_bytes = len(str(action.arguments["content"]).encode("utf-8"))
    if checkpoint.idempotency_entries != [
        IdempotencyEntry(
            key=key,
            request_fingerprint=fingerprint,
            tool_name="write_text",
            result_path="app.conf",
            bytes_written=expected_bytes,
        )
    ]:
        raise DurableValidationError("idempotency checkpoint state is inconsistent")
    if len(records) <= checkpoint.journal_record_count:
        raise DurableValidationError("checkpoint journal suffix is incomplete")
    if checkpoint.usage.elapsed_seconds >= checkpoint.runtime_config.timeout_seconds:
        raise DurableValidationError("checkpoint has no remaining elapsed-time budget")


def _validate_persisted_counters(
    checkpoint: Checkpoint, records: Sequence[DurableEvent]
) -> None:
    prefix = records[: checkpoint.journal_record_count]
    attempts = [record for record in prefix if record.event_kind == "attempt_started"]
    hits = [record for record in prefix if record.event_kind == "idempotency_hit"]
    retries = [record for record in prefix if record.event_kind == "retry_scheduled"]
    injections = [record for record in prefix if record.event_kind == "failure_injected"]
    occurrence_lists: dict[ToolName, list[int]] = {}
    for record in attempts:
        if record.payload.tool_name is None or record.payload.occurrence is None:
            raise DurableValidationError("attempt record lacks failure-tracker identity")
        occurrence_lists.setdefault(record.payload.tool_name, []).append(
            record.payload.occurrence
        )
    if any(values != list(range(1, len(values) + 1)) for values in occurrence_lists.values()):
        raise DurableValidationError("journal tool occurrences are not continuous")
    write_attempts = sum(
        record.payload.tool_name == "write_text" for record in attempts
    )
    physical_executions = len(attempts) - len(hits)
    physical_writes = write_attempts - len(hits)
    expected_usage = {
        "steps": checkpoint.resume_step,
        "tool_calls": len(attempts),
        "physical_executions": physical_executions,
        "physical_write_executions": physical_writes,
        "side_effect_executions": physical_writes,
        "duplicate_side_effects": max(physical_writes - 1, 0),
        "retries": len(retries),
        "idempotency_hits": len(hits),
        "backoff_seconds": sum(
            record.payload.backoff_seconds or 0.0 for record in retries
        ),
    }
    actual_usage = checkpoint.usage.model_dump()
    if any(actual_usage[name] != value for name, value in expected_usage.items()):
        raise DurableValidationError("checkpoint counters do not match journal boundary")
    occurrences = {
        entry.tool_name: entry.count for entry in checkpoint.failure_tracker.occurrences
    }
    expected_occurrences = {
        tool_name: len(values) for tool_name, values in occurrence_lists.items()
    }
    if occurrences != expected_occurrences:
        raise DurableValidationError("failure tracker occurrences are inconsistent")
    if checkpoint.failure_tracker.injection_count != len(injections):
        raise DurableValidationError("failure injection counter is inconsistent")
    if expected_occurrences != {"read_text": 1, "write_text": 2} or len(injections) != 1:
        raise DurableValidationError("journal is not the fixed M3A checkpoint history")


def _finish_success(
    *,
    fixture: WorkspaceTaskFixture,
    run_directory: Path,
    journal: AppendOnlyJournal,
    environment: WorkspaceEnvironment,
    verifier: WorkspaceStateVerifier,
    initial: WorkspaceSnapshot,
    machine: RuntimeStateMachine,
    counters: _Counters,
    identity: ExperimentIdentity,
    config: RuntimeConfig,
    resumed: bool,
    checkpoint: Checkpoint | None,
    segment_start: float,
    clock: ClockProtocol,
) -> DurableResult:
    _transition(machine, RuntimeState.VERIFYING, journal, counters.steps)
    verifier_result = verifier.verify(
        initial, environment.snapshot(), fixture.private.expected
    )
    _require_deadline(counters, config, segment_start, clock)
    usage = counters.public(segment_elapsed=clock.monotonic() - segment_start)
    terminal = RuntimeState.SUCCEEDED if verifier_result.passed else RuntimeState.FAILED
    verifier_digest = verifier_summary_digest(verifier_result)
    summary = DurableResultSummary(
        run_id=journal.run_id,
        task_id=fixture.task.task_id,
        terminal_state=terminal,
        identity=identity,
        runtime_config=config,
        verifier=verifier_result,
        usage=usage,
        resumed=resumed,
        segment_count=journal.segment + 1,
        checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
        checkpoint_step=checkpoint.resume_step if checkpoint else None,
    )
    summary_digest = durable_result_summary_digest(summary)
    journal.append(
        "verification_finished",
        step_index=counters.steps,
        attempt=0,
        payload=DurableEventPayload(
            verifier_passed=verifier_result.passed,
            verifier_summary_digest=verifier_digest,
        ),
    )
    _require_deadline(counters, config, segment_start, clock)
    _transition(machine, terminal, journal, counters.steps)
    journal.append(
        "run_finished",
        step_index=counters.steps,
        attempt=0,
        payload=DurableEventPayload(
            terminal_state=terminal.value,
            verifier_passed=verifier_result.passed,
            verifier_summary_digest=verifier_digest,
            result_summary_digest=summary_digest,
        ),
    )
    result = DurableResult(
        run_id=journal.run_id,
        task_id=fixture.task.task_id,
        terminal_state=terminal,
        identity=identity,
        runtime_config=config,
        verifier=verifier_result,
        usage=usage,
        resumed=resumed,
        segment_count=journal.segment + 1,
        checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
        checkpoint_step=checkpoint.resume_step if checkpoint else None,
        journal_record_count=journal.record_count,
        journal_final_hash=journal.final_hash,
    )
    _write_result_once(run_directory / "result.json", result)
    return result


def _transition(
    machine: RuntimeStateMachine,
    target: RuntimeState,
    journal: AppendOnlyJournal,
    step_index: int,
) -> None:
    source, actual = machine.transition(target)
    journal.append(
        "runtime_state_changed",
        step_index=step_index,
        attempt=0,
        payload=DurableEventPayload(
            from_state=source.value,
            to_state=actual.value,
        ),
    )


def _require_decision_budget(
    counters: _Counters,
    config: RuntimeConfig,
    segment_start: float,
    clock: ClockProtocol,
) -> None:
    if counters.steps >= config.max_steps:
        raise DurableValidationError("remaining step budget is exhausted")
    _require_deadline(counters, config, segment_start, clock)


def _elapsed_seconds(
    counters: _Counters, segment_start: float, clock: ClockProtocol
) -> float:
    return counters.elapsed_seconds + clock.monotonic() - segment_start


def _require_deadline(
    counters: _Counters,
    config: RuntimeConfig,
    segment_start: float,
    clock: ClockProtocol,
) -> None:
    if _elapsed_seconds(counters, segment_start, clock) >= config.timeout_seconds:
        raise DurableValidationError("remaining elapsed-time budget is exhausted")


def _fingerprint(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_result_once(path: Path, result: DurableResult) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(result.model_dump_json(indent=2))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
