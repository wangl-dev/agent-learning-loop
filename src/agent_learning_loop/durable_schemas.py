"""Strict M3A contracts for durable events, checkpoints, and results."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeState
from agent_learning_loop.schemas import StrictModel, ToolName, VerifierResult

HashValue = str
DurableEventKind = Literal[
    "run_started",
    "runtime_state_changed",
    "attempt_started",
    "attempt_completed",
    "failure_injected",
    "retry_scheduled",
    "idempotency_hit",
    "checkpoint_committed",
    "interruption_injected",
    "run_resumed",
    "verification_finished",
    "run_finished",
]


class CheckpointingMode(StrEnum):
    OFF = "off"
    ON = "on"


class DurableEventPayload(StrictModel):
    """Allow only stable metadata; action arguments and raw results have no field."""

    mode: Literal["safeguarded"] | None = None
    checkpointing: Literal["on", "off"] | None = None
    fixture_id: str | None = None
    fixture_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_schedule_id: str | None = None
    failure_schedule_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    interruption_schedule_id: str | None = None
    interruption_schedule_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    seed: int | None = None
    from_state: str | None = None
    to_state: str | None = None
    tool_name: ToolName | None = None
    occurrence: int | None = Field(default=None, ge=1)
    status: Literal["ok", "error"] | None = None
    failure_kind: Literal["result_lost"] | None = None
    error_category: Literal["tool_transient"] | None = None
    retryable: bool | None = None
    failed_attempt: int | None = Field(default=None, ge=1)
    next_attempt: int | None = Field(default=None, ge=1)
    backoff_seconds: float | None = Field(default=None, ge=0.0)
    checkpoint_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_step: int | None = Field(default=None, ge=0)
    checkpoint_record_count: int | None = Field(default=None, ge=1)
    checkpoint_final_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    boundary: Literal["post_observation"] | None = None
    resume_target: str | None = None
    verifier_passed: bool | None = None
    verifier_summary_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    result_summary_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    terminal_state: str | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")


class DurableEvent(StrictModel):
    schema_version: Literal["3"] = "3"
    run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    step_index: int = Field(ge=0)
    attempt: int = Field(ge=0)
    segment: int = Field(ge=0)
    event_kind: DurableEventKind
    payload: DurableEventPayload
    previous_record_hash: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DurableUsage(StrictModel):
    schema_version: Literal["3"] = "3"
    steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    physical_executions: int = Field(default=0, ge=0)
    physical_write_executions: int = Field(default=0, ge=0)
    side_effect_executions: int = Field(default=0, ge=0)
    duplicate_side_effects: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    idempotency_hits: int = Field(default=0, ge=0)
    backoff_seconds: float = Field(default=0.0, ge=0.0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)


class ExperimentIdentity(StrictModel):
    schema_version: Literal["3"] = "3"
    task_id: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    fixture_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_schedule_id: str = Field(min_length=1)
    failure_schedule_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    interruption_schedule_id: str | None = None
    interruption_schedule_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    checkpointing: CheckpointingMode
    seed: int

    @model_validator(mode="after")
    def interruption_identity_is_complete(self) -> Self:
        if (self.interruption_schedule_id is None) != (
            self.interruption_schedule_fingerprint is None
        ):
            raise ValueError("interruption schedule ID and fingerprint must appear together")
        return self


class ToolOccurrence(StrictModel):
    tool_name: ToolName
    count: int = Field(ge=1)


class FailureTrackerState(StrictModel):
    occurrences: list[ToolOccurrence]
    injection_count: int = Field(ge=0)

    @model_validator(mode="after")
    def occurrence_tools_are_unique(self) -> Self:
        names = [entry.tool_name for entry in self.occurrences]
        if len(names) != len(set(names)):
            raise ValueError("failure tracker occurrence tools must be unique")
        return self


class IdempotencyEntry(StrictModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_name: Literal["write_text"]
    result_path: str = Field(min_length=1)
    bytes_written: int = Field(ge=0)


class Checkpoint(StrictModel):
    schema_version: Literal["1"] = "1"
    checkpoint_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    identity: ExperimentIdentity
    runtime_config: RuntimeConfig
    resume_step: int = Field(ge=0)
    runtime_state: RuntimeState
    resume_target: RuntimeState
    usage: DurableUsage
    failure_tracker: FailureTrackerState
    idempotency_entries: list[IdempotencyEntry]
    workspace_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_record_count: int = Field(ge=1)
    journal_final_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment: int = Field(ge=0)

    @model_validator(mode="after")
    def checkpoint_is_a_supported_safe_boundary(self) -> Self:
        if self.runtime_state is not RuntimeState.OBSERVING:
            raise ValueError("M3A checkpoints only an Observation boundary")
        if self.resume_target is not RuntimeState.DECIDING:
            raise ValueError("M3A resumes only toward the next decision")
        if self.resume_step != 2:
            raise ValueError("M3A supports only the fixed step-2 safe boundary")
        return self


class DurableResultSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    task_id: str = Field(min_length=1)
    terminal_state: RuntimeState
    identity: ExperimentIdentity
    runtime_config: RuntimeConfig
    verifier: VerifierResult
    usage: DurableUsage
    resumed: bool
    segment_count: int = Field(ge=1)
    checkpoint_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_step: int | None = Field(default=None, ge=0)
    action_replay_match_rate: Literal["N/A"] = "N/A"


def verifier_summary_digest(verifier: VerifierResult) -> str:
    payload = verifier.model_dump(mode="json")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def durable_result_summary_digest(summary: DurableResultSummary) -> str:
    payload = summary.model_dump(mode="json")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DurableResult(StrictModel):
    schema_version: Literal["3"] = "3"
    run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    task_id: str = Field(min_length=1)
    terminal_state: RuntimeState
    identity: ExperimentIdentity
    runtime_config: RuntimeConfig
    verifier: VerifierResult
    usage: DurableUsage
    resumed: bool
    segment_count: int = Field(ge=1)
    checkpoint_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_step: int | None = Field(default=None, ge=0)
    journal_record_count: int = Field(ge=1)
    journal_final_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    events_file: Literal["events.jsonl"] = "events.jsonl"
    workspace_dir: Literal["workspace"] = "workspace"
    action_replay_match_rate: Literal["N/A"] = "N/A"
    limitation: str = (
        "M3A resumes only a committed post-Observation checkpoint; event replay does not "
        "execute actions or promise arbitrary-crash exactly-once behavior."
    )

    @model_validator(mode="after")
    def result_identity_is_consistent(self) -> Self:
        if self.task_id != self.identity.task_id:
            raise ValueError("result task does not match experiment identity")
        if self.resumed and (self.checkpoint_id is None or self.checkpoint_step != 2):
            raise ValueError("a resumed result must identify its step-2 checkpoint")
        if not self.resumed and (
            self.checkpoint_id is not None or self.checkpoint_step is not None
        ):
            raise ValueError("an uninterrupted result cannot claim a checkpoint")
        return self

    def summary(self) -> DurableResultSummary:
        return DurableResultSummary(
            run_id=self.run_id,
            task_id=self.task_id,
            terminal_state=self.terminal_state,
            identity=self.identity,
            runtime_config=self.runtime_config,
            verifier=self.verifier,
            usage=self.usage,
            resumed=self.resumed,
            segment_count=self.segment_count,
            checkpoint_id=self.checkpoint_id,
            checkpoint_step=self.checkpoint_step,
        )
