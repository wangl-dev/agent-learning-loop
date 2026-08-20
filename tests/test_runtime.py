from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from agent_learning_loop.clock import ClockProtocol
from agent_learning_loop.failure_schedules import (
    FailureSchedule,
    FailureScheduleMismatchError,
    load_failure_schedule,
)
from agent_learning_loop.policy import ScriptedPolicy
from agent_learning_loop.runtime import (
    InMemoryIdempotencyStore,
    execute_runtime_task,
    idempotency_key,
    is_retry_allowed,
    request_fingerprint,
)
from agent_learning_loop.runtime_schemas import (
    ErrorCategory,
    ErrorPhase,
    ErrorRecord,
    RuntimeConfig,
    RuntimeMode,
    RuntimeResult,
    RuntimeState,
    ToolInvocation,
)
from agent_learning_loop.schemas import (
    Action,
    Observation,
    Task,
    ToolResult,
    VerifierResult,
    WorkspaceExpectedState,
    WorkspaceSnapshot,
)
from agent_learning_loop.tasks import load_task
from agent_learning_loop.tool_metadata import TOOL_METADATA
from agent_learning_loop.verifier import WorkspaceStateVerifier


class FakeClock(ClockProtocol):
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def run_scenario(
    tmp_path: Path,
    *,
    task_id: str,
    schedule_id: str,
    mode: RuntimeMode,
    max_steps: int = 8,
    max_tool_calls: int = 12,
    timeout_seconds: float = 30.0,
    clock: ClockProtocol | None = None,
) -> tuple[RuntimeResult, Path]:
    schedule = load_failure_schedule(schedule_id)
    config = RuntimeConfig.for_mode(
        mode,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
        retry_backoff_seconds=[0.25],
    )
    run_directory = tmp_path / f"{task_id}-{mode.value}"
    result = execute_runtime_task(
        load_task(task_id),
        run_directory,
        run_id=f"run-{mode.value}",
        config=config,
        schedule=schedule,
        clock=clock or FakeClock(),
    )
    return result, run_directory


def test_transient_read_is_a_paired_naive_failure_and_safeguarded_recovery(
    tmp_path: Path,
) -> None:
    naive, _ = run_scenario(
        tmp_path,
        task_id="workspace.build-summary",
        schedule_id="workspace.transient-read.v1",
        mode=RuntimeMode.NAIVE,
    )
    safe, _ = run_scenario(
        tmp_path,
        task_id="workspace.build-summary",
        schedule_id="workspace.transient-read.v1",
        mode=RuntimeMode.SAFEGUARDED,
    )

    assert naive.terminal_state is RuntimeState.FAILED
    assert naive.error is not None
    assert naive.error.category is ErrorCategory.TOOL_TRANSIENT
    assert safe.terminal_state is RuntimeState.SUCCEEDED
    assert safe.error is None
    assert safe.usage.steps == 4
    assert safe.usage.tool_calls == 4
    assert safe.usage.retries == 1
    assert naive.schedule_fingerprint == safe.schedule_fingerprint


def test_logical_timeout_is_retryable_only_inside_attempt_and_deadline_budgets(
    tmp_path: Path,
) -> None:
    naive, _ = run_scenario(
        tmp_path,
        task_id="workspace.update-status",
        schedule_id="workspace.logical-timeout.v1",
        mode=RuntimeMode.NAIVE,
    )
    safe_clock = FakeClock()
    safe, _ = run_scenario(
        tmp_path,
        task_id="workspace.update-status",
        schedule_id="workspace.logical-timeout.v1",
        mode=RuntimeMode.SAFEGUARDED,
        clock=safe_clock,
    )
    exhausted_clock = FakeClock()
    exhausted, _ = run_scenario(
        tmp_path / "deadline-exhausted",
        task_id="workspace.update-status",
        schedule_id="workspace.logical-timeout.v1",
        mode=RuntimeMode.SAFEGUARDED,
        timeout_seconds=0.2,
        clock=exhausted_clock,
    )

    assert naive.terminal_state is RuntimeState.TIMED_OUT
    assert safe.terminal_state is RuntimeState.SUCCEEDED
    assert safe_clock.sleeps == [0.25]
    assert exhausted.terminal_state is RuntimeState.TIMED_OUT
    assert exhausted.usage.physical_executions == 1  # list_files precedes read_text
    assert exhausted_clock.sleeps == []
    assert exhausted.usage.backoff_seconds == 0.0


