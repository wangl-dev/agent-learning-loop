from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_learning_loop.durable_schemas import DurableEventPayload
from agent_learning_loop.journal import (
    AppendOnlyJournal,
    JournalValidationError,
    canonical_json_bytes,
    read_and_validate_journal,
)


def test_canonical_json_is_stable_across_mapping_order() -> None:
    left = {"b": [2, 1], "a": {"z": False, "y": "text"}}
    right = {"a": {"y": "text", "z": False}, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)


def test_append_writer_preserves_prefix_and_continues_sequence_hash_and_segment(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "events.jsonl"
    with AppendOnlyJournal.create(journal_path, run_id="run-1", task_id="task-1") as writer:
        first = writer.append(
            "run_started", step_index=0, attempt=0, payload=DurableEventPayload()
        )
        second = writer.append(
            "runtime_state_changed",
            step_index=0,
            attempt=0,
            payload=DurableEventPayload(from_state="CREATED", to_state="RESETTING"),
        )
    prefix = journal_path.read_bytes()
    records = read_and_validate_journal(journal_path)

    with AppendOnlyJournal.resume(journal_path, records=records, segment=1) as writer:
        third = writer.append(
            "run_resumed",
            step_index=2,
            attempt=0,
            payload=DurableEventPayload(resume_target="DECIDING"),
        )

    assert journal_path.read_bytes().startswith(prefix)
    assert [first.sequence, second.sequence, third.sequence] == [0, 1, 2]
    assert third.previous_record_hash == second.record_hash
    assert third.segment == 1
    assert read_and_validate_journal(journal_path)[-1] == third


def test_append_writer_refuses_to_create_over_an_existing_prefix(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        AppendOnlyJournal.create(path, run_id="run-1", task_id="task-1")


@pytest.mark.parametrize("mutation", ["payload", "hash", "sequence", "partial_line"])
def test_journal_validation_rejects_modified_records(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "events.jsonl"
    with AppendOnlyJournal.create(path, run_id="run-1", task_id="task-1") as writer:
        writer.append("run_started", step_index=0, attempt=0, payload=DurableEventPayload())
        writer.append(
            "runtime_state_changed",
            step_index=0,
            attempt=0,
            payload=DurableEventPayload(from_state="CREATED", to_state="RESETTING"),
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    if mutation == "payload":
        payload["payload"]["to_state"] = "READY"
        lines[1] = json.dumps(payload, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "hash":
        payload["record_hash"] = "0" * 64
        lines[1] = json.dumps(payload, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "sequence":
        payload["sequence"] = 0
        lines[1] = json.dumps(payload, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes()[:-2])

    with pytest.raises(JournalValidationError):
        read_and_validate_journal(path)
