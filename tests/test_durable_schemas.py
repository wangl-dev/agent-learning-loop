from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from agent_learning_loop.durable_schemas import (
    Checkpoint,
    CheckpointingMode,
    DurableEvent,
    DurableEventPayload,
    DurableResult,
    DurableUsage,
    ExperimentIdentity,
    FailureTrackerState,
    IdempotencyEntry,
    ToolOccurrence,
)
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode, RuntimeState
from agent_learning_loop.schemas import VerifierCheck, VerifierResult

HASH = "a" * 64
OTHER_HASH = "b" * 64


def runtime_config() -> RuntimeConfig:
    return RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id="workspace.lost-write-result.v1",
        schedule_fingerprint=HASH,
        seed=303,
    )


def identity() -> ExperimentIdentity:
    return ExperimentIdentity(
        task_id="workspace.fix-config",
        fixture_id="workspace.fix-config.v1",
        fixture_fingerprint=HASH,
        config_fingerprint=OTHER_HASH,
        failure_schedule_id="workspace.lost-write-result.v1",
        failure_schedule_fingerprint=HASH,
        interruption_schedule_id="workspace.post-write-boundary.v1",
        interruption_schedule_fingerprint=OTHER_HASH,
        checkpointing=CheckpointingMode.ON,
        seed=303,
    )


def usage() -> DurableUsage:
    return DurableUsage(
        steps=2,
        tool_calls=3,
        physical_executions=2,
        physical_write_executions=1,
        side_effect_executions=1,
        duplicate_side_effects=0,
        retries=1,
        idempotency_hits=1,
        backoff_seconds=0.0,
        elapsed_seconds=0.25,
    )


def verifier() -> VerifierResult:
    return VerifierResult(
        passed=True,
        score=1.0,
        checks=[VerifierCheck(name="state", passed=True, detail="state matched")],
    )


def checkpoint() -> Checkpoint:
    saved = Checkpoint(
        checkpoint_id="0" * 64,
        run_id="run-1",
        identity=identity(),
        runtime_config=runtime_config(),
        resume_step=2,
        runtime_state=RuntimeState.OBSERVING,
        resume_target=RuntimeState.DECIDING,
        usage=usage(),
        failure_tracker=FailureTrackerState(
            occurrences=[
                ToolOccurrence(tool_name="read_text", count=1),
                ToolOccurrence(tool_name="write_text", count=2),
            ],
            injection_count=1,
        ),
        idempotency_entries=[
            IdempotencyEntry(
                key=HASH,
                request_fingerprint=OTHER_HASH,
                tool_name="write_text",
                result_path="app.conf",
                bytes_written=26,
            )
        ],
        workspace_digest=HASH,
        journal_record_count=17,
        journal_final_hash=OTHER_HASH,
        segment=0,
    )
    from agent_learning_loop.checkpoint import checkpoint_id

    return saved.model_copy(update={"checkpoint_id": checkpoint_id(saved)})


def test_v3_event_checkpoint_and_result_round_trip_strictly() -> None:
    event = DurableEvent(
        run_id="run-1",
        task_id="workspace.fix-config",
        sequence=0,
        step_index=0,
        attempt=0,
        segment=0,
        event_kind="run_started",
        payload=DurableEventPayload(
            mode="safeguarded",
            checkpointing="on",
            fixture_id="workspace.fix-config.v1",
            config_fingerprint=HASH,
            failure_schedule_id="workspace.lost-write-result.v1",
            failure_schedule_fingerprint=HASH,
            interruption_schedule_id="workspace.post-write-boundary.v1",
            interruption_schedule_fingerprint=OTHER_HASH,
            seed=303,
        ),
        previous_record_hash="",
        record_hash=HASH,
    )
    restored_event = DurableEvent.model_validate_json(event.model_dump_json())
    assert restored_event == event

    saved = checkpoint()
    restored_checkpoint = Checkpoint.model_validate_json(saved.model_dump_json())
    assert restored_checkpoint == saved

    result = DurableResult(
        run_id="run-1",
        task_id="workspace.fix-config",
        terminal_state=RuntimeState.SUCCEEDED,
        identity=identity(),
        runtime_config=runtime_config(),
        verifier=verifier(),
        usage=usage(),
        resumed=True,
        segment_count=2,
        checkpoint_id=HASH,
        checkpoint_step=2,
        journal_record_count=23,
        journal_final_hash=OTHER_HASH,
    )
    restored_result = DurableResult.model_validate_json(result.model_dump_json())
    assert restored_result == result
    assert restored_result.action_replay_match_rate == "N/A"


@pytest.mark.parametrize("model", ["event", "checkpoint", "result"])
def test_v3_contracts_reject_unknown_fields(model: str) -> None:
    if model == "event":
        payload = DurableEvent(
            run_id="run-1",
            task_id="workspace.fix-config",
            sequence=0,
            step_index=0,
            attempt=0,
            segment=0,
            event_kind="run_started",
            payload=DurableEventPayload(mode="safeguarded"),
            previous_record_hash="",
            record_hash=HASH,
        ).model_dump(mode="json")
    elif model == "checkpoint":
        payload = checkpoint().model_dump(mode="json")
    else:
        payload = DurableResult(
            run_id="run-1",
            task_id="workspace.fix-config",
            terminal_state=RuntimeState.SUCCEEDED,
            identity=identity(),
            runtime_config=runtime_config(),
            verifier=verifier(),
            usage=usage(),
            resumed=True,
            segment_count=2,
            checkpoint_id=HASH,
            checkpoint_step=2,
            journal_record_count=23,
            journal_final_hash=OTHER_HASH,
        ).model_dump(mode="json")
    changed = deepcopy(payload)
    changed["unknown"] = "rejected"

    with pytest.raises(ValidationError):
        if model == "event":
            DurableEvent.model_validate(changed)
        elif model == "checkpoint":
            Checkpoint.model_validate(changed)
        else:
            DurableResult.model_validate(changed)


def test_event_payload_is_an_allowlist_not_an_arbitrary_dictionary() -> None:
    with pytest.raises(ValidationError):
        DurableEventPayload.model_validate(
            {"tool_name": "read_text", "raw_file_content": "ghp_secret"}
        )
