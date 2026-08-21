"""Append-only JSONL journal with a canonical SHA-256 record chain."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from pydantic import ValidationError

from agent_learning_loop.durable_schemas import (
    DurableEvent,
    DurableEventKind,
    DurableEventPayload,
)


class JournalValidationError(ValueError):
    pass


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def record_hash(event_without_hash: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(event_without_hash)).hexdigest()


def event_hash_payload(event: DurableEvent) -> dict[str, object]:
    return event.model_dump(mode="json", exclude={"record_hash"})


class AppendOnlyJournal:
    def __init__(
        self,
        path: Path,
        stream: IO[str],
        *,
        run_id: str,
        task_id: str,
        segment: int,
        record_count: int,
        final_hash: str,
    ) -> None:
        self.path = path
        self._stream = stream
        self.run_id = run_id
        self.task_id = task_id
        self.segment = segment
        self.record_count = record_count
        self.final_hash = final_hash

    @classmethod
    def create(cls, path: Path, *, run_id: str, task_id: str) -> Self:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("x", encoding="utf-8", newline="\n")
        return cls(
            path,
            stream,
            run_id=run_id,
            task_id=task_id,
            segment=0,
            record_count=0,
            final_hash="",
        )

    @classmethod
    def resume(cls, path: Path, *, records: list[DurableEvent], segment: int) -> Self:
        if not records:
            raise JournalValidationError("cannot resume an empty journal")
        stream = path.open("a", encoding="utf-8", newline="\n")
        return cls(
            path,
            stream,
            run_id=records[0].run_id,
            task_id=records[0].task_id,
            segment=segment,
            record_count=len(records),
            final_hash=records[-1].record_hash,
        )

    def append(
        self,
        event_kind: DurableEventKind,
        *,
        step_index: int,
        attempt: int,
        payload: DurableEventPayload,
    ) -> DurableEvent:
        base: dict[str, object] = {
            "schema_version": "3",
            "run_id": self.run_id,
            "task_id": self.task_id,
            "sequence": self.record_count,
            "step_index": step_index,
            "attempt": attempt,
            "segment": self.segment,
            "event_kind": event_kind,
            "payload": payload.model_dump(mode="json"),
            "previous_record_hash": self.final_hash,
        }
        event = DurableEvent.model_validate({**base, "record_hash": record_hash(base)})
        self._stream.write(canonical_json_bytes(event.model_dump(mode="json")).decode("utf-8"))
        self._stream.write("\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.record_count += 1
        self.final_hash = event.record_hash
        return event

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


def read_and_validate_journal(path: Path) -> list[DurableEvent]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise JournalValidationError("journal could not be read") from exc
    if not raw or not raw.endswith(b"\n"):
        raise JournalValidationError("journal must end after a complete JSONL record")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise JournalValidationError("journal is not strict UTF-8") from exc
    records: list[DurableEvent] = []
    for index, line in enumerate(text.splitlines()):
        if not line:
            raise JournalValidationError("journal cannot contain blank records")
        try:
            payload: Any = json.loads(line)
            event = DurableEvent.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise JournalValidationError("journal record failed strict v3 validation") from exc
        if event.sequence != index:
            raise JournalValidationError("journal sequence is not continuous")
        if records:
            previous = records[-1]
            if event.run_id != previous.run_id or event.task_id != previous.task_id:
                raise JournalValidationError("journal run/task identity changed")
            if event.previous_record_hash != previous.record_hash:
                raise JournalValidationError("journal hash chain is broken")
            if event.segment not in {previous.segment, previous.segment + 1}:
                raise JournalValidationError("journal segment is not continuous")
            if event.segment != previous.segment and event.event_kind != "run_resumed":
                raise JournalValidationError("a new segment must begin with run_resumed")
        elif event.previous_record_hash != "" or event.segment != 0:
            raise JournalValidationError("journal first record has an invalid prefix")
        expected_hash = record_hash(event_hash_payload(event))
        if event.record_hash != expected_hash:
            raise JournalValidationError("journal record hash does not match its content")
        records.append(event)
    return records
