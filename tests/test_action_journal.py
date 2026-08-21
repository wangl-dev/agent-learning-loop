from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_learning_loop.action_journal import (
    ActionJournalRecord,
    ActionJournalValidationError,
    ActionJournalWriter,
    read_and_validate_action_journal,
)

HASH = "a" * 64
OTHER_HASH = "b" * 64


def write_complete_journal(path: Path) -> None:
    with ActionJournalWriter.create(
        path,
        source_run_id="source-run",
        task_id="workspace.fix-config",
        catalog_id="workspace.fix-config.actions.v1",
        catalog_fingerprint=HASH,
    ) as writer:
        writer.append("source_started")
        writer.append(
            "action_started",
            step_index=1,
            action_ref="workspace.fix-config.step-1.v1",
            tool_name="read_text",
            action_fingerprint=OTHER_HASH,
        )
        writer.append(
            "action_finished",
            step_index=1,
            action_ref="workspace.fix-config.step-1.v1",
            tool_name="read_text",
            action_fingerprint=OTHER_HASH,
            attempt_count=1,
            post_action_workspace_digest=HASH,
        )
        writer.append(
            "source_finished",
            source_event_final_hash=HASH,
            source_result_summary_digest=OTHER_HASH,
            source_final_workspace_digest=HASH,
            source_verifier_digest=OTHER_HASH,
            action_count=1,
        )


def test_action_journal_appends_independent_canonical_hash_chain(tmp_path: Path) -> None:
    path = tmp_path / "actions.jsonl"
    write_complete_journal(path)

    records = read_and_validate_action_journal(path)

    assert [record.sequence for record in records] == [0, 1, 2, 3]
    assert [record.event_kind for record in records] == [
        "source_started",
        "action_started",
        "action_finished",
        "source_finished",
    ]
    assert records[0].previous_record_hash == ""
    assert records[-1].previous_record_hash == records[-2].record_hash
    assert path.read_bytes().endswith(b"\n")
    public_text = path.read_text(encoding="utf-8")
    assert '"arguments"' not in public_text
    assert '"content"' not in public_text
    assert '"tool_result"' not in public_text


@pytest.mark.parametrize("mutation", ["missing", "reordered", "modified", "partial_line"])
def test_action_journal_rejects_missing_reordered_modified_or_partial_records(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / f"{mutation}.jsonl"
    write_complete_journal(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    if mutation == "missing":
        lines.pop(1)
    elif mutation == "reordered":
        lines[1], lines[2] = lines[2], lines[1]
    elif mutation == "modified":
        payload = json.loads(lines[1])
        payload["action_ref"] = "workspace.fix-config.step-9.v1"
        lines[1] = json.dumps(payload, separators=(",", ":"))
    else:
        path.write_bytes(path.read_bytes()[:-1])
    if mutation != "partial_line":
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ActionJournalValidationError):
        read_and_validate_action_journal(path)


def test_action_journal_record_rejects_unknown_fields() -> None:
    payload = {
        "schema_version": "1",
        "source_run_id": "source-run",
        "task_id": "workspace.fix-config",
        "sequence": 0,
        "event_kind": "source_started",
        "catalog_id": "workspace.fix-config.actions.v1",
        "catalog_fingerprint": HASH,
        "previous_record_hash": "",
        "record_hash": OTHER_HASH,
        "arguments": {"path": "app.conf"},
    }

    with pytest.raises(ValidationError):
        ActionJournalRecord.model_validate(payload)
