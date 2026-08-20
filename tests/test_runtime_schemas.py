from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_learning_loop.artifacts import read_event_artifact, read_result_artifact
from agent_learning_loop.runtime_schemas import (
    BudgetUsage,
    ErrorCategory,
    ErrorPhase,
    ErrorRecord,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeMode,
    RuntimeResult,
    RuntimeState,
)
from agent_learning_loop.schemas import Event, RunResult, VerifierCheck, VerifierResult


def test_runtime_modes_freeze_the_real_safeguard_switches() -> None:
    naive = RuntimeConfig.for_mode(RuntimeMode.NAIVE, schedule_id="schedule", seed=1)
    retry_only = RuntimeConfig.for_mode(
        RuntimeMode.RETRY_ONLY, schedule_id="schedule", seed=1
    )
    safeguarded = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED, schedule_id="schedule", seed=1
    )

    assert (naive.retry_enabled, naive.idempotency_enabled) == (False, False)
    assert (retry_only.retry_enabled, retry_only.idempotency_enabled) == (True, False)
    assert (safeguarded.retry_enabled, safeguarded.idempotency_enabled) == (True, True)
    assert naive.max_attempts == 1
    assert retry_only.max_attempts == safeguarded.max_attempts == 2


def test_error_taxonomy_has_nine_stable_machine_categories() -> None:
    assert {category.value for category in ErrorCategory} == {
        "schema_validation",
        "policy_rejection",
        "tool_transient",
        "tool_permanent",
        "environment_error",
        "timeout",
        "budget_exhausted",
        "idempotency_conflict",
        "internal_error",
    }


def test_runtime_config_rejects_a_mode_label_that_lies_about_switches() -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(
            mode=RuntimeMode.NAIVE,
            max_steps=5,
            max_tool_calls=5,
            timeout_seconds=5.0,
            retry_enabled=True,
            max_attempts=2,
            retry_backoff_seconds=[0.0],
            idempotency_enabled=False,
            schedule_id="schedule",
            seed=1,
        )


def test_runtime_v2_result_round_trip_is_strict() -> None:
    verifier = VerifierResult(
        passed=False,
        score=0.0,
        checks=[VerifierCheck(name="state", passed=False, detail="state differed")],
    )
    result = RuntimeResult(
        run_id="run-1",
        task_id="workspace.build-summary",
        terminal_state=RuntimeState.FAILED,
        config=RuntimeConfig.for_mode(
            RuntimeMode.NAIVE, schedule_id="workspace.transient-read.v1", seed=101
        ),
        error=ErrorRecord(
            category=ErrorCategory.TOOL_TRANSIENT,
            phase=ErrorPhase.EXECUTING_TOOL,
            retryable=True,
            step_index=1,
            attempt=1,
            tool_name="read_text",
            detail="controlled transient failure",
        ),
        verifier=verifier,
        usage=BudgetUsage(steps=1, tool_calls=1),
        schedule_id="workspace.transient-read.v1",
        schedule_fingerprint="a" * 64,
    )

    restored = RuntimeResult.model_validate_json(result.model_dump_json())
    assert restored == result
    payload = json.loads(result.model_dump_json())
    payload["unknown"] = True
    with pytest.raises(ValidationError):
        RuntimeResult.model_validate(payload)


def test_artifact_reader_routes_m1_v1_and_runtime_v2(tmp_path: Path) -> None:
    verifier = VerifierResult(
        passed=True,
        score=1.0,
        checks=[VerifierCheck(name="state", passed=True, detail="state matched")],
    )
    v1_result = RunResult(
        run_id="m1-run",
        task_id="workspace.fix-config",
        outcome="passed",
        verifier=verifier,
    )
    v1_event = Event(
        run_id="m1-run",
        task_id="workspace.fix-config",
        step_index=0,
        event_kind="task_started",
        payload={},
    )
    result_path = tmp_path / "result.json"
    event_path = tmp_path / "event.json"
    result_path.write_text(v1_result.model_dump_json(), encoding="utf-8")
    event_path.write_text(v1_event.model_dump_json(), encoding="utf-8")

    assert read_result_artifact(result_path) == v1_result
    assert read_event_artifact(event_path) == v1_event

    v2_event = RuntimeEvent(
        run_id="m2-run",
        task_id="workspace.fix-config",
        sequence=0,
        step_index=0,
        event_kind="run_started",
        payload={},
    )
    event_path.write_text(v2_event.model_dump_json(), encoding="utf-8")
    assert read_event_artifact(event_path) == v2_event


def test_public_error_record_rejects_traceback_shaped_details() -> None:
    with pytest.raises(ValidationError):
        ErrorRecord(
            category=ErrorCategory.INTERNAL_ERROR,
            phase=ErrorPhase.INTERNAL,
            retryable=False,
            step_index=0,
            attempt=0,
            detail="Traceback (most recent call last): secret",
        )
