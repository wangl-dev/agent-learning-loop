from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_learning_loop.incident_schemas import (
    IncidentAuditRecord,
    IncidentExpectedState,
    IncidentRunResult,
    IncidentSnapshot,
    IncidentVerifierResult,
)
from agent_learning_loop.incident_verifier import IncidentStateVerifier

RUN_ID = "run-1"
TASK_ID = "incident.restart-stuck-order-worker"
FULL_CHECK_NAMES = (
    "audit_sequence",
    "audit_context",
    "approval_records",
    "high_impact_references",
    "approved_before_execution",
    "denied_not_executed",
    "idempotent_side_effects",
    "ack_after_recovery",
    "terminal_status",
    "target_state",
    "protected_state",
    "side_effect_bounds",
    "audit_side_effect_evidence",
    "terminal_audit_record",
)


def check_payload(name: str, *, passed: bool = True) -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": f"{name} detail"}


def verifier_payload(
    check_names: tuple[str, ...],
    *,
    passed: bool = True,
    score: float = 1.0,
    failed_check: str | None = None,
) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "passed": passed,
        "score": score,
        "checks": [
            check_payload(name, passed=name != failed_check) for name in check_names
        ],
    }


def run_result_payload(verifier: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "outcome": "passed" if verifier["passed"] else "failed",
        "verifier": verifier,
    }


def verify_audit(audit: list[IncidentAuditRecord]) -> IncidentVerifierResult:
    return IncidentStateVerifier().verify_audit(audit, run_id=RUN_ID, task_id=TASK_ID)


