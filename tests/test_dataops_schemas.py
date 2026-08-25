from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_learning_loop.dataops_schemas import (
    DATAOPS_FULL_CHECK_NAMES,
    DataOpsAction,
    DataOpsRunResult,
    DataOpsVerifierResult,
)

STRICT_ACTIONS = (
    ("describe_table", {"table": "orders"}),
    (
        "query_rows",
        {"table": "orders", "columns": ["id", "status"], "where": {}, "limit": 10},
    ),
    ("begin_transaction", {"transaction_id": "tx-1"}),
    (
        "update_rows",
        {
            "transaction_id": "tx-1",
            "operation_id": "op-1",
            "table": "orders",
            "where": {"id": 1},
            "values": {"status": "ready"},
            "expected_match_count": 1,
        },
    ),
    (
        "insert_row",
        {
            "transaction_id": "tx-1",
            "operation_id": "op-2",
            "table": "orders",
            "row": {"id": 2, "status": "ready", "tenant_id": "tenant-a"},
        },
    ),
    ("validate_constraints", {"transaction_id": "tx-1"}),
    ("commit_transaction", {"transaction_id": "tx-1"}),
    (
        "rollback_transaction",
        {"transaction_id": "tx-1", "reason_category": "ambiguous_match"},
    ),
)


