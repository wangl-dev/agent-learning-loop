"""Synchronous M2 Runtime with fixed faults, bounded retry, and run-local idempotency."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, ValidationError

from agent_learning_loop.clock import ClockProtocol, SystemClock
from agent_learning_loop.failure_schedules import (
    FailureSchedule,
    FailureScheduleMismatchError,
    fingerprint_schedule,
    validate_schedule_for_task,
)
from agent_learning_loop.policy import ScriptedPolicy
from agent_learning_loop.protocols import PolicyProtocol, ToolProtocol, VerifierProtocol
from agent_learning_loop.runtime_schemas import (
    BudgetUsage,
    ErrorCategory,
    ErrorPhase,
    ErrorRecord,
    IdempotencyLookup,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeResult,
    RuntimeState,
    ToolInvocation,
)
from agent_learning_loop.runtime_state import RuntimeStateMachine
from agent_learning_loop.schemas import (
    Action,
    ToolName,
    ToolResult,
    VerifierCheck,
    VerifierResult,
    WorkspaceSnapshot,
    WorkspaceTaskFixture,
)
from agent_learning_loop.tool_metadata import TOOL_METADATA, ToolMetadata
from agent_learning_loop.verifier import WorkspaceStateVerifier
from agent_learning_loop.vertical_slice import OutputExistsError
from agent_learning_loop.workspace import WorkspaceEnvironment, WorkspaceError
from agent_learning_loop.workspace_tools import ListFilesTool, ReadTextTool, WriteTextTool


@dataclass
class _Counters:
    steps: int = 0
    tool_calls: int = 0
    physical_executions: int = 0
    side_effect_executions: int = 0
    duplicate_side_effects: int = 0
    retries: int = 0
    idempotency_hits: int = 0
    backoff_seconds: float = 0.0

    def public(self, elapsed_seconds: float) -> BudgetUsage:
        return BudgetUsage(
            steps=self.steps,
            tool_calls=self.tool_calls,
            physical_executions=self.physical_executions,
            side_effect_executions=self.side_effect_executions,
            duplicate_side_effects=self.duplicate_side_effects,
            retries=self.retries,
            idempotency_hits=self.idempotency_hits,
            backoff_seconds=self.backoff_seconds,
            elapsed_seconds=max(elapsed_seconds, 0.0),
        )


class InMemoryIdempotencyStore:
    """Remember successful side-effect results for one process-local run."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, ToolResult]] = {}

    def save(self, invocation: ToolInvocation, result: ToolResult) -> None:
        if invocation.idempotency_key is None:
            raise ValueError("cannot save an invocation without an idempotency key")
        self._entries[invocation.idempotency_key] = (
            invocation.request_fingerprint,
            result,
        )

    def lookup(self, invocation: ToolInvocation) -> IdempotencyLookup:
        if invocation.idempotency_key is None:
            return IdempotencyLookup()
        entry = self._entries.get(invocation.idempotency_key)
        if entry is None:
            return IdempotencyLookup()
        saved_fingerprint, result = entry
        if saved_fingerprint != invocation.request_fingerprint:
            return IdempotencyLookup(conflict=True)
        return IdempotencyLookup(result=result)