def test_deadline_is_rechecked_after_the_final_policy_decision(tmp_path: Path) -> None:
    clock = FakeClock()

    class FinalDecisionOverrunPolicy(ScriptedPolicy):
        def decide(self, task: Task, observation: Observation) -> Action | None:
            action = super().decide(task, observation)
            if action is None:
                clock.sleep(10.0)
            return action

    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
        timeout_seconds=1.0,
    )
    result = execute_runtime_task(
        load_task(schedule.task_id),
        tmp_path / "policy-deadline",
        run_id="policy-deadline",
        config=config,
        schedule=schedule,
        policy=FinalDecisionOverrunPolicy(),
        clock=clock,
    )

    assert result.terminal_state is RuntimeState.TIMED_OUT
    assert result.error is not None
    assert result.error.phase is ErrorPhase.DECIDING


def test_deadline_is_rechecked_after_verifier_returns(tmp_path: Path) -> None:
    clock = FakeClock()

    class VerifierOverrun(WorkspaceStateVerifier):
        def verify(
            self,
            initial: WorkspaceSnapshot,
            final: WorkspaceSnapshot,
            expected: WorkspaceExpectedState,
        ) -> VerifierResult:
            clock.sleep(2.0)
            return super().verify(initial, final, expected)

    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
        timeout_seconds=1.0,
    )
    result = execute_runtime_task(
        load_task(schedule.task_id),
        tmp_path / "verifier-deadline",
        run_id="verifier-deadline",
        config=config,
        schedule=schedule,
        verifier=VerifierOverrun(),
        clock=clock,
    )

    assert result.terminal_state is RuntimeState.TIMED_OUT
    assert result.error is not None
    assert result.error.phase is ErrorPhase.VERIFYING


def test_lost_write_result_exposes_and_then_suppresses_duplicate_side_effect(
    tmp_path: Path,
) -> None:
    naive, _ = run_scenario(
        tmp_path,
        task_id="workspace.fix-config",
        schedule_id="workspace.lost-write-result.v1",
        mode=RuntimeMode.NAIVE,
    )
    retry_only, _ = run_scenario(
        tmp_path,
        task_id="workspace.fix-config",
        schedule_id="workspace.lost-write-result.v1",
        mode=RuntimeMode.RETRY_ONLY,
    )
    safe, _ = run_scenario(
        tmp_path,
        task_id="workspace.fix-config",
        schedule_id="workspace.lost-write-result.v1",
        mode=RuntimeMode.SAFEGUARDED,
    )

    assert naive.terminal_state is RuntimeState.FAILED
    assert naive.verifier.passed is True
    assert retry_only.terminal_state is RuntimeState.SUCCEEDED
    assert retry_only.usage.side_effect_executions == 2
    assert retry_only.usage.duplicate_side_effects == 1
    assert safe.terminal_state is RuntimeState.SUCCEEDED
    assert safe.usage.side_effect_executions == 1
    assert safe.usage.duplicate_side_effects == 0
    assert safe.usage.idempotency_hits == 1


def test_tool_call_budget_refuses_retry_before_one_extra_attempt(tmp_path: Path) -> None:
    result, _ = run_scenario(
        tmp_path,
        task_id="workspace.build-summary",
        schedule_id="workspace.transient-read.v1",
        mode=RuntimeMode.SAFEGUARDED,
        max_tool_calls=1,
    )

    assert result.terminal_state is RuntimeState.BUDGET_EXHAUSTED
    assert result.error is not None
    assert result.error.category is ErrorCategory.BUDGET_EXHAUSTED
    assert result.usage.tool_calls == 1
    assert result.usage.physical_executions == 0


def test_step_budget_refuses_a_new_policy_decision(tmp_path: Path) -> None:
    result, _ = run_scenario(
        tmp_path,
        task_id="workspace.fix-config",
        schedule_id="workspace.lost-write-result.v1",
        mode=RuntimeMode.SAFEGUARDED,
        max_steps=1,
    )
    assert result.terminal_state is RuntimeState.BUDGET_EXHAUSTED
    assert result.usage.steps == 1


