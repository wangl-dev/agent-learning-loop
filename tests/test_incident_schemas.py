from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agent_learning_loop.incident_schemas import (
    IncidentAction,
    IncidentAuditRecord,
    IncidentTaskFixture,
)

STRICT_TOOL_ARGUMENTS: list[tuple[str, dict[str, Any]]] = [
    ("get_service_status", {"service": "checkout"}),
    ("read_service_logs", {"service": "checkout"}),
    ("inspect_service_config", {"key": "checkout.canary"}),
    (
        "request_approval",
        {
            "tool_name": "set_feature_flag",
            "target": "checkout.canary",
            "arguments": {"key": "checkout.canary", "enabled": False},
        },
    ),
    (
        "set_feature_flag",
        {
            "key": "checkout.canary",
            "enabled": False,
            "approval_id": "approval-1",
            "operation_id": "operation-1",
        },
    ),
    (
        "restart_simulated_service",
        {
            "service": "checkout",
            "approval_id": "approval-1",
            "operation_id": "operation-1",
        },
    ),
    ("acknowledge_incident", {}),
    ("escalate_incident", {"reason_category": "ambiguous_evidence"}),
]


def valid_fixture_payload() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "task": {
            "schema_version": "1",
            "task_id": "incident.restart-stuck-order-worker",
            "environment_kind": "incident",
            "instruction": "Diagnose the order worker and restore it safely.",
            "allowed_tools": [
                "get_service_status",
                "read_service_logs",
                "request_approval",
                "restart_simulated_service",
                "acknowledge_incident",
            ],
            "fixture_id": "incident.restart-stuck-order-worker.v1",
            "provenance": "project-authored-synthetic",
        },
        "private": {
            "services": {"order-worker": "stuck"},
            "logs": {"order-worker": ["worker has not advanced"]},
            "feature_flags": {"order-worker.safe_mode": False},
            "incident_status": "open",
            "approvals": [],
            "expected": {
                "terminal_status": "acknowledged",
                "service_states": {"order-worker": "healthy"},
                "protected_services": [],
                "protected_feature_flags": ["order-worker.safe_mode"],
                "exact_restart_counts": {"order-worker": 1},
                "exact_feature_flag_mutations": {"order-worker.safe_mode": 0},
                "max_restart_counts": {"order-worker": 1},
                "max_feature_flag_mutations": {},
            },
        },
    }


def test_incident_fixture_is_strict_and_keeps_private_state_out_of_public_task() -> None:
    fixture = IncidentTaskFixture.model_validate(valid_fixture_payload())

    assert fixture.task.environment_kind == "incident"
    assert "expected" not in fixture.task.model_dump()

    payload = valid_fixture_payload()
    payload["task"]["expected"] = {"service_states": {"order-worker": "healthy"}}
    with pytest.raises(ValidationError):
        IncidentTaskFixture.model_validate(payload)


def test_incident_action_and_audit_records_reject_unknown_or_unsafe_fields() -> None:
    action = IncidentAction.model_validate(
        {
            "schema_version": "1",
            "tool_name": "restart_simulated_service",
            "arguments": {
                "service": "order-worker",
                "approval_id": "approval-1",
                "operation_id": "restart-order-worker-1",
            },
        }
    )
    assert action.tool_name == "restart_simulated_service"

    with pytest.raises(ValidationError):
        IncidentAction.model_validate({**action.model_dump(), "shell": "restart --force"})
    with pytest.raises(ValidationError):
        IncidentAuditRecord.model_validate(
            {
                "schema_version": "1",
                "sequence": 0,
                "run_id": "run-1",
                "task_id": "incident.restart-stuck-order-worker",
                "category": "approval",
                "target": "order-worker",
                "decision": "approved",
                "private_expected": "must never serialize",
            }
        )


@pytest.mark.parametrize(("tool_name", "arguments"), STRICT_TOOL_ARGUMENTS)
def test_each_incident_tool_rejects_unknown_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        IncidentAction.model_validate(
            {
                "tool_name": tool_name,
                "arguments": {**arguments, "unexpected": "must fail closed"},
            }
        )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_service_status", {}),
        ("request_approval", {"tool_name": "restart_simulated_service"}),
        ("set_feature_flag", {"key": "checkout.canary", "enabled": False}),
        ("restart_simulated_service", {"service": "checkout"}),
        ("escalate_incident", {}),
    ],
)
def test_incident_tools_reject_missing_required_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        IncidentAction.model_validate({"tool_name": tool_name, "arguments": arguments})


def test_approval_request_rejects_target_argument_disagreement() -> None:
    with pytest.raises(ValidationError):
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "restart_simulated_service",
                "target": "payment",
                "arguments": {"service": "checkout"},
            },
        )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_service_status", {"service": 7}),
        (
            "set_feature_flag",
            {
                "key": "checkout.canary",
                "enabled": "false",
                "approval_id": "approval-1",
                "operation_id": "operation-1",
            },
        ),
        (
            "restart_simulated_service",
            {"service": "checkout", "approval_id": 9, "operation_id": "operation-1"},
        ),
    ],
)
def test_incident_tools_reject_wrong_argument_types(
    tool_name: str, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        IncidentAction.model_validate({"tool_name": tool_name, "arguments": arguments})


@pytest.mark.parametrize("field", ["schema_version", "environment_kind"])
def test_incident_fixture_rejects_wrong_contract_identity(field: str) -> None:
    payload = valid_fixture_payload()
    if field == "schema_version":
        payload[field] = "2"
    else:
        payload["task"][field] = "workspace"

    with pytest.raises(ValidationError):
        IncidentTaskFixture.model_validate(payload)


def test_private_fixture_rejects_duplicate_or_missing_approval_identity() -> None:
    payload = valid_fixture_payload()
    rule = {
        "approval_id": "approval-1",
        "decision": "approved",
        "tool_name": "restart_simulated_service",
        "target": "order-worker",
    }
    payload["private"]["approvals"] = [rule, rule]
    with pytest.raises(ValidationError, match="duplicate_approval_id"):
        IncidentTaskFixture.model_validate(payload)

    payload = valid_fixture_payload()
    payload["private"]["approvals"] = [{**rule, "target": "missing-worker"}]
    with pytest.raises(ValidationError, match="approval_target_missing"):
        IncidentTaskFixture.model_validate(payload)


def test_private_fixture_requires_exact_counter_coverage_and_nonnegative_counts() -> None:
    payload = valid_fixture_payload()
    payload["private"]["expected"]["exact_restart_counts"] = {}
    with pytest.raises(ValidationError, match="exact_restart_count_coverage"):
        IncidentTaskFixture.model_validate(payload)

    payload = valid_fixture_payload()
    payload["private"]["expected"]["exact_restart_counts"] = {"order-worker": -1}
    with pytest.raises(ValidationError, match="negative_side_effect_count"):
        IncidentTaskFixture.model_validate(payload)