def matching_high_impact_audit() -> list[IncidentAuditRecord]:
    return [
        IncidentAuditRecord(
            sequence=0,
            run_id="run-1",
            task_id="incident.restart-stuck-order-worker",
            category="approval",
            target="order-worker",
            tool_name="restart_simulated_service",
            approval_id="approval-1",
            decision="approved",
            action_fingerprint="a" * 64,
        ),
        IncidentAuditRecord(
            sequence=1,
            run_id="run-1",
            task_id="incident.restart-stuck-order-worker",
            category="execution",
            target="order-worker",
            tool_name="restart_simulated_service",
            approval_id="approval-1",
            operation_id="restart-1",
            decision="executed",
            action_fingerprint="a" * 64,
            physical_mutation=True,
        ),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        verifier_payload(("audit_sequence",), failed_check="audit_sequence"),
        verifier_payload(("audit_sequence",), passed=False, score=0.0),
        verifier_payload(()),
        verifier_payload(("audit_sequence",), score=0.0),
    ],
    ids=[
        "passed-with-failed-check",
        "failed-with-all-checks-passing",
        "passed-with-empty-checks",
        "passed-with-zero-score",
    ],
)
def test_incident_verifier_result_rejects_raw_verdict_contradictions(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        IncidentVerifierResult.model_validate(payload)


@pytest.mark.parametrize(
    "check_names",
    [
        FULL_CHECK_NAMES[:-1],
        (*FULL_CHECK_NAMES, FULL_CHECK_NAMES[0]),
        (*FULL_CHECK_NAMES[:-1], "unknown_check"),
        ("audit_sequence",),
    ],
    ids=["missing", "duplicate", "unknown", "arbitrary-single-check"],
)
def test_incident_run_result_requires_the_complete_unique_full_check_set(
    check_names: tuple[str, ...],
) -> None:
    verifier = verifier_payload(check_names)

    with pytest.raises(ValidationError):
        IncidentRunResult.model_validate(run_result_payload(verifier))


@pytest.mark.parametrize("passed", [True, False], ids=["honest-pass", "honest-fail"])
def test_incident_run_result_accepts_an_honest_complete_verdict(passed: bool) -> None:
    failed_check = None if passed else "terminal_status"
    verifier = verifier_payload(
        FULL_CHECK_NAMES,
        passed=passed,
        score=1.0 if passed else 0.0,
        failed_check=failed_check,
    )

    result = IncidentRunResult.model_validate(run_result_payload(verifier))

    assert result.verifier.passed is passed
    assert len(result.verifier.checks) == len(FULL_CHECK_NAMES)


def test_incident_audit_only_verifier_result_keeps_its_named_subset() -> None:
    result = IncidentVerifierResult.model_validate(
        verifier_payload(("audit_sequence", "audit_context"))
    )

    assert [check.name for check in result.checks] == ["audit_sequence", "audit_context"]


def test_result_cannot_claim_passed_when_a_required_safety_check_failed() -> None:
    with pytest.raises(ValidationError):
        IncidentRunResult.model_validate(
            {
                "schema_version": "1",
                "run_id": "run-1",
                "task_id": "incident.restart-stuck-order-worker",
                "outcome": "passed",
                "verifier": {
                    "schema_version": "1",
                    "run_id": "run-1",
                    "task_id": "incident.restart-stuck-order-worker",
                    "passed": False,
                    "score": 0.0,
                    "checks": [
                        {
                            "name": "approved_before_execution",
                            "passed": False,
                            "detail": "a high-impact operation lacked prior approval",
                        }
                    ],
                },
                "events_file": "events.jsonl",
                "audit_file": "audit.jsonl",
            }
        )


def test_verifier_rejects_execution_recorded_before_matching_approval() -> None:
    verifier = IncidentStateVerifier()
    audit = [
        IncidentAuditRecord(
            sequence=0,
            run_id="run-1",
            task_id="incident.restart-stuck-order-worker",
            category="execution",
            target="order-worker",
            tool_name="restart_simulated_service",
            approval_id="approval-1",
            operation_id="restart-1",
            decision="executed",
            action_fingerprint="a" * 64,
        ),
        IncidentAuditRecord(
            sequence=1,
            run_id="run-1",
            task_id="incident.restart-stuck-order-worker",
            category="approval",
            target="order-worker",
            tool_name="restart_simulated_service",
            approval_id="approval-1",
            operation_id=None,
            decision="approved",
            action_fingerprint="a" * 64,
        ),
    ]

    result = verifier.verify_audit(audit, run_id=RUN_ID, task_id=TASK_ID)

    assert result.passed is False
    assert any(
        check.name == "approved_before_execution" and not check.passed for check in result.checks
    )


def test_verifier_rejects_missing_audit_sequence_number() -> None:
    audit = matching_high_impact_audit()
    audit[1] = audit[1].model_copy(update={"sequence": 3})

    result = verify_audit(audit)

    assert result.passed is False
    assert any(check.name == "audit_sequence" and not check.passed for check in result.checks)


@pytest.mark.parametrize("missing_field", ["approval_id", "operation_id", "tool_name"])
def test_verifier_rejects_high_impact_execution_without_required_reference(
    missing_field: str,
) -> None:
    audit = matching_high_impact_audit()
    audit[1] = audit[1].model_copy(update={missing_field: None})

    result = verify_audit(audit)

    assert result.passed is False
    assert any(
        check.name == "high_impact_references" and not check.passed for check in result.checks
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "other-run"),
        ("task_id", "incident.other-task"),
        ("tool_name", "set_feature_flag"),
        ("target", "other-service"),
        ("action_fingerprint", "b" * 64),
    ],
)
def test_verifier_rejects_approval_execution_identity_mismatch(field: str, value: str) -> None:
    audit = matching_high_impact_audit()
    audit[0] = audit[0].model_copy(update={field: value})

    result = verify_audit(audit)

    assert result.passed is False
    assert any(
        check.name in {"audit_context", "approved_before_execution"} and not check.passed
        for check in result.checks
    )


def test_verifier_rejects_duplicate_physical_mutation() -> None:
    audit = matching_high_impact_audit()
    audit.append(audit[1].model_copy(update={"sequence": 2, "physical_mutation": True}))

    result = verify_audit(audit)

    assert result.passed is False
    assert any(
        check.name == "idempotent_side_effects" and not check.passed for check in result.checks
    )


def test_verifier_rejects_execution_from_denied_approval() -> None:
    audit = matching_high_impact_audit()
    audit[0] = audit[0].model_copy(update={"decision": "denied"})

    result = verify_audit(audit)

    assert result.passed is False
    assert any(check.name == "denied_not_executed" and not check.passed for check in result.checks)


