from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_learning_loop.eval_aggregation import aggregate_eval_records
from agent_learning_loop.eval_schemas import (
    EvalComparison,
    ExactRatio,
    NormalizedEvalRecord,
)


def record(
    cell_id: str,
    *,
    environment: str,
    split: str | None,
    verifier: bool,
    completion: bool | str,
    contract: bool = True,
    pair_id: str | None = None,
    arm: str | None = None,
    duplicates: int | None = None,
    idempotency_hits: int | None = None,
) -> NormalizedEvalRecord:
    kind = "reliability" if completion != "N/A" else "system"
    runtime_metric = 0 if kind == "reliability" else None
    is_incident_system = kind == "system" and environment == "incident"
    is_dataops_system = kind == "system" and environment == "dataops"
    return NormalizedEvalRecord.model_validate(
        {
        "kind": kind,
        "suite_id": (
            "runtime-reliability-v1" if kind == "reliability" else "system-correctness-v1"
        ),
        "cell_id": cell_id,
        "pair_id": pair_id,
        "arm": arm,
        "source_commit": "6" * 40,
        "environment": environment,
        "task_id": f"{environment}.{cell_id}",
        "split": split,
        "tags": ["hand-calculated"],
        "seed": 1,
        "resource_id": f"{cell_id}.resource.v1",
        "resource_fingerprint": "a" * 64,
        "schedule_id": "workspace.test.v1" if kind == "reliability" else None,
        "schedule_fingerprint": "d" * 64 if kind == "reliability" else None,
        "config_fingerprint": "b" * 64,
        "raw_result_path": f"runs/{cell_id}/result.json",
        "raw_result_sha256": "c" * 64,
        "cell_contract_passed": contract,
        "verifier_state_success": verifier,
        "runtime_completion_success": completion,
        "terminal": "SUCCEEDED" if completion is True else "FAILED",
        "error_category": None if completion is True else "expected_failure",
        "steps": runtime_metric,
        "tool_calls": runtime_metric,
        "physical_executions": runtime_metric,
        "physical_write_executions": runtime_metric,
        "side_effect_executions": runtime_metric,
        "duplicate_side_effects": duplicates,
        "retries": runtime_metric,
        "idempotency_hits": idempotency_hits,
        "dataops_attempted": 1 if is_dataops_system else None,
        "dataops_committed": 1 if is_dataops_system else None,
        "incident_terminal": "acknowledged" if is_incident_system else None,
        "incident_safety_success": verifier if is_incident_system else None,
        "diagnostic": None,
        }
    )


def test_hand_calculated_four_record_oracle_matches_3_4_and_2_4() -> None:
    records = [
        record(
            "pair.baseline",
            environment="workspace",
            split=None,
            verifier=True,
            completion=False,
            pair_id="one-mechanism",
            arm="baseline",
            duplicates=1,
            idempotency_hits=0,
        ),
        record(
            "pair.mechanism",
            environment="workspace",
            split=None,
            verifier=True,
            completion=True,
            pair_id="one-mechanism",
            arm="mechanism",
            duplicates=0,
            idempotency_hits=1,
        ),
        record(
            "incident.expected-naive",
            environment="incident",
            split=None,
            verifier=False,
            completion=False,
            contract=True,
            duplicates=0,
            idempotency_hits=0,
        ),
        record(
            "dataops.pass",
            environment="dataops",
            split=None,
            verifier=True,
            completion=True,
            duplicates=0,
            idempotency_hits=0,
        ),
    ]
    comparison = EvalComparison(
        comparison_id="one-mechanism",
        baseline_cell_id="pair.baseline",
        mechanism_cell_id="pair.mechanism",
        allowed_config_differences=["idempotency_enabled"],
    )

    summary = aggregate_eval_records(records, [comparison])

    assert summary.verifier_state_success == ExactRatio(numerator=3, denominator=4, rate=0.75)
    assert summary.runtime_completion_success == ExactRatio(
        numerator=2, denominator=4, rate=0.5
    )
    assert records[2].cell_contract_passed is True
    assert summary.pair_deltas[0].completion_delta == 1
    assert summary.pair_deltas[0].duplicate_side_effect_delta == -1
    assert summary.pair_deltas[0].idempotency_hit_delta == 1


def test_rates_missing_arms_and_duplicate_cells_fail_closed() -> None:
    with pytest.raises(ValidationError):
        ExactRatio(numerator=0, denominator=0, rate=0.0)
    with pytest.raises(ValidationError):
        ExactRatio(numerator=1, denominator=2, rate=0.75)

    baseline = record(
        "pair.baseline",
        environment="workspace",
        split=None,
        verifier=True,
        completion=False,
        pair_id="one-mechanism",
        arm="baseline",
        duplicates=1,
        idempotency_hits=0,
    )
    comparison = EvalComparison(
        comparison_id="one-mechanism",
        baseline_cell_id="pair.baseline",
        mechanism_cell_id="pair.mechanism",
        allowed_config_differences=["idempotency_enabled"],
    )
    with pytest.raises(ValueError, match="incomplete_pair"):
        aggregate_eval_records([baseline], [comparison])
    with pytest.raises(ValueError, match="duplicate_record_cell"):
        aggregate_eval_records([baseline, baseline], [])


def test_system_runtime_completion_is_strict_na_not_numeric_zero() -> None:
    payload = record(
        "workspace.pass",
        environment="workspace",
        split="train",
        verifier=True,
        completion="N/A",
    ).model_dump(mode="json")
    payload["runtime_completion_success"] = 0

    with pytest.raises(ValidationError):
        NormalizedEvalRecord.model_validate(payload)


def test_hand_calculated_system_slices_keep_two_environments_and_splits() -> None:
    records = [
        record(
            "workspace.train-one",
            environment="workspace",
            split="train",
            verifier=True,
            completion="N/A",
        ),
        record(
            "workspace.test-one",
            environment="workspace",
            split="test",
            verifier=False,
            completion="N/A",
            contract=False,
        ),
        record(
            "incident.train-one",
            environment="incident",
            split="train",
            verifier=True,
            completion="N/A",
        ),
        record(
            "incident.test-one",
            environment="incident",
            split="test",
            verifier=True,
            completion="N/A",
        ),
    ]

    summary = aggregate_eval_records(records, [])
    slices = {
        (item.dimension, item.value): (item.verifier_passed, item.selected)
        for item in summary.system_slices
    }

    assert slices[("environment", "workspace")] == (1, 2)
    assert slices[("environment", "incident")] == (2, 2)
    assert slices[("split", "train")] == (2, 2)
    assert slices[("split", "test")] == (1, 2)
