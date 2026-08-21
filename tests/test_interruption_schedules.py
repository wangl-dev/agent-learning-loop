from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from agent_learning_loop.interruption_schedules import (
    InterruptionSchedule,
    InterruptionScheduleMismatchError,
    fingerprint_interruption_schedule,
    load_all_interruption_schedules,
    load_interruption_schedule,
    validate_interruption_schedule,
)


def test_packaged_interruption_schedule_is_strict_fixed_and_stable() -> None:
    schedules = load_all_interruption_schedules()

    assert len(schedules) == 1
    schedule = schedules[0]
    assert schedule.schedule_id == "workspace.post-write-boundary.v1"
    assert schedule.task_id == "workspace.fix-config"
    assert schedule.step_index == 2
    assert schedule.boundary == "post_observation"
    assert schedule.schedule_version == 1
    assert fingerprint_interruption_schedule(schedule) == schedule.golden_fingerprint
    validate_interruption_schedule(schedule, task_id="workspace.fix-config")


def test_mutated_interruption_schedule_is_rejected() -> None:
    schedule = load_interruption_schedule("workspace.post-write-boundary.v1")
    payload = schedule.model_dump(mode="json")
    payload["provenance"] = "mutated provenance"
    mutated = InterruptionSchedule.model_validate(payload)

    with pytest.raises(InterruptionScheduleMismatchError):
        validate_interruption_schedule(mutated, task_id="workspace.fix-config")


def test_interruption_schedule_rejects_unknown_fields() -> None:
    payload = deepcopy(
        load_interruption_schedule("workspace.post-write-boundary.v1").model_dump(mode="json")
    )
    payload["arbitrary_crash_hook"] = "forbidden"

    with pytest.raises(ValidationError):
        InterruptionSchedule.model_validate(payload)
