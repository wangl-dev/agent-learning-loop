from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_learning_loop.durable_runtime import (
    ControlledInterruption,
    execute_durable_task,
    resume_durable_task,
)
from agent_learning_loop.durable_schemas import CheckpointingMode
from agent_learning_loop.event_replay import TrajectoryValidationError, validate_trajectory
from agent_learning_loop.failure_schedules import load_failure_schedule
from agent_learning_loop.interruption_schedules import load_interruption_schedule
from agent_learning_loop.journal import canonical_json_bytes, record_hash
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode
from agent_learning_loop.tasks import load_task


def make_partial(run_directory: Path, *, run_id: str = "replay-run") -> None:
    failure = load_failure_schedule("workspace.lost-write-result.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=failure.schedule_id,
        seed=failure.seed,
    )
    with pytest.raises(ControlledInterruption):
        execute_durable_task(
            load_task("workspace.fix-config"),
            run_directory,
            run_id=run_id,
            config=config,
            failure_schedule=failure,
            checkpointing=CheckpointingMode.ON,
            interruption_schedule=load_interruption_schedule(
                "workspace.post-write-boundary.v1"
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "swapped", "payload", "duplicate_sequence", "bad_hash", "partial_line"],
)
def test_event_replay_rejects_missing_modified_reordered_or_partial_journal(
    tmp_path: Path, mutation: str
) -> None:
    run_directory = tmp_path / mutation
    make_partial(run_directory)
    path = run_directory / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    if mutation == "missing":
        del lines[2]
    elif mutation == "swapped":
        lines[2], lines[3] = lines[3], lines[2]
    elif mutation == "payload":
        payload = json.loads(lines[1])
        payload["payload"]["to_state"] = "READY"
        lines[1] = json.dumps(payload, separators=(",", ":"))
    elif mutation == "duplicate_sequence":
        payload = json.loads(lines[2])
        payload["sequence"] = 1
        lines[2] = json.dumps(payload, separators=(",", ":"))
    elif mutation == "bad_hash":
        payload = json.loads(lines[-1])
        payload["record_hash"] = "0" * 64
        lines[-1] = json.dumps(payload, separators=(",", ":"))
    else:
        path.write_bytes(path.read_bytes()[:-3])
        lines = []
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)


def test_event_replay_is_read_only_and_never_constructs_execution_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "read-only"
    make_partial(run_directory)

    def fail_component(*args: object, **kwargs: object) -> None:
        raise AssertionError("event replay constructed an execution component")

    monkeypatch.setattr("agent_learning_loop.workspace.WorkspaceEnvironment", fail_component)
    monkeypatch.setattr("agent_learning_loop.policy.ScriptedPolicy", fail_component)
    monkeypatch.setattr("agent_learning_loop.verifier.WorkspaceStateVerifier", fail_component)
    monkeypatch.setattr("agent_learning_loop.workspace_tools.WriteTextTool", fail_component)

    validation = validate_trajectory(run_directory)

    assert validation.status == "valid_partial"
    assert validation.action_replay_match_rate == "N/A"


def test_event_replay_rejects_checkpoint_from_another_run(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    make_partial(first, run_id="first-run")
    make_partial(second, run_id="second-run")
    (first / "checkpoint.json").write_bytes((second / "checkpoint.json").read_bytes())

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(first)


def test_event_replay_rejects_result_with_wrong_terminal(tmp_path: Path) -> None:
    run_directory = tmp_path / "completed"
    failure = load_failure_schedule("workspace.lost-write-result.v1")
    config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=failure.schedule_id,
        seed=failure.seed,
    )
    execute_durable_task(
        load_task("workspace.fix-config"),
        run_directory,
        run_id="completed-run",
        config=config,
        failure_schedule=failure,
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
    )
    path = run_directory / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["terminal_state"] = "FAILED"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)


@pytest.mark.parametrize("mutation", ["usage", "verifier"])
def test_event_replay_rejects_completed_result_summary_tamper(
    tmp_path: Path, mutation: str
) -> None:
    run_directory = tmp_path / mutation
    failure = load_failure_schedule("workspace.lost-write-result.v1")
    execute_durable_task(
        load_task("workspace.fix-config"),
        run_directory,
        run_id="summary-run",
        config=RuntimeConfig.for_mode(
            RuntimeMode.SAFEGUARDED,
            schedule_id=failure.schedule_id,
            seed=failure.seed,
        ),
        failure_schedule=failure,
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
    )
    result_path = run_directory / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if mutation == "usage":
        result["usage"]["physical_write_executions"] = 999
        result["usage"]["side_effect_executions"] = 999
        result["usage"]["duplicate_side_effects"] = 998
    else:
        result["verifier"]["score"] = 0.25
        result["verifier"]["checks"][0]["detail"] = "tampered summary"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)


@pytest.mark.parametrize(
    "mutation", ["same_segment", "resume_payload", "resume_order"]
)
def test_event_replay_rejects_invalid_resumed_segment_payload_or_order(
    tmp_path: Path, mutation: str
) -> None:
    run_directory = tmp_path / mutation
    make_partial(run_directory)
    resume_durable_task(run_directory)
    journal_path = run_directory / "events.jsonl"
    records = [
        json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()
    ]
    resume_index = next(
        index
        for index, record in enumerate(records)
        if record["event_kind"] == "run_resumed"
    )
    if mutation == "same_segment":
        for record in records[resume_index:]:
            record["segment"] = 0
    elif mutation == "resume_payload":
        records[resume_index]["payload"]["resume_target"] = "VERIFYING"
    else:
        state_record = records.pop(resume_index + 1)
        state_record["segment"] = 0
        records.insert(resume_index, state_record)
    final_hash = rewrite_journal(journal_path, records)
    result_path = run_directory / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["journal_final_hash"] = final_hash
    if mutation == "same_segment":
        result["segment_count"] = 1
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)


def rewrite_journal(path: Path, records: list[dict[str, object]]) -> str:
    previous = ""
    lines: list[bytes] = []
    for sequence, record in enumerate(records):
        record["sequence"] = sequence
        record["previous_record_hash"] = previous
        record.pop("record_hash", None)
        current = record_hash(record)
        record["record_hash"] = current
        lines.append(canonical_json_bytes(record))
        previous = current
    path.write_bytes(b"\n".join(lines) + b"\n")
    return previous
