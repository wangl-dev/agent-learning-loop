"""Load the one packaged M3A post-Observation interruption schedule."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Literal

from pydantic import Field

from agent_learning_loop.schemas import StrictModel


class InterruptionSchedule(StrictModel):
    schema_version: Literal["1"] = "1"
    schedule_version: Literal[1] = 1
    schedule_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_id: Literal["workspace.fix-config"]
    step_index: Literal[2]
    boundary: Literal["post_observation"]
    provenance: str = Field(min_length=1)
    golden_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class InterruptionScheduleNotFoundError(LookupError):
    pass


class InterruptionScheduleMismatchError(ValueError):
    pass


GOLDEN_INTERRUPTION_FINGERPRINT = (
    "4e419fb3f5514acd130f1a08e6ee5e5b7b48ecc187f43cd7aeabd595a1ad5db5"
)


def load_all_interruption_schedules() -> list[InterruptionSchedule]:
    root = files("agent_learning_loop").joinpath("interruption_schedules")
    schedules = [
        InterruptionSchedule.model_validate_json(resource.read_text(encoding="utf-8"))
        for resource in root.iterdir()
        if resource.name.endswith(".json")
    ]
    return sorted(schedules, key=lambda schedule: schedule.schedule_id)


def load_interruption_schedule(schedule_id: str) -> InterruptionSchedule:
    for schedule in load_all_interruption_schedules():
        if schedule.schedule_id == schedule_id:
            return schedule
    raise InterruptionScheduleNotFoundError(
        f"unknown M3A interruption schedule: {schedule_id}"
    )


def fingerprint_interruption_schedule(schedule: InterruptionSchedule) -> str:
    payload = schedule.model_dump(mode="json", exclude={"golden_fingerprint"})
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_interruption_schedule(
    schedule: InterruptionSchedule, *, task_id: str
) -> None:
    actual = fingerprint_interruption_schedule(schedule)
    if (
        schedule.golden_fingerprint != GOLDEN_INTERRUPTION_FINGERPRINT
        or actual != GOLDEN_INTERRUPTION_FINGERPRINT
    ):
        raise InterruptionScheduleMismatchError(
            "interruption schedule does not match the packaged M3A schedule"
        )
    if schedule.task_id != task_id:
        raise InterruptionScheduleMismatchError(
            "interruption schedule does not belong to selected task"
        )