@pytest.mark.parametrize(("tool_name", "arguments"), STRICT_ACTIONS)
def test_each_dataops_tool_accepts_only_its_exact_strict_arguments(
    tool_name: str, arguments: dict[str, object]
) -> None:
    action = DataOpsAction.model_validate(
        {"schema_version": "1", "tool_name": tool_name, "arguments": arguments}
    )
    assert action.tool_name == tool_name

    with pytest.raises(ValidationError):
        DataOpsAction.model_validate(
            {
                "schema_version": "1",
                "tool_name": tool_name,
                "arguments": {**arguments, "raw_sql": "UPDATE orders SET status='bad'"},
            }
        )
    first_field = next(iter(arguments))
    missing = dict(arguments)
    del missing[first_field]
    with pytest.raises(ValidationError):
        DataOpsAction.model_validate(
            {"schema_version": "1", "tool_name": tool_name, "arguments": missing}
        )
    wrong_type = {**arguments, first_field: None}
    with pytest.raises(ValidationError):
        DataOpsAction.model_validate(
            {"schema_version": "1", "tool_name": tool_name, "arguments": wrong_type}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "update_rows", "arguments": STRICT_ACTIONS[3][1] | {"where": {}}},
        {
            "tool_name": "update_rows",
            "arguments": STRICT_ACTIONS[3][1] | {"expected_match_count": 0},
        },
        {
            "tool_name": "query_rows",
            "arguments": STRICT_ACTIONS[1][1] | {"limit": 0},
        },
    ],
)
def test_action_schema_rejects_unsafe_cardinality_and_query_bounds(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DataOpsAction.model_validate({"schema_version": "1", **payload})


@pytest.mark.parametrize(
    "unsafe_value",
    ["ready; DROP TABLE orders", r"C:\private\customer.db", "/var/data/customer.sqlite"],
)
def test_action_schema_rejects_sql_and_database_path_like_values(
    unsafe_value: str,
) -> None:
    payload = dict(STRICT_ACTIONS[3][1])
    payload["values"] = {"status": unsafe_value}

    with pytest.raises(ValidationError):
        DataOpsAction.model_validate({"tool_name": "update_rows", "arguments": payload})


def _checks(*, failed: str | None = None) -> list[dict[str, object]]:
    return [
        {"name": name, "passed": name != failed, "detail": name}
        for name in DATAOPS_FULL_CHECK_NAMES
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"passed": True, "score": 1.0, "checks": _checks(failed="target_final")},
        {"passed": False, "score": 0.0, "checks": _checks()},
        {"passed": True, "score": 1.0, "checks": []},
        {"passed": True, "score": 0.0, "checks": _checks()},
    ],
)
def test_dataops_verifier_result_rejects_contradictory_verdicts(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DataOpsVerifierResult.model_validate(
            {"run_id": "run-1", "task_id": "dataops.correct-order-status", **payload}
        )


@pytest.mark.parametrize(
    "names",
    [
        DATAOPS_FULL_CHECK_NAMES[:-1],
        (*DATAOPS_FULL_CHECK_NAMES, DATAOPS_FULL_CHECK_NAMES[0]),
        (*DATAOPS_FULL_CHECK_NAMES[:-1], "unknown"),
        (DATAOPS_FULL_CHECK_NAMES[0],),
    ],
)
def test_dataops_run_result_requires_the_complete_unique_full_check_set(
    names: tuple[str, ...],
) -> None:
    verifier = {
        "run_id": "run-1",
        "task_id": "dataops.correct-order-status",
        "passed": True,
        "score": 1.0,
        "checks": [{"name": name, "passed": True, "detail": name} for name in names],
        "terminal_state": "committed",
        "attempted_row_count": 1,
        "committed_row_count": 1,
        "split": "train",
    }
    with pytest.raises(ValidationError):
        DataOpsRunResult.model_validate(
            {
                "run_id": "run-1",
                "task_id": "dataops.correct-order-status",
                "split": "train",
                "outcome": "passed",
                "terminal_state": "committed",
                "attempted_row_count": 1,
                "committed_row_count": 1,
                "verifier": verifier,
            }
        )


@pytest.mark.parametrize("passed", [True, False], ids=["honest-pass", "honest-fail"])
def test_dataops_run_result_accepts_honest_full_verdict(passed: bool) -> None:
    failed = None if passed else "target_final"
    verifier = {
        "run_id": "run-1",
        "task_id": "dataops.correct-order-status",
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "checks": _checks(failed=failed),
        "terminal_state": "committed",
        "attempted_row_count": 1,
        "committed_row_count": 1,
        "split": "train",
    }
    result = DataOpsRunResult.model_validate(
        {
            "run_id": "run-1",
            "task_id": "dataops.correct-order-status",
            "split": "train",
            "outcome": "passed" if passed else "failed",
            "terminal_state": "committed",
            "attempted_row_count": 1,
            "committed_row_count": 1,
            "verifier": verifier,
        }
    )

    assert result.verifier.passed is passed


def test_dataops_run_result_rejects_foreign_verifier_context() -> None:
    verifier = {
        "run_id": "run-foreign",
        "task_id": "dataops.correct-order-status",
        "passed": True,
        "score": 1.0,
        "checks": _checks(),
        "terminal_state": "committed",
        "attempted_row_count": 1,
        "committed_row_count": 1,
        "split": "train",
    }
    with pytest.raises(ValidationError):
        DataOpsRunResult.model_validate(
            {
                "run_id": "run-1",
                "task_id": "dataops.correct-order-status",
                "split": "train",
                "outcome": "passed",
                "terminal_state": "committed",
                "attempted_row_count": 1,
                "committed_row_count": 1,
                "verifier": verifier,
            }
        )


def test_dataops_run_result_rejects_passed_verifier_with_forged_terminal_and_effects() -> None:
    verifier = {
        "run_id": "run-1",
        "task_id": "dataops.correct-order-status",
        "passed": True,
        "score": 1.0,
        "checks": _checks(),
        "terminal_state": "committed",
        "attempted_row_count": 1,
        "committed_row_count": 1,
        "split": "train",
    }

    with pytest.raises(ValidationError):
        DataOpsRunResult.model_validate(
            {
                "run_id": "run-1",
                "task_id": "dataops.correct-order-status",
                "split": "train",
                "outcome": "passed",
                "terminal_state": "rolled_back",
                "attempted_row_count": 999,
                "committed_row_count": 999,
                "verifier": verifier,
            }
        )


def test_dataops_run_result_rejects_trusted_train_split_tampered_to_test() -> None:
    verifier = {
        "run_id": "run-1",
        "task_id": "dataops.correct-order-status",
        "passed": True,
        "score": 1.0,
        "checks": _checks(),
        "terminal_state": "committed",
        "attempted_row_count": 1,
        "committed_row_count": 1,
        "split": "train",
    }

    with pytest.raises(ValidationError):
        DataOpsRunResult.model_validate(
            {
                "run_id": "run-1",
                "task_id": "dataops.correct-order-status",
                "split": "test",
                "outcome": "passed",
                "terminal_state": "committed",
                "attempted_row_count": 1,
                "committed_row_count": 1,
                "verifier": verifier,
            }
        )
