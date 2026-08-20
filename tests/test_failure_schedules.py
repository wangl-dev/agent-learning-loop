from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_learning_loop.failure_schedules import (
    FailureScheduleMismatchError,
    fingerprint_schedule,
    load_all_failure_schedules,
    load_failure_schedule,
    validate_schedule_for_task,
)

EXPECTED = {
    "workspace.logical-timeout.v1": {
        "task_id": "workspace.update-status",
        "seed": 202,
        "tool_name": "read_text",
        "occurrence": 1,
        "injection_phase": "before_execution",
        "failure_kind": "logical_timeout",
        "error_category": "timeout",
        "fingerprint": "4b2ab244685d642c803d3fdb96e8a19d2a91aef78d7a74859c25c4210cce8d74",
    },
    "workspace.lost-write-result.v1": {
        "task_id": "workspace.fix-config",
        "seed": 303,
        "tool_name": "write_text",
        "occurrence": 1,
        "injection_phase": "after_success",
        "failure_kind": "result_lost",
        "error_category": "tool_transient",
        "fingerprint": "6720ea76647c9a8ec587852bd18f78919ae3d5ce0baf0d27c5637bf6d333f5ce",
    },
    "workspace.transient-read.v1": {
        "task_id": "workspace.build-summary",
        "seed": 101,
        "tool_name": "read_text",
        "occurrence": 1,
        "injection_phase": "before_execution",
        "failure_kind": "transient",
        "error_category": "tool_transient",
        "fingerprint": "118f5616a9386885dcfaa3d0e94084866532aacb5177c2e76b008e3af10afc8c",
    },
}


def test_three_packaged_failure_schedules_are_strict_and_stable() -> None:
    schedules = load_all_failure_schedules()
    assert {schedule.schedule_id for schedule in schedules} == set(EXPECTED)

    for schedule in schedules:
        expected = EXPECTED[schedule.schedule_id]
        assert schedule.task_id == expected["task_id"]
        assert schedule.seed == expected["seed"]
        assert schedule.rule.target_tool == expected["tool_name"]
        assert schedule.rule.occurrence == expected["occurrence"]
        assert schedule.rule.injection_phase == expected["injection_phase"]
        assert schedule.rule.failure_kind == expected["failure_kind"]
        assert schedule.rule.error_category.value == expected["error_category"]
        assert schedule.rule.retryable is True
        assert schedule.provenance == "Project-authored synthetic M2 failure schedule."
        assert fingerprint_schedule(schedule) == expected["fingerprint"]


def test_schedule_task_mismatch_is_rejected_before_runtime_execution() -> None:
    schedule = load_failure_schedule("workspace.transient-read.v1")
    with pytest.raises(FailureScheduleMismatchError):
        validate_schedule_for_task(schedule, "workspace.fix-config")


def test_mutated_schedule_with_the_same_ids_and_seed_is_rejected() -> None:
    schedule = load_failure_schedule("workspace.transient-read.v1")
    mutated = schedule.model_copy(
        update={"rule": schedule.rule.model_copy(update={"occurrence": 99})}
    )

    with pytest.raises(FailureScheduleMismatchError):
        validate_schedule_for_task(mutated, schedule.task_id)


def test_failure_schedule_rejects_unknown_fields() -> None:
    schedule = load_failure_schedule("workspace.transient-read.v1")
    payload = schedule.model_dump(mode="json")
    payload["rule"]["unexpected"] = True
    with pytest.raises(ValidationError):
        type(schedule).model_validate(payload)