def test_same_idempotency_key_with_different_arguments_conflicts() -> None:
    first = Action(tool_name="write_text", arguments={"path": "a", "content": "one"})
    second = Action(tool_name="write_text", arguments={"path": "a", "content": "two"})
    store = InMemoryIdempotencyStore()
    first_invocation = ToolInvocation(
        action=first,
        idempotency_key="stable-key",
        request_fingerprint=request_fingerprint(first),
    )
    second_invocation = ToolInvocation(
        action=second,
        idempotency_key="stable-key",
        request_fingerprint=request_fingerprint(second),
    )
    store.save(first_invocation, ToolResult(status="ok", payload={"path": "a"}))

    lookup = store.lookup(second_invocation)
    assert lookup.conflict is True
    assert lookup.result is None


def test_schedule_seed_and_runtime_semantics_are_deterministic(tmp_path: Path) -> None:
    first, first_dir = run_scenario(
        tmp_path / "first",
        task_id="workspace.build-summary",
        schedule_id="workspace.transient-read.v1",
        mode=RuntimeMode.SAFEGUARDED,
    )
    second, second_dir = run_scenario(
        tmp_path / "second",
        task_id="workspace.build-summary",
        schedule_id="workspace.transient-read.v1",
        mode=RuntimeMode.SAFEGUARDED,
    )
    assert first.model_copy(update={"run_id": "same"}) == second.model_copy(
        update={"run_id": "same"}
    )
    first_events = (first_dir / "events.jsonl").read_text(encoding="utf-8").replace(
        "run-safeguarded", "same"
    )
    second_events = (second_dir / "events.jsonl").read_text(encoding="utf-8").replace(
        "run-safeguarded", "same"
    )
    assert first_events == second_events


def test_runtime_rejects_a_caller_fingerprint_that_disagrees_with_schedule(
    tmp_path: Path,
) -> None:
    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
        schedule_fingerprint="0" * 64,
    )

    with pytest.raises(FailureScheduleMismatchError):
        execute_runtime_task(
            load_task(schedule.task_id),
            tmp_path / "fingerprint-mismatch",
            run_id="fingerprint-mismatch",
            config=config,
            schedule=schedule,
            clock=FakeClock(),
        )
    assert not (tmp_path / "fingerprint-mismatch").exists()


def test_a_normally_completed_run_cannot_ignore_its_fixed_failure(tmp_path: Path) -> None:
    class EmptyPolicy:
        def decide(self, task: Task, observation: Observation) -> Action | None:
            return None

    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
    )
    result = execute_runtime_task(
        load_task(schedule.task_id),
        tmp_path / "schedule-not-injected",
        run_id="schedule-not-injected",
        config=config,
        schedule=schedule,
        policy=EmptyPolicy(),
        clock=FakeClock(),
    )

    assert result.terminal_state is RuntimeState.FAILED
    assert result.error is not None
    assert result.error.category is ErrorCategory.INTERNAL_ERROR
    assert result.error.detail == "fixed failure schedule did not inject exactly once"


def test_invalid_policy_shape_is_schema_validation_not_an_exception_leak(
    tmp_path: Path,
) -> None:
    class InvalidPolicy(ScriptedPolicy):
        def decide(self, task: Task, observation: Observation) -> Action | None:
            return cast(Action, {"tool_name": "read_text", "unexpected": True})

    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.NAIVE, schedule_id=schedule.schedule_id, seed=schedule.seed
    )
    result = execute_runtime_task(
        load_task("workspace.build-summary"),
        tmp_path / "invalid-policy",
        run_id="invalid-policy",
        config=config,
        schedule=schedule,
        policy=InvalidPolicy(),
        clock=FakeClock(),
    )
    assert result.error is not None
    assert result.error.category is ErrorCategory.SCHEMA_VALIDATION
    assert "Traceback" not in result.model_dump_json()
    assert "unexpected" not in result.model_dump_json()


def test_failure_schedule_type_is_publicly_constructible_for_boundary_tests() -> None:
    schedule = load_failure_schedule("workspace.transient-read.v1")
    assert isinstance(schedule, FailureSchedule)


def test_non_retryable_tool_argument_error_stops_after_one_attempt(tmp_path: Path) -> None:
    class InvalidArgumentsPolicy(ScriptedPolicy):
        def decide(self, task: Task, observation: Observation) -> Action | None:
            if observation.step_index == 0:
                return Action(tool_name="write_text", arguments={"wrong": "field"})
            return None

    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
    )
    result = execute_runtime_task(
        load_task(schedule.task_id),
        tmp_path / "permanent-error",
        run_id="permanent-error",
        config=config,
        schedule=schedule,
        policy=InvalidArgumentsPolicy(),
        clock=FakeClock(),
    )

    assert result.terminal_state is RuntimeState.FAILED
    assert result.error is not None
    assert result.error.category is ErrorCategory.TOOL_PERMANENT
    assert result.error.retryable is False
    assert result.usage.tool_calls == 1
    assert result.usage.retries == 0


