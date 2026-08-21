"""Independent append-only journal for safe M3B action references."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Literal, Self

from pydantic import Field, ValidationError, model_validator

from agent_learning_loop.journal import canonical_json_bytes, record_hash
from agent_learning_loop.schemas import StrictModel, ToolName

ActionJournalEventKind = Literal[
    "source_started",
    "action_started",
    "action_finished",
    "source_finished",
]


class ActionJournalValidationError(ValueError):
    """The action-reference journal is incomplete, malformed, or inconsistent."""


class ActionJournalRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    source_run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    event_kind: ActionJournalEventKind
    catalog_id: str = Field(min_length=1)
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_index: int | None = Field(default=None, ge=1)
    action_ref: str | None = None
    tool_name: ToolName | None = None
    action_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempt_count: int | None = Field(default=None, ge=1)
    post_action_workspace_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_event_final_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_result_summary_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_final_workspace_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_verifier_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    action_count: int | None = Field(default=None, ge=0)
    previous_record_hash: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def fields_match_event_kind(self) -> Self:
        action_identity = (
            self.step_index,
            self.action_ref,
            self.tool_name,
            self.action_fingerprint,
        )
        finish_values = (self.attempt_count, self.post_action_workspace_digest)
        source_finish_values = (
            self.source_event_final_hash,
            self.source_result_summary_digest,
            self.source_final_workspace_digest,
            self.source_verifier_digest,
            self.action_count,
        )
        if self.event_kind == "source_started":
            all_specific_values = (
                *action_identity,
                *finish_values,
                *source_finish_values,
            )
            if any(value is not None for value in all_specific_values):
                raise ValueError("source_started contains event-specific fields")
        elif self.event_kind == "action_started":
            if any(value is None for value in action_identity) or any(
                value is not None for value in (*finish_values, *source_finish_values)
            ):
                raise ValueError("action_started fields are incomplete")
        elif self.event_kind == "action_finished":
            if any(value is None for value in (*action_identity, *finish_values)) or any(
                value is not None for value in source_finish_values
            ):
                raise ValueError("action_finished fields are incomplete")
        elif any(value is not None for value in (*action_identity, *finish_values)) or any(
            value is None for value in source_finish_values
        ):
            raise ValueError("source_finished fields are incomplete")
        return self


def action_record_hash_payload(record: ActionJournalRecord) -> dict[str, object]:
    return record.model_dump(
        mode="json", exclude={"record_hash"}, exclude_none=True
    )


class ActionJournalWriter:
    def __init__(
        self,
        path: Path,
        stream: IO[str],
        *,
        source_run_id: str,
        task_id: str,
        catalog_id: str,
        catalog_fingerprint: str,
    ) -> None:
        self.path = path
        self._stream = stream
        self.source_run_id = source_run_id
        self.task_id = task_id
        self.catalog_id = catalog_id
        self.catalog_fingerprint = catalog_fingerprint
        self.record_count = 0
        self.final_hash = ""

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        source_run_id: str,
        task_id: str,
        catalog_id: str,
        catalog_fingerprint: str,
    ) -> Self:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("x", encoding="utf-8", newline="\n")
        return cls(
            path,
            stream,
            source_run_id=source_run_id,
            task_id=task_id,
            catalog_id=catalog_id,
            catalog_fingerprint=catalog_fingerprint,
        )

    def append(
        self,
        event_kind: ActionJournalEventKind,
        *,
        step_index: int | None = None,
        action_ref: str | None = None,
        tool_name: ToolName | None = None,
        action_fingerprint: str | None = None,
        attempt_count: int | None = None,
        post_action_workspace_digest: str | None = None,
        source_event_final_hash: str | None = None,
        source_result_summary_digest: str | None = None,
        source_final_workspace_digest: str | None = None,
        source_verifier_digest: str | None = None,
        action_count: int | None = None,
    ) -> ActionJournalRecord:
        base: dict[str, object] = {
            "schema_version": "1",
            "source_run_id": self.source_run_id,
            "task_id": self.task_id,
            "sequence": self.record_count,
            "event_kind": event_kind,
            "catalog_id": self.catalog_id,
            "catalog_fingerprint": self.catalog_fingerprint,
            "previous_record_hash": self.final_hash,
        }
        optional = {
            "step_index": step_index,
            "action_ref": action_ref,
            "tool_name": tool_name,
            "action_fingerprint": action_fingerprint,
            "attempt_count": attempt_count,
            "post_action_workspace_digest": post_action_workspace_digest,
            "source_event_final_hash": source_event_final_hash,
            "source_result_summary_digest": source_result_summary_digest,
            "source_final_workspace_digest": source_final_workspace_digest,
            "source_verifier_digest": source_verifier_digest,
            "action_count": action_count,
        }
        base.update({name: value for name, value in optional.items() if value is not None})
        record = ActionJournalRecord.model_validate(
            {**base, "record_hash": record_hash(base)}
        )
        serialized = canonical_json_bytes(
            record.model_dump(mode="json", exclude_none=True)
        ).decode("utf-8")
        self._stream.write(serialized)
        self._stream.write("\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.record_count += 1
        self.final_hash = record.record_hash
        return record

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def read_and_validate_action_journal(path: Path) -> list[ActionJournalRecord]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ActionJournalValidationError("action journal could not be read") from exc
    if not raw or not raw.endswith(b"\n"):
        raise ActionJournalValidationError(
            "action journal must end after a complete JSONL record"
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ActionJournalValidationError("action journal is not strict UTF-8") from exc
    records: list[ActionJournalRecord] = []
    for index, line in enumerate(text.splitlines()):
        if not line:
            raise ActionJournalValidationError("action journal cannot contain blank records")
        try:
            payload: Any = json.loads(line)
            record = ActionJournalRecord.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ActionJournalValidationError(
                "action journal record failed strict v1 validation"
            ) from exc
        if record.sequence != index:
            raise ActionJournalValidationError("action journal sequence is not continuous")
        if records:
            previous = records[-1]
            if (
                record.source_run_id != previous.source_run_id
                or record.task_id != previous.task_id
                or record.catalog_id != previous.catalog_id
                or record.catalog_fingerprint != previous.catalog_fingerprint
            ):
                raise ActionJournalValidationError(
                    "action journal source/catalog identity changed"
                )
            if record.previous_record_hash != previous.record_hash:
                raise ActionJournalValidationError("action journal hash chain is broken")
        elif record.previous_record_hash != "":
            raise ActionJournalValidationError("action journal first record has a prefix")
        if record.record_hash != record_hash(action_record_hash_payload(record)):
            raise ActionJournalValidationError(
                "action journal record hash does not match its content"
            )
        records.append(record)
    return records
