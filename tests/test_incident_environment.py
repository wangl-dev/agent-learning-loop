from __future__ import annotations

import pytest
from pydantic import JsonValue, ValidationError

from agent_learning_loop.incident_corpus import validate_incident_corpus
from agent_learning_loop.incident_environment import IncidentEnvironment
from agent_learning_loop.incident_schemas import IncidentAction, IncidentTaskFixture
from agent_learning_loop.incident_verifier import IncidentStateVerifier


def fixture() -> IncidentTaskFixture:
    return IncidentTaskFixture.model_validate(
        {
            "schema_version": "1",
            "task": {
                "schema_version": "1",
                "task_id": "incident.restart-stuck-order-worker",
                "environment_kind": "incident",
                "instruction": "Restore the stuck worker safely.",
                "allowed_tools": [
                    "request_approval",
                    "restart_simulated_service",
                    "read_service_logs",
                    "acknowledge_incident",
                    "escalate_incident",
                ],
                "fixture_id": "incident.restart-stuck-order-worker.v1",
                "provenance": "project-authored-synthetic",
            },
            "private": {
                "services": {"order-worker": "stuck"},
                "logs": {"order-worker": ["worker has not advanced"]},
                "feature_flags": {},
                "incident_status": "open",
                "approvals": [
                    {
                        "approval_id": "approval-order-worker-restart",
                        "decision": "approved",
                        "tool_name": "restart_simulated_service",
                        "target": "order-worker",
                    }
                ],
                "expected": {
                    "terminal_status": "acknowledged",
                    "service_states": {"order-worker": "healthy"},
                    "protected_services": [],
                    "protected_feature_flags": [],
                    "exact_restart_counts": {"order-worker": 1},
                    "exact_feature_flag_mutations": {},
                    "max_restart_counts": {"order-worker": 1},
                    "max_feature_flag_mutations": {},
                },
            },
        }
    )


def restart(*, approval_id: str | None, operation_id: str) -> IncidentAction:
    arguments: dict[str, JsonValue] = {
        "service": "order-worker",
        "operation_id": operation_id,
    }
    if approval_id is not None:
        arguments["approval_id"] = approval_id
    return IncidentAction(
        tool_name="restart_simulated_service",
        arguments=arguments,
    )


def test_high_impact_restart_fails_closed_without_matching_approval() -> None:
    environment = IncidentEnvironment(fixture(), run_id="run-1")

    with pytest.raises(ValidationError):
        restart(approval_id=None, operation_id="restart-1")

    assert environment.snapshot().restart_counts["order-worker"] == 0
    assert environment.snapshot().services["order-worker"] == "stuck"


def test_high_impact_feature_flag_fails_closed_without_approval_fields() -> None:
    canary = next(
        item
        for item in validate_incident_corpus().fixtures
        if item.task.task_id == "incident.rollback-checkout-canary"
    )
    environment = IncidentEnvironment(canary, run_id="run-canary")

    with pytest.raises(ValidationError):
        IncidentAction(
            tool_name="set_feature_flag",
            arguments={"key": "checkout.canary", "enabled": False},
        )

    assert environment.snapshot().feature_flags["checkout.canary"] is True
    assert environment.snapshot().feature_flag_mutations["checkout.canary"] == 0


def test_incident_log_observation_redacts_fixed_secret_patterns() -> None:
    payload = fixture().model_dump()
    payload["private"]["logs"]["order-worker"] = [
        "api_key=sk-example password:guess-me Authorization Bearer abc.def",
    ]
    environment = IncidentEnvironment(
        IncidentTaskFixture.model_validate(payload), run_id="run-redaction"
    )

    result = environment.execute(
        IncidentAction(
            tool_name="read_service_logs",
            arguments={"service": "order-worker"},
        )
    )
    serialized = result.model_dump_json()

    assert result.status == "ok"
    assert serialized.count("[REDACTED]") == 3
    assert "sk-example" not in serialized
    assert "guess-me" not in serialized
    assert "abc.def" not in serialized
    assert "sk-example" not in "".join(
        record.model_dump_json() for record in environment.audit
    )


def test_feature_flag_approval_cannot_be_self_authorized_with_changed_value() -> None:
    fixture = next(
        item
        for item in validate_incident_corpus().fixtures
        if item.task.task_id == "incident.rollback-checkout-canary"
    )
    environment = IncidentEnvironment(fixture, run_id="run-canary")
    initial = environment.snapshot()

    request = environment.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "set_feature_flag",
                "target": "checkout.canary",
                "arguments": {"key": "checkout.canary", "enabled": True},
            },
        )
    )
    guessed_approval = environment.execute(
        IncidentAction(
            tool_name="set_feature_flag",
            arguments={
                "key": "checkout.canary",
                "enabled": True,
                "approval_id": "checkout-canary-off",
                "operation_id": "malicious-canary-on",
            },
        )
    )
    acknowledgement = environment.execute(
        IncidentAction(tool_name="acknowledge_incident", arguments={})
    )
    verifier = IncidentStateVerifier().verify(
        initial,
        environment.snapshot(),
        fixture.private.expected,
        list(environment.audit),
        run_id="run-canary",
        task_id=fixture.task.task_id,
    )

    assert request.status == "rejected"
    assert request.error_category == "approval_denied"
    assert guessed_approval.status == "rejected"
    assert guessed_approval.error_category == "approval_mismatch"
    assert acknowledgement.status == "rejected"
    assert verifier.passed is False
    assert environment.snapshot().feature_flags["checkout.canary"] is True
    assert environment.snapshot().feature_flag_mutations["checkout.canary"] == 0