def _read_events(run_directory: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (run_directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _assert_event_association(events: list[dict[str, Any]], result: RuntimeResult) -> None:
    non_attempt_kinds = {"run_started", "runtime_state_changed", "run_finished"}
    attempt_kinds = {
        "attempt_started",
        "attempt_completed",
        "failure_injected",
        "idempotency_hit",
        "idempotency_conflict",
    }
    assert all(
        event["attempt"] == 0
        for event in events
        if event["event_kind"] in non_attempt_kinds
    )
    assert all(
        event["attempt"] == event["payload"]["attempt"]
        for event in events
        if event["event_kind"] in attempt_kinds
    )
    for event in (event for event in events if event["event_kind"] == "retry_scheduled"):
        assert event["attempt"] == event["payload"]["failed_attempt"]
        assert event["payload"]["next_attempt"] == event["attempt"] + 1
    finished = [event for event in events if event["event_kind"] == "run_finished"]
    assert len(finished) == 1
    assert finished[0]["step_index"] == result.usage.steps


def test_transient_retry_events_have_consistent_step_and_attempt_association(
    tmp_path: Path,
) -> None:
    result, run_directory = run_scenario(
        tmp_path,
        task_id="workspace.build-summary",
        schedule_id="workspace.transient-read.v1",
        mode=RuntimeMode.SAFEGUARDED,
    )
    events = _read_events(run_directory)
    _assert_event_association(events, result)

    assert {
        "runtime_state_changed",
        "attempt_started",
        "attempt_completed",
        "failure_injected",
        "retry_scheduled",
        "run_finished",
    } <= {event["event_kind"] for event in events}
    attempts = [event for event in events if event["event_kind"] == "attempt_started"]
    assert [(event["step_index"], event["attempt"]) for event in attempts] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (3, 1),
    ]
    deciding_steps = [
        event["step_index"]
        for event in events
        if event["event_kind"] == "runtime_state_changed"
        and event["payload"]["to"] == "DECIDING"
    ]
    assert deciding_steps == [1, 2, 3, 4]


def test_lost_result_events_associate_retry_and_idempotency_hit(tmp_path: Path) -> None:
    result, run_directory = run_scenario(
        tmp_path,
        task_id="workspace.fix-config",
        schedule_id="workspace.lost-write-result.v1",
        mode=RuntimeMode.SAFEGUARDED,
    )
    events = _read_events(run_directory)
    _assert_event_association(events, result)

    hits = [event for event in events if event["event_kind"] == "idempotency_hit"]
    assert [(event["step_index"], event["attempt"]) for event in hits] == [(2, 2)]
    injected = [event for event in events if event["event_kind"] == "failure_injected"]
    assert len(injected) == 1


def test_tool_metadata_does_not_claim_an_unenforced_per_tool_timeout() -> None:
    assert all(not hasattr(metadata, "timeout_seconds") for metadata in TOOL_METADATA.values())


def test_private_expected_state_never_enters_runtime_v2_artifacts(tmp_path: Path) -> None:
    fixture = load_task("workspace.build-summary")
    payload = fixture.model_dump(mode="json")
    marker = "PRIVATE_EXPECTED_MARKER_M2"
    payload["private"]["expected"]["required_files"]["output/summary.txt"] = marker
    private_fixture = type(fixture).model_validate(payload)
    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
    )
    run_directory = tmp_path / "private-marker"
    execute_runtime_task(
        private_fixture,
        run_directory,
        run_id="private-marker",
        config=config,
        schedule=schedule,
        clock=FakeClock(),
    )

    public_text = (run_directory / "events.jsonl").read_text(encoding="utf-8")
    public_text += (run_directory / "result.json").read_text(encoding="utf-8")
    assert marker not in public_text


