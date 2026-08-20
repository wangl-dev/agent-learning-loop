"""Load and fingerprint the three packaged, deterministic M2 failure schedules."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Literal, Self

from pydantic import Field, model_validator

from agent_learning_loop.runtime_schemas import ErrorCategory
from agent_learning_loop.schemas import StrictModel, ToolName


class FailureRule(StrictModel):
    target_tool: ToolName
    occurrence: int = Field(ge=1)
    injection_phase: Literal["before_execution", "after_success"]
    failure_kind: Literal["transient", "logical_timeout", "result_lost"]
    error_category: ErrorCategory
    retryable: bool

    @model_validator(mode="after")
    def kind_matches_public_error(self) -> Self:
        expected = {
            "transient": ("before_execution", ErrorCategory.TOOL_TRANSIENT),
            "logical_timeout": ("before_execution", ErrorCategory.TIMEOUT),
            "result_lost": ("after_success", ErrorCategory.TOOL_TRANSIENT),
        }[self.failure_kind]
        if (self.injection_phase, self.error_category) != expected:
            raise ValueError("failure kind does not match injection phase and error category")
        if not self.retryable:
            raise ValueError("the three M2 injected failures are retryable by definition")
        return self


class FailureSchedule(StrictModel):
    schema_version: Literal["1"] = "1"
    schedule_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_id: str = Field(min_length=1)
    seed: int
    rule: FailureRule
    provenance: str = Field(min_length=1)


class FailureScheduleNotFoundError(LookupError):
    pass


class FailureScheduleMismatchError(ValueError):
    pass


CANONICAL_SCHEDULE_FINGERPRINTS: dict[str, str] = {
    "workspace.logical-timeout.v1": (
        "4b2ab244685d642c803d3fdb96e8a19d2a91aef78d7a74859c25c4210cce8d74"
    ),
    "workspace.lost-write-result.v1": (
        "6720ea76647c9a8ec587852bd18f78919ae3d5ce0baf0d27c5637bf6d333f5ce"
    ),
    "workspace.transient-read.v1": (
        "118f5616a9386885dcfaa3d0e94084866532aacb5177c2e76b008e3af10afc8c"
    ),
}


def load_all_failure_schedules() -> list[FailureSchedule]:
    root = files("agent_learning_loop").joinpath("failure_schedules")
    schedules = [
        FailureSchedule.model_validate_json(resource.read_text(encoding="utf-8"))
        for resource in root.iterdir()
        if resource.name.endswith(".json")
    ]
    return sorted(schedules, key=lambda schedule: schedule.schedule_id)


def load_failure_schedule(schedule_id: str) -> FailureSchedule:
    for schedule in load_all_failure_schedules():
        if schedule.schedule_id == schedule_id:
            return schedule
    raise FailureScheduleNotFoundError(f"unknown M2 failure schedule: {schedule_id}")


def validate_schedule_for_task(schedule: FailureSchedule, task_id: str) -> None:
    expected_fingerprint = CANONICAL_SCHEDULE_FINGERPRINTS.get(schedule.schedule_id)
    if (
        expected_fingerprint is None
        or fingerprint_schedule(schedule) != expected_fingerprint
    ):
        raise FailureScheduleMismatchError(
            "failure schedule does not match a canonical M2 schedule"
        )
    if schedule.task_id != task_id:
        raise FailureScheduleMismatchError("failure schedule does not belong to selected task")


def fingerprint_schedule(schedule: FailureSchedule) -> str:
    canonical = json.dumps(
        schedule.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