def request_fingerprint(action: Action) -> str:
    canonical = json.dumps(
        action.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def idempotency_key(task_id: str, step_index: int, fingerprint: str) -> str:
    identity = f"{task_id}:{step_index}:{fingerprint}".encode()
    return hashlib.sha256(identity).hexdigest()


class _FailureTracker:
    def __init__(self, schedule: FailureSchedule) -> None:
        self.schedule = schedule
        self._occurrences: dict[str, int] = {}
        self.injection_count = 0

    def begin_attempt(self, tool_name: str) -> int:
        occurrence = self._occurrences.get(tool_name, 0) + 1
        self._occurrences[tool_name] = occurrence
        return occurrence

    def claim_injection(self, tool_name: str, occurrence: int, phase: str) -> bool:
        rule = self.schedule.rule
        matches = (
            rule.target_tool == tool_name
            and rule.occurrence == occurrence
            and rule.injection_phase == phase
        )
        if matches:
            self.injection_count += 1
        return matches


def execute_runtime_task(
    fixture: WorkspaceTaskFixture,
    run_directory: Path,
    *,
    run_id: str,
    config: RuntimeConfig,
    schedule: FailureSchedule,
    clock: ClockProtocol | None = None,
    policy: PolicyProtocol | None = None,
    verifier: VerifierProtocol | None = None,
    idempotency_store: InMemoryIdempotencyStore | None = None,
) -> RuntimeResult:
    """Run one fixture through the explicit M2 Runtime and write v2 artifacts."""
    if run_directory.exists() and any(run_directory.iterdir()):
        raise OutputExistsError(f"run directory is not empty: {run_directory}")
    validate_schedule_for_task(schedule, fixture.task.task_id)
    if config.schedule_id != schedule.schedule_id or config.seed != schedule.seed:
        raise FailureScheduleMismatchError("Runtime config does not match failure schedule")
    schedule_fingerprint = fingerprint_schedule(schedule)
    if (
        config.schedule_fingerprint is not None
        and config.schedule_fingerprint != schedule_fingerprint
    ):
        raise FailureScheduleMismatchError(
            "Runtime config fingerprint does not match failure schedule"
        )
    run_directory.mkdir(parents=True, exist_ok=True)

    runtime_clock = clock or SystemClock()
    runtime_policy = policy or ScriptedPolicy()
    environment = WorkspaceEnvironment(run_directory / "workspace")
    runtime_verifier = verifier or WorkspaceStateVerifier()
    tools: dict[str, ToolProtocol] = {
        "list_files": ListFilesTool(),
        "read_text": ReadTextTool(),
        "write_text": WriteTextTool(),
    }
    store = idempotency_store or InMemoryIdempotencyStore()
    tracker = _FailureTracker(schedule)
    machine = RuntimeStateMachine()
    counters = _Counters()
    events: list[RuntimeEvent] = []
    effective_config = RuntimeConfig.model_validate(
        {**config.model_dump(), "schedule_fingerprint": schedule_fingerprint}
    )
    start_time = runtime_clock.monotonic()
    last_attempt = 0

    def elapsed() -> float:
        return runtime_clock.monotonic() - start_time

    def emit(
        event_kind: RuntimeEventKind,
        payload: dict[str, JsonValue],
        attempt: int = 0,
    ) -> None:
        events.append(
            RuntimeEvent(
                run_id=run_id,
                task_id=fixture.task.task_id,
                sequence=len(events),
                step_index=counters.steps,
                attempt=attempt,
                event_kind=event_kind,
                payload=payload,
            )
        )

    def transition(target: RuntimeState) -> None:
        source, actual = machine.transition(target)
        emit(
            "runtime_state_changed",
            {"from": source.value, "to": actual.value},
        )

    emit(
        "run_started",
        {
            "mode": effective_config.mode.value,
            "schedule_id": schedule.schedule_id,
            "schedule_fingerprint": schedule_fingerprint,
            "seed": schedule.seed,
        },
    )

    error: ErrorRecord | None = None
    verifier_result = _execution_failure_verifier()
    initial: WorkspaceSnapshot | None = None
    observation = None
    verification_attempted = False
    try:
        transition(RuntimeState.RESETTING)
        try:
            observation = environment.reset(
                fixture.private.setup, task_id=fixture.task.task_id
            )
            initial = environment.snapshot()
        except (WorkspaceError, OSError, UnicodeError):
            error = _error(
                ErrorCategory.ENVIRONMENT_ERROR,
                ErrorPhase.RESETTING,
                False,
                counters,
                0,
                None,
                "Workspace reset was rejected",
            )
        if error is None:
            transition(RuntimeState.READY)

        while error is None and observation is not None:
            if elapsed() >= effective_config.timeout_seconds:
                error = _error(
                    ErrorCategory.TIMEOUT,
                    ErrorPhase.DECIDING,
                    False,
                    counters,
                    0,
                    None,
                    "Runtime deadline was exhausted before a new decision",
                )
                break
            if counters.steps >= effective_config.max_steps:
                error = _error(
                    ErrorCategory.BUDGET_EXHAUSTED,
                    ErrorPhase.DECIDING,
                    False,
                    counters,
                    0,
                    None,
                    "step budget was exhausted before a new decision",
                )
                break
            counters.steps += 1
            transition(RuntimeState.DECIDING)
            try:
                raw_action = runtime_policy.decide(fixture.task, observation)
            except Exception:  # policy implementations are an untrusted boundary
                error = _error(
                    ErrorCategory.INTERNAL_ERROR,
                    ErrorPhase.DECIDING,
                    False,
                    counters,
                    0,
                    None,
                    "Policy decision failed inside the Runtime boundary",
                )
                break
            if elapsed() >= effective_config.timeout_seconds:
                error = _error(
                    ErrorCategory.TIMEOUT,
                    ErrorPhase.DECIDING,
                    False,
                    counters,
                    0,
                    None,
                    "Runtime deadline was exhausted during policy decision",
                )
                break
            if raw_action is None:
                if tracker.injection_count != 1:
                    error = _error(
                        ErrorCategory.INTERNAL_ERROR,
                        ErrorPhase.INTERNAL,
                        False,
                        counters,
                        0,
                        None,
                        "fixed failure schedule did not inject exactly once",
                    )
                    break
                transition(RuntimeState.VERIFYING)
                try:
                    assert initial is not None
                    final = environment.snapshot()
                    verification_attempted = True
                    verifier_result = runtime_verifier.verify(
                        initial, final, fixture.private.expected
                    )
                except (WorkspaceError, OSError, UnicodeError):
                    error = _error(
                        ErrorCategory.ENVIRONMENT_ERROR,
                        ErrorPhase.VERIFYING,
                        False,
                        counters,
                        0,
                        None,
                        "Workspace state could not be verified",
                    )
                if error is None and elapsed() >= effective_config.timeout_seconds:
                    error = _error(
                        ErrorCategory.TIMEOUT,
                        ErrorPhase.VERIFYING,
                        False,
                        counters,
                        0,
                        None,
                        "Runtime deadline was exhausted during verification",
                    )
                break

            transition(RuntimeState.VALIDATING_ACTION)
            try:
                action = Action.model_validate(raw_action)
            except ValidationError:
                error = _error(
                    ErrorCategory.SCHEMA_VALIDATION,
                    ErrorPhase.VALIDATING_ACTION,
                    False,
                    counters,
                    0,
                    None,
                    "Policy returned an invalid structured action",
                )
                break
            if action.tool_name not in fixture.task.allowed_tools:
                error = _error(
                    ErrorCategory.POLICY_REJECTION,
                    ErrorPhase.VALIDATING_ACTION,
                    False,
                    counters,
                    0,
                    action.tool_name,
                    "Policy selected a tool outside the task allowlist",
                )
                break

            transition(RuntimeState.EXECUTING_TOOL)
            metadata = TOOL_METADATA[action.tool_name]
            invocation_fingerprint = request_fingerprint(action)
            invocation = ToolInvocation(
                action=action,
                idempotency_key=(
                    idempotency_key(
                        fixture.task.task_id, counters.steps, invocation_fingerprint
                    )
                    if metadata.side_effecting
                    else None
                ),
                request_fingerprint=invocation_fingerprint,
            )
            tool_result, error, last_attempt = _execute_tool_attempts(
                environment=environment,
                tool=tools[action.tool_name],
                metadata=metadata,
                invocation=invocation,
                config=effective_config,
                schedule=schedule,
                tracker=tracker,
                store=store,
                counters=counters,
                clock=runtime_clock,
                elapsed=elapsed,
                emit=emit,
            )
            if error is not None:
                break
            assert tool_result is not None
            transition(RuntimeState.OBSERVING)
            try:
                observation = environment.observe(
                    task_id=fixture.task.task_id,
                    step_index=counters.steps,
                    last_tool_result=tool_result,
                )
            except (WorkspaceError, OSError, UnicodeError):
                error = _error(
                    ErrorCategory.ENVIRONMENT_ERROR,
                    ErrorPhase.OBSERVING,
                    False,
                    counters,
                    last_attempt,
                    action.tool_name,
                    "Workspace observation failed",
                )
            if error is None and elapsed() >= effective_config.timeout_seconds:
                error = _error(
                    ErrorCategory.TIMEOUT,
                    ErrorPhase.OBSERVING,
                    False,
                    counters,
                    last_attempt,
                    action.tool_name,
                    "Runtime deadline was exhausted during observation",
                )

        if error is None and machine.state is RuntimeState.VERIFYING:
            if elapsed() >= effective_config.timeout_seconds:
                error = _error(
                    ErrorCategory.TIMEOUT,
                    ErrorPhase.VERIFYING,
                    False,
                    counters,
                    0,
                    None,
                    "Runtime deadline was exhausted before terminal success",
                )
                transition(RuntimeState.TIMED_OUT)
            else:
                transition(
                    RuntimeState.SUCCEEDED
                    if verifier_result.passed
                    else RuntimeState.FAILED
                )
        elif error is not None:
            if initial is not None and not verification_attempted:
                try:
                    verifier_result = runtime_verifier.verify(
                        initial,
                        environment.snapshot(),
                        fixture.private.expected,
                    )
                except (WorkspaceError, OSError, UnicodeError):
                    verifier_result = _execution_failure_verifier()
            transition(_terminal_for_error(error))
    except Exception:
        if machine.state not in {
            RuntimeState.SUCCEEDED,
            RuntimeState.FAILED,
            RuntimeState.REJECTED,
            RuntimeState.BUDGET_EXHAUSTED,
            RuntimeState.TIMED_OUT,
        }:
            error = _error(
                ErrorCategory.INTERNAL_ERROR,
                ErrorPhase.INTERNAL,
                False,
                counters,
                last_attempt,
                None,
                "Unexpected Runtime boundary failure",
            )
            transition(RuntimeState.FAILED)
    finally:
        environment.close()

    emit(
        "run_finished",
        {
            "terminal_state": machine.state.value,
            "verifier_passed": verifier_result.passed,
        },
    )
    result = RuntimeResult(
        run_id=run_id,
        task_id=fixture.task.task_id,
        terminal_state=machine.state,
        config=effective_config,
        error=error,
        verifier=verifier_result,
        usage=counters.public(elapsed()),
        schedule_id=schedule.schedule_id,
        schedule_fingerprint=schedule_fingerprint,
    )
    _write_outputs(run_directory, events, result)
    return result


def _execute_tool_attempts(
    *,
    environment: WorkspaceEnvironment,
    tool: ToolProtocol,
    metadata: ToolMetadata,
    invocation: ToolInvocation,
    config: RuntimeConfig,
    schedule: FailureSchedule,
    tracker: _FailureTracker,
    store: InMemoryIdempotencyStore,
    counters: _Counters,
    clock: ClockProtocol,
    elapsed: Callable[[], float],
    emit: Callable[[RuntimeEventKind, dict[str, JsonValue], int], None],
) -> tuple[ToolResult | None, ErrorRecord | None, int]:
    successful_side_effects = 0
    for attempt in range(1, config.max_attempts + 1):
        if elapsed() >= config.timeout_seconds:
            return None, _error(
                ErrorCategory.TIMEOUT,
                ErrorPhase.EXECUTING_TOOL,
                False,
                counters,
                attempt,
                metadata.name,
                "Runtime deadline was exhausted before tool execution",
            ), attempt
        if counters.tool_calls >= config.max_tool_calls:
            return None, _error(
                ErrorCategory.BUDGET_EXHAUSTED,
                ErrorPhase.EXECUTING_TOOL,
                False,
                counters,
                attempt,
                metadata.name,
                "tool-call budget was exhausted before execution",
            ), attempt

        counters.tool_calls += 1
        occurrence = tracker.begin_attempt(metadata.name)
        emit(
            "attempt_started",
            {"tool_name": metadata.name, "attempt": attempt, "occurrence": occurrence},
            attempt,
        )

        if config.idempotency_enabled and metadata.side_effecting:
            lookup = store.lookup(invocation)
            if lookup.conflict:
                emit(
                    "idempotency_conflict",
                    {"tool_name": metadata.name, "attempt": attempt},
                    attempt,
                )
                return None, _error(
                    ErrorCategory.IDEMPOTENCY_CONFLICT,
                    ErrorPhase.EXECUTING_TOOL,
                    False,
                    counters,
                    attempt,
                    metadata.name,
                    "idempotency key was reused with different arguments",
                ), attempt
            if lookup.result is not None:
                counters.idempotency_hits += 1
                emit(
                    "idempotency_hit",
                    {"tool_name": metadata.name, "attempt": attempt},
                    attempt,
                )
                emit(
                    "attempt_completed",
                    {"tool_name": metadata.name, "attempt": attempt, "status": "ok"},
                    attempt,
                )
                return lookup.result, None, attempt

        injected_error: ErrorRecord | None = None
        if tracker.claim_injection(metadata.name, occurrence, "before_execution"):
            injected_error = _injected_error(schedule, counters, attempt)
            emit(
                "failure_injected",
                {
                    "tool_name": metadata.name,
                    "attempt": attempt,
                    "failure_kind": schedule.rule.failure_kind,
                    "injection_phase": "before_execution",
                    "error_category": schedule.rule.error_category.value,
                    "retryable": schedule.rule.retryable,
                },
                attempt,
            )
        else:
            counters.physical_executions += 1
            result = tool.execute(environment, invocation.action)
            tool_boundary_timed_out = elapsed() >= config.timeout_seconds
            if result.status == "error":
                injected_error = _error(
                    ErrorCategory.TOOL_PERMANENT,
                    ErrorPhase.EXECUTING_TOOL,
                    False,
                    counters,
                    attempt,
                    metadata.name,
                    "Validated Workspace tool rejected the action",
                )
            else:
                if metadata.side_effecting:
                    counters.side_effect_executions += 1
                    successful_side_effects += 1
                    if successful_side_effects > 1:
                        counters.duplicate_side_effects += 1
                    if config.idempotency_enabled:
                        store.save(invocation, result)
            if tool_boundary_timed_out:
                emit(
                    "attempt_completed",
                    {"tool_name": metadata.name, "attempt": attempt, "status": "error"},
                    attempt,
                )
                return None, _error(
                    ErrorCategory.TIMEOUT,
                    ErrorPhase.EXECUTING_TOOL,
                    False,
                    counters,
                    attempt,
                    metadata.name,
                    "Runtime deadline was exhausted during tool execution",
                ), attempt
            if result.status == "ok":
                if tracker.claim_injection(metadata.name, occurrence, "after_success"):
                    injected_error = _injected_error(schedule, counters, attempt)
                    emit(
                        "failure_injected",
                        {
                            "tool_name": metadata.name,
                            "attempt": attempt,
                            "failure_kind": schedule.rule.failure_kind,
                            "injection_phase": "after_success",
                            "error_category": schedule.rule.error_category.value,
                            "retryable": schedule.rule.retryable,
                        },
                        attempt,
                    )
                else:
                    emit(
                        "attempt_completed",
                        {"tool_name": metadata.name, "attempt": attempt, "status": "ok"},
                        attempt,
                    )
                    return result, None, attempt

        assert injected_error is not None
        emit(
            "attempt_completed",
            {"tool_name": metadata.name, "attempt": attempt, "status": "error"},
            attempt,
        )
        if not is_retry_allowed(
            injected_error, metadata, invocation, config=config, attempt=attempt
        ):
            return None, injected_error, attempt
        if counters.tool_calls >= config.max_tool_calls:
            return None, _error(
                ErrorCategory.BUDGET_EXHAUSTED,
                ErrorPhase.EXECUTING_TOOL,
                False,
                counters,
                attempt,
                metadata.name,
                "tool-call budget was exhausted before retry",
            ), attempt

        delay = config.retry_backoff_seconds[attempt - 1]
        if delay >= config.timeout_seconds - elapsed():
            return None, _error(
                ErrorCategory.TIMEOUT,
                ErrorPhase.EXECUTING_TOOL,
                False,
                counters,
                attempt,
                metadata.name,
                "Runtime deadline would be exhausted by retry backoff",
            ), attempt
        counters.retries += 1
        counters.backoff_seconds += delay
        emit(
            "retry_scheduled",
            {
                "tool_name": metadata.name,
                "failed_attempt": attempt,
                "next_attempt": attempt + 1,
                "backoff": delay,
            },
            attempt,
        )
        clock.sleep(delay)
        if elapsed() >= config.timeout_seconds:
            return None, _error(
                ErrorCategory.TIMEOUT,
                ErrorPhase.EXECUTING_TOOL,
                False,
                counters,
                attempt,
                metadata.name,
                "Runtime deadline was exhausted during retry backoff",
            ), attempt
    return None, _error(
        ErrorCategory.INTERNAL_ERROR,
        ErrorPhase.INTERNAL,
        False,
        counters,
        config.max_attempts,
        metadata.name,
        "retry loop ended without a terminal attempt result",
    ), config.max_attempts


def is_retry_allowed(
    error: ErrorRecord,
    metadata: ToolMetadata,
    invocation: ToolInvocation,
    *,
    config: RuntimeConfig,
    attempt: int,
) -> bool:
    if not config.retry_enabled or attempt >= config.max_attempts:
        return False
    if not error.retryable or error.category not in metadata.retryable_categories:
        return False
    if metadata.side_effecting and invocation.idempotency_key is None:
        return False
    return True


def _injected_error(
    schedule: FailureSchedule, counters: _Counters, attempt: int
) -> ErrorRecord:
    detail = {
        "transient": "controlled transient tool failure",
        "logical_timeout": "controlled logical tool timeout",
        "result_lost": "controlled tool result loss after successful execution",
    }[schedule.rule.failure_kind]
    return _error(
        schedule.rule.error_category,
        ErrorPhase.EXECUTING_TOOL,
        schedule.rule.retryable,
        counters,
        attempt,
        schedule.rule.target_tool,
        detail,
    )


def _error(
    category: ErrorCategory,
    phase: ErrorPhase,
    retryable: bool,
    counters: _Counters,
    attempt: int,
    tool_name: ToolName | None,
    detail: str,
) -> ErrorRecord:
    return ErrorRecord(
        category=category,
        phase=phase,
        retryable=retryable,
        step_index=counters.steps,
        attempt=attempt,
        tool_name=tool_name,
        detail=detail,
    )


def _terminal_for_error(error: ErrorRecord) -> RuntimeState:
    if error.category is ErrorCategory.TIMEOUT:
        return RuntimeState.TIMED_OUT
    if error.category is ErrorCategory.BUDGET_EXHAUSTED:
        return RuntimeState.BUDGET_EXHAUSTED
    if error.category in {
        ErrorCategory.POLICY_REJECTION,
        ErrorCategory.IDEMPOTENCY_CONFLICT,
    }:
        return RuntimeState.REJECTED
    return RuntimeState.FAILED


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


def _write_outputs(
    run_directory: Path, events: list[RuntimeEvent], result: RuntimeResult
) -> None:
    event_text = "".join(f"{event.model_dump_json()}\n" for event in events)
    (run_directory / result.events_file).write_text(
        event_text, encoding="utf-8", newline="\n"
    )
    (run_directory / "result.json").write_text(
        result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