def test_policy_allowlist_rejection_has_a_stable_category(tmp_path: Path) -> None:
    fixture = load_task("workspace.fix-config")
    payload = fixture.model_dump(mode="json")
    payload["task"]["allowed_tools"] = ["read_text"]
    restricted_fixture = type(fixture).model_validate(payload)
    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.NAIVE, schedule_id=schedule.schedule_id, seed=schedule.seed
    )
    result = execute_runtime_task(
        restricted_fixture,
        tmp_path / "policy-rejection",
        run_id="policy-rejection",
        config=config,
        schedule=schedule,
        clock=FakeClock(),
    )
    assert result.terminal_state is RuntimeState.REJECTED
    assert result.error is not None
    assert result.error.category is ErrorCategory.POLICY_REJECTION
    assert result.usage.physical_executions == 1


def test_environment_reset_error_is_sanitized_and_cannot_escape(tmp_path: Path) -> None:
    fixture = load_task("workspace.build-summary")
    payload = fixture.model_dump(mode="json")
    payload["private"]["setup"]["files"] = {"../outside.txt": "secret"}
    unsafe_fixture = type(fixture).model_validate(payload)
    schedule = load_failure_schedule("workspace.transient-read.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.NAIVE, schedule_id=schedule.schedule_id, seed=schedule.seed
    )
    result = execute_runtime_task(
        unsafe_fixture,
        tmp_path / "environment-error",
        run_id="environment-error",
        config=config,
        schedule=schedule,
        clock=FakeClock(),
    )
    assert result.terminal_state is RuntimeState.FAILED
    assert result.error is not None
    assert result.error.category is ErrorCategory.ENVIRONMENT_ERROR
    assert not (tmp_path / "outside.txt").exists()
    assert "outside.txt" not in result.model_dump_json()


def test_runtime_idempotency_conflict_rejects_without_second_mutation(tmp_path: Path) -> None:
    write = Action(
        tool_name="write_text",
        arguments={"path": "app.conf", "content": "mode=production\nport=8080\n"},
    )
    conflicting = Action(
        tool_name="write_text",
        arguments={"path": "app.conf", "content": "different\n"},
    )
    key = idempotency_key("workspace.fix-config", 2, request_fingerprint(write))
    store = InMemoryIdempotencyStore()
    store.save(
        ToolInvocation(
            action=conflicting,
            idempotency_key=key,
            request_fingerprint=request_fingerprint(conflicting),
        ),
        ToolResult(status="ok", payload={"path": "app.conf"}),
    )
    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
    )
    result = execute_runtime_task(
        load_task("workspace.fix-config"),
        tmp_path / "idempotency-conflict",
        run_id="idempotency-conflict",
        config=config,
        schedule=schedule,
        clock=FakeClock(),
        idempotency_store=store,
    )
    assert result.terminal_state is RuntimeState.REJECTED
    assert result.error is not None
    assert result.error.category is ErrorCategory.IDEMPOTENCY_CONFLICT
    assert result.usage.side_effect_executions == 0
    events = _read_events(tmp_path / "idempotency-conflict")
    _assert_event_association(events, result)
    conflicts = [
        event for event in events if event["event_kind"] == "idempotency_conflict"
    ]
    assert [(event["step_index"], event["attempt"]) for event in conflicts] == [(2, 1)]
    assert (
        tmp_path / "idempotency-conflict" / "workspace" / "app.conf"
    ).read_text(encoding="utf-8") == "mode=debug\nport=8080\n"


def test_uncertain_side_effect_without_a_stable_key_cannot_retry() -> None:
    action = Action(
        tool_name="write_text",
        arguments={"path": "app.conf", "content": "new"},
    )
    invocation = ToolInvocation(
        action=action,
        idempotency_key=None,
        request_fingerprint=request_fingerprint(action),
    )
    error = ErrorRecord(
        category=ErrorCategory.TOOL_TRANSIENT,
        phase=ErrorPhase.EXECUTING_TOOL,
        retryable=True,
        step_index=1,
        attempt=1,
        tool_name="write_text",
        detail="controlled result uncertainty",
    )
    config = RuntimeConfig.for_mode(
        RuntimeMode.RETRY_ONLY, schedule_id="schedule", seed=1
    )

    assert not is_retry_allowed(
        error,
        TOOL_METADATA["write_text"],
        invocation,
        config=config,
        attempt=1,
    )
    assert not is_retry_allowed(
        error,
        TOOL_METADATA["write_text"],
        invocation.model_copy(update={"idempotency_key": "stable"}),
        config=config,
        attempt=2,
    )