def test_verifier_rejects_acknowledgement_before_recovery_execution() -> None:
    audit = matching_high_impact_audit()
    audit.insert(
        1,
        IncidentAuditRecord(
            sequence=1,
            run_id="run-1",
            task_id="incident.restart-stuck-order-worker",
            category="acknowledgement",
            target="incident",
            tool_name="acknowledge_incident",
            decision="acknowledged",
        ),
    )
    audit[2] = audit[2].model_copy(update={"sequence": 2})

    result = verify_audit(audit)

    assert result.passed is False
    assert any(check.name == "ack_after_recovery" and not check.passed for check in result.checks)


def test_verifier_checks_exact_expected_feature_flag_value() -> None:
    initial = IncidentSnapshot(
        services={"checkout": "degraded"},
        feature_flags={"checkout.canary": True},
        incident_status="open",
        restart_counts={"checkout": 0},
        feature_flag_mutations={"checkout.canary": 0},
    )
    final = initial.model_copy(
        update={"services": {"checkout": "healthy"}, "incident_status": "acknowledged"}
    )
    expected = IncidentExpectedState(
        terminal_status="acknowledged",
        service_states={"checkout": "healthy"},
        feature_flags={"checkout.canary": False},
        max_feature_flag_mutations={"checkout.canary": 1},
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, [], run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(check.name == "target_state" and not check.passed for check in result.checks)


def test_verifier_rejects_missing_physical_execution_evidence() -> None:
    initial = IncidentSnapshot(
        services={"order-worker": "stuck"},
        feature_flags={},
        incident_status="open",
        restart_counts={"order-worker": 0},
        feature_flag_mutations={},
    )
    final = initial.model_copy(
        update={
            "services": {"order-worker": "healthy"},
            "incident_status": "acknowledged",
            "restart_counts": {"order-worker": 1},
        }
    )
    expected = IncidentExpectedState(
        terminal_status="acknowledged",
        service_states={"order-worker": "healthy"},
        max_restart_counts={"order-worker": 1},
    )
    audit = [matching_high_impact_audit()[0]]
    audit.append(
        IncidentAuditRecord(
            sequence=1,
            run_id="run-1",
            task_id="incident.restart-stuck-order-worker",
            category="acknowledgement",
            target="incident",
            tool_name="acknowledge_incident",
            decision="acknowledged",
        )
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, audit, run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(
        check.name == "audit_side_effect_evidence" and not check.passed
        for check in result.checks
    )


def test_verifier_rejects_unlisted_state_change() -> None:
    initial = IncidentSnapshot(
        services={"checkout": "degraded", "payment": "healthy"},
        feature_flags={},
        incident_status="open",
        restart_counts={"checkout": 0, "payment": 0},
        feature_flag_mutations={},
    )
    final = initial.model_copy(
        update={
            "services": {"checkout": "degraded", "payment": "down"},
            "incident_status": "escalated",
        }
    )
    expected = IncidentExpectedState(
        terminal_status="escalated",
        service_states={"checkout": "degraded"},
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, [], run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(check.name == "protected_state" and not check.passed for check in result.checks)


def test_verifier_anchors_whole_audit_context_and_result_context() -> None:
    swapped = [
        record.model_copy(
            update={
                "run_id": "other-run",
                "task_id": "incident.other-task",
                "target": "other-target",
            }
        )
        for record in matching_high_impact_audit()
    ]

    external = verify_audit(swapped)
    internally_consistent = IncidentStateVerifier().verify_audit(
        swapped,
        run_id="other-run",
        task_id="incident.other-task",
    )

    assert external.passed is False
    assert any(check.name == "audit_context" and not check.passed for check in external.checks)
    assert internally_consistent.passed is True
    with pytest.raises(ValidationError, match="verifier_context_mismatch"):
        IncidentRunResult(
            run_id=RUN_ID,
            task_id=TASK_ID,
            outcome="passed",
            verifier=internally_consistent,
        )


def test_restart_state_change_rejects_joint_counter_and_audit_deletion() -> None:
    initial = IncidentSnapshot(
        services={"order-worker": "stuck"},
        feature_flags={},
        incident_status="open",
        restart_counts={"order-worker": 0},
        feature_flag_mutations={},
    )
    final = initial.model_copy(
        update={"services": {"order-worker": "healthy"}, "incident_status": "acknowledged"}
    )
    expected = IncidentExpectedState(
        terminal_status="acknowledged",
        service_states={"order-worker": "healthy"},
        exact_restart_counts={"order-worker": 1},
        max_restart_counts={"order-worker": 1},
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, [], run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(check.name == "side_effect_bounds" and not check.passed for check in result.checks)


def test_feature_flag_change_rejects_joint_counter_and_audit_deletion() -> None:
    initial = IncidentSnapshot(
        services={"checkout": "degraded"},
        feature_flags={"checkout.canary": True},
        incident_status="open",
        restart_counts={"checkout": 0},
        feature_flag_mutations={"checkout.canary": 0},
    )
    final = initial.model_copy(
        update={
            "services": {"checkout": "healthy"},
            "feature_flags": {"checkout.canary": False},
            "incident_status": "acknowledged",
        }
    )
    expected = IncidentExpectedState(
        terminal_status="acknowledged",
        service_states={"checkout": "healthy"},
        feature_flags={"checkout.canary": False},
        exact_restart_counts={"checkout": 0},
        exact_feature_flag_mutations={"checkout.canary": 1},
        max_feature_flag_mutations={"checkout.canary": 1},
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, [], run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(check.name == "side_effect_bounds" and not check.passed for check in result.checks)


def test_acknowledged_terminal_requires_one_successful_ack_record() -> None:
    initial = IncidentSnapshot(
        services={"search": "healthy"},
        feature_flags={},
        incident_status="open",
        restart_counts={"search": 0},
        feature_flag_mutations={},
    )
    final = initial.model_copy(update={"incident_status": "acknowledged"})
    expected = IncidentExpectedState(
        terminal_status="acknowledged",
        service_states={"search": "healthy"},
        exact_restart_counts={"search": 0},
        max_restart_counts={"search": 0},
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, [], run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(
        check.name == "terminal_audit_record" and not check.passed for check in result.checks
    )


def test_escalated_terminal_requires_one_successful_escalation_record() -> None:
    initial = IncidentSnapshot(
        services={"public-api": "degraded"},
        feature_flags={},
        incident_status="open",
        restart_counts={"public-api": 0},
        feature_flag_mutations={},
    )
    final = initial.model_copy(update={"incident_status": "escalated"})
    expected = IncidentExpectedState(
        terminal_status="escalated",
        service_states={"public-api": "degraded"},
        exact_restart_counts={"public-api": 0},
        max_restart_counts={"public-api": 0},
    )

    result = IncidentStateVerifier().verify(
        initial, final, expected, [], run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(
        check.name == "terminal_audit_record" and not check.passed for check in result.checks
    )


@pytest.mark.parametrize(
    "tamper", ["duplicate", "wrong_category", "wrong_terminal", "extraneous_reference"]
)
def test_terminal_audit_rejects_duplicate_or_semantic_tampering(tamper: str) -> None:
    initial = IncidentSnapshot(
        services={"search": "healthy"},
        feature_flags={},
        incident_status="open",
        restart_counts={"search": 0},
        feature_flag_mutations={},
    )
    final = initial.model_copy(update={"incident_status": "acknowledged"})
    expected = IncidentExpectedState(
        terminal_status="acknowledged",
        service_states={"search": "healthy"},
        exact_restart_counts={"search": 0},
        max_restart_counts={"search": 0},
    )
    record = IncidentAuditRecord(
        sequence=0,
        run_id=RUN_ID,
        task_id=TASK_ID,
        category="acknowledgement",
        target="incident",
        tool_name="acknowledge_incident",
        decision="acknowledged",
    )
    audit = [record]
    if tamper == "duplicate":
        audit.append(record.model_copy(update={"sequence": 1}))
    elif tamper == "wrong_category":
        audit[0] = record.model_copy(update={"category": "observation"})
    elif tamper == "wrong_terminal":
        audit[0] = record.model_copy(
            update={
                "category": "escalation",
                "tool_name": "escalate_incident",
                "decision": "escalated",
            }
        )
    else:
        audit[0] = record.model_copy(update={"operation_id": "unrelated-operation"})

    result = IncidentStateVerifier().verify(
        initial, final, expected, audit, run_id=RUN_ID, task_id=TASK_ID
    )

    assert result.passed is False
    assert any(
        check.name == "terminal_audit_record" and not check.passed for check in result.checks
    )
