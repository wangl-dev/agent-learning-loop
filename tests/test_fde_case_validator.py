from __future__ import annotations

from agent_learning_loop.fde_case_manifest import build_case_acceptance, load_fde_case
from agent_learning_loop.fde_case_schemas import FdeCaseCellEvidence
from agent_learning_loop.incident_schemas import INCIDENT_FULL_CHECK_NAMES


def _honest_evidence() -> list[FdeCaseCellEvidence]:
    case = load_fde_case("incident-copilot-v1")
    return [
        FdeCaseCellEvidence(
            cell_id=cell.cell_id,
            task_id=cell.task_id,
            split=cell.split,
            cell_contract_passed=True,
            incident_safety_success=True,
            full_check_names=list(INCIDENT_FULL_CHECK_NAMES),
            unauthorized_high_impact_execution_ids=[],
        )
        for cell in case.cells
    ]


def test_acceptance_is_exact_for_all_ten_registered_cells() -> None:
    case = load_fde_case("incident-copilot-v1")
    acceptance = build_case_acceptance(case, _honest_evidence())

    assert acceptance.registered_contracts.model_dump() == {
        "numerator": 10,
        "denominator": 10,
        "passed_ids": [cell.cell_id for cell in case.cells],
        "failed_ids": [],
    }
    assert acceptance.held_out_contracts.numerator == 4
    assert acceptance.held_out_contracts.denominator == 4
    assert acceptance.control_groups.numerator == 3
    assert acceptance.control_groups.denominator == 3
    assert acceptance.incident_safety.numerator == 10
    assert acceptance.incident_safety.denominator == 10
    assert acceptance.unauthorized_high_impact_executions.count == 0
    assert acceptance.overall == "accepted"
    assert {
        acceptance.real_customer_adoption,
        acceptance.manual_baseline_time,
        acceptance.roi,
        acceptance.sla,
        acceptance.production_latency,
        acceptance.model_performance,
    } == {"N/A"}


def test_one_held_out_safety_drift_hits_every_relevant_denominator() -> None:
    case = load_fde_case("incident-copilot-v1")
    evidence = _honest_evidence()
    target = next(
        item
        for item in evidence
        if item.cell_id == "system.incident.escalate-ambiguous-api-errors"
    )
    evidence[evidence.index(target)] = target.model_copy(
        update={"cell_contract_passed": False, "incident_safety_success": False}
    )

    acceptance = build_case_acceptance(case, evidence)

    assert acceptance.registered_contracts.numerator == 9
    assert acceptance.held_out_contracts.numerator == 3
    assert acceptance.control_groups.numerator == 2
    assert acceptance.incident_safety.numerator == 9
    assert acceptance.overall == "drifted"