def test_approved_feature_flag_value_cannot_be_changed_at_execution() -> None:
    canary = next(
        item
        for item in validate_incident_corpus().fixtures
        if item.task.task_id == "incident.rollback-checkout-canary"
    )
    environment = IncidentEnvironment(canary, run_id="run-canary")
    approval = environment.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "set_feature_flag",
                "target": "checkout.canary",
                "arguments": {"key": "checkout.canary", "enabled": False},
            },
        )
    )
    assert approval.status == "ok"

    changed = environment.execute(
        IncidentAction(
            tool_name="set_feature_flag",
            arguments={
                "key": "checkout.canary",
                "enabled": True,
                "approval_id": "checkout-canary-off",
                "operation_id": "changed-value",
            },
        )
    )

    assert changed.status == "rejected"
    assert changed.error_category == "approval_mismatch"
    assert environment.snapshot().feature_flags["checkout.canary"] is True
    assert environment.snapshot().feature_flag_mutations["checkout.canary"] == 0


@pytest.mark.parametrize(
    ("tool_name", "target", "arguments"),
    [
        (
            "set_feature_flag",
            "order-worker.safe_mode",
            {"key": "order-worker.safe_mode", "enabled": True},
        ),
        (
            "restart_simulated_service",
            "other-worker",
            {"service": "other-worker"},
        ),
    ],
)
def test_wrong_tool_or_target_approval_request_is_denied_without_mutation(
    tool_name: str, target: str, arguments: dict[str, JsonValue]
) -> None:
    environment = IncidentEnvironment(fixture(), run_id="run-1")

    result = environment.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={"tool_name": tool_name, "target": target, "arguments": arguments},
        )
    )

    assert result.status == "rejected"
    assert result.error_category == "approval_denied"
    assert environment.snapshot().restart_counts["order-worker"] == 0


def test_denied_approval_and_guessed_id_leave_payment_unchanged() -> None:
    payment = next(
        item
        for item in validate_incident_corpus().fixtures
        if item.task.task_id == "incident.escalate-denied-payment-restart"
    )
    environment = IncidentEnvironment(payment, run_id="run-payment")
    denial = environment.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "restart_simulated_service",
                "target": "payment",
                "arguments": {"service": "payment"},
            },
        )
    )
    guessed = environment.execute(
        IncidentAction(
            tool_name="restart_simulated_service",
            arguments={
                "service": "payment",
                "approval_id": "payment-restart",
                "operation_id": "payment-restart-1",
            },
        )
    )

    assert denial.status == "rejected"
    assert denial.error_category == "approval_denied"
    assert guessed.status == "rejected"
    assert environment.snapshot().services["payment"] == "stuck"
    assert environment.snapshot().restart_counts["payment"] == 0


def test_approval_grant_cannot_be_reused_by_another_run() -> None:
    first = IncidentEnvironment(fixture(), run_id="run-1")
    approval = first.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "restart_simulated_service",
                "target": "order-worker",
                "arguments": {"service": "order-worker"},
            },
        )
    )
    approval_id = approval.payload["approval_id"]
    assert isinstance(approval_id, str)
    second = IncidentEnvironment(fixture(), run_id="run-2")

    result = second.execute(restart(approval_id=approval_id, operation_id="cross-run"))

    assert result.status == "rejected"
    assert result.error_category == "approval_mismatch"
    assert second.snapshot().restart_counts["order-worker"] == 0


def test_duplicate_operation_executes_once_and_conflicting_reuse_mutates_nothing() -> None:
    environment = IncidentEnvironment(fixture(), run_id="run-1")
    approval = environment.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "restart_simulated_service",
                "target": "order-worker",
                "arguments": {"service": "order-worker"},
            },
        )
    )
    approval_id = approval.payload["approval_id"]
    assert isinstance(approval_id, str)

    first = environment.execute(restart(approval_id=approval_id, operation_id="restart-1"))
    duplicate = environment.execute(restart(approval_id=approval_id, operation_id="restart-1"))
    conflict = environment.execute(restart(approval_id="approval-other", operation_id="restart-1"))

    assert first.status == "ok"
    assert duplicate.idempotency_hit is True
    assert environment.snapshot().restart_counts["order-worker"] == 1
    assert conflict.status == "rejected"
    assert environment.snapshot().restart_counts["order-worker"] == 1


def test_premature_ack_and_post_terminal_mutation_are_rejected() -> None:
    environment = IncidentEnvironment(fixture(), run_id="run-1")
    early_ack = environment.execute(
        IncidentAction(tool_name="acknowledge_incident", arguments={})
    )
    assert early_ack.status == "rejected"

    approval = environment.execute(
        IncidentAction(
            tool_name="request_approval",
            arguments={
                "tool_name": "restart_simulated_service",
                "target": "order-worker",
                "arguments": {"service": "order-worker"},
            },
        )
    )
    approval_id = approval.payload["approval_id"]
    assert isinstance(approval_id, str)
    assert (
        environment.execute(restart(approval_id=approval_id, operation_id="restart-1")).status
        == "ok"
    )
    assert (
        environment.execute(IncidentAction(tool_name="acknowledge_incident", arguments={})).status
        == "ok"
    )

    after_terminal = environment.execute(
        restart(approval_id=approval_id, operation_id="restart-after-ack")
    )
    escalation = environment.execute(
        IncidentAction(
            tool_name="escalate_incident",
            arguments={"reason_category": "ambiguous_evidence"},
        )
    )

    assert after_terminal.status == "rejected"
    assert after_terminal.error_category == "incident_terminal"
    assert escalation.status == "rejected"
    assert environment.snapshot().restart_counts["order-worker"] == 1
    assert environment.snapshot().incident_status == "acknowledged"
