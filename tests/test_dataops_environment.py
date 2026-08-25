from __future__ import annotations

from agent_learning_loop.dataops_environment import DataOpsEnvironment
from agent_learning_loop.dataops_schemas import DataOpsAction, DataOpsTaskFixture


def fixture() -> DataOpsTaskFixture:
    return DataOpsTaskFixture.model_validate(
        {
            "task": {
                "task_id": "dataops.correct-order-status",
                "instruction": "Set order 1 to ready without changing another tenant.",
                "allowed_tools": [
                    "describe_table",
                    "query_rows",
                    "begin_transaction",
                    "update_rows",
                    "validate_constraints",
                    "commit_transaction",
                    "rollback_transaction",
                ],
                "fixture_id": "dataops.correct-order-status.v1",
                "scope": [
                    {
                        "table": "orders",
                        "readable_columns": ["id", "tenant_id", "status"],
                        "mutable_columns": ["status"],
                        "predicate_columns": ["id", "tenant_id", "status"],
                        "max_mutated_rows": 1,
                    }
                ],
                "public_constraints": ["orders.status is non-empty"],
            },
            "private": {
                "tables": [
                    {
                        "name": "orders",
                        "columns": [
                            {"name": "id", "type": "integer", "primary_key": True},
                            {"name": "tenant_id", "type": "text", "not_null": True},
                            {"name": "status", "type": "text", "not_null": True},
                        ],
                        "rows": [
                            {"id": 1, "tenant_id": "tenant-a", "status": "pending"},
                            {"id": 2, "tenant_id": "tenant-b", "status": "pending"},
                        ],
                    }
                ],
                "expected": {
                    "terminal_state": "committed",
                    "tables": {
                        "orders": [
                            {"id": 1, "tenant_id": "tenant-a", "status": "ready"},
                            {"id": 2, "tenant_id": "tenant-b", "status": "pending"},
                        ]
                    },
                    "exact_attempted_row_count": 1,
                    "exact_committed_row_count": 1,
                    "exact_attempted_by_table": {"orders": 1},
                    "exact_committed_by_table": {"orders": 1},
                    "exact_attempted_by_operation": {"op-1": 1},
                    "exact_committed_by_operation": {"op-1": 1},
                    "protected_rows": [{"table": "orders", "where": {"id": 2}}],
                },
            },
        }
    )


def action(tool_name: str, **arguments: object) -> DataOpsAction:
    return DataOpsAction.model_validate({"tool_name": tool_name, "arguments": arguments})


def update(*, expected: int = 1, operation_id: str = "op-1") -> DataOpsAction:
    return action(
        "update_rows",
        transaction_id="tx-1",
        operation_id=operation_id,
        table="orders",
        where={"id": 1},
        values={"status": "ready"},
        expected_match_count=expected,
    )


def test_commit_vertical_slice_has_exact_attempted_and_committed_effects() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-commit")

    assert environment.execute(action("begin_transaction", transaction_id="tx-1")).status == "ok"
    assert environment.execute(update()).status == "ok"
    assert environment.execute(action("validate_constraints", transaction_id="tx-1")).status == "ok"
    assert environment.execute(action("commit_transaction", transaction_id="tx-1")).status == "ok"

    snapshot = environment.snapshot()
    assert snapshot.terminal_state == "committed"
    assert snapshot.attempted_row_count == 1
    assert snapshot.committed_row_count == 1
    assert snapshot.tables["orders"][0]["status"] == "ready"


def test_rollback_vertical_slice_restores_digest_and_commits_nothing() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-rollback")
    initial = environment.snapshot()

    environment.execute(action("begin_transaction", transaction_id="tx-1"))
    environment.execute(update())
    result = environment.execute(
        action(
            "rollback_transaction",
            transaction_id="tx-1",
            reason_category="ambiguous_match",
        )
    )

    snapshot = environment.snapshot()
    assert result.status == "ok"
    assert snapshot.terminal_state == "rolled_back"
    assert snapshot.database_digest == initial.database_digest
    assert snapshot.attempted_row_count == 1
    assert snapshot.committed_row_count == 0


def test_transactionless_and_cardinality_mismatch_are_zero_mutation() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-reject")
    initial = environment.snapshot()

    transactionless = environment.execute(update())
    environment.execute(action("begin_transaction", transaction_id="tx-1"))
    mismatch = environment.execute(update(expected=2, operation_id="op-cardinality"))

    snapshot = environment.snapshot()
    assert transactionless.status == "rejected"
    assert transactionless.error_category == "transaction_required"
    assert mismatch.status == "rejected"
    assert mismatch.error_category == "cardinality_mismatch"
    assert snapshot.database_digest == initial.database_digest
    assert snapshot.attempted_row_count == 0


def test_duplicate_operation_is_one_effect_and_conflict_is_zero_additional_effect() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-idempotency")
    environment.execute(action("begin_transaction", transaction_id="tx-1"))

    first = environment.execute(update())
    duplicate = environment.execute(update())
    conflict = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-1",
            table="orders",
            where={"id": 1},
            values={"status": "shipped"},
            expected_match_count=1,
        )
    )

    assert first.status == "ok"
    assert duplicate.idempotency_hit is True
    assert conflict.status == "rejected"
    assert conflict.error_category == "operation_conflict"
    assert environment.snapshot().attempted_row_count == 1


def test_scope_escape_and_identifier_injection_are_zero_mutation() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-scope")
    initial = environment.snapshot()
    environment.execute(action("begin_transaction", transaction_id="tx-1"))

    table_escape = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-table-escape",
            table="orders; drop table orders",
            where={"id": 1},
            values={"status": "bad"},
            expected_match_count=1,
        )
    )
    column_escape = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-column-escape",
            table="orders",
            where={"id": 1},
            values={"tenant_id": "tenant-b"},
            expected_match_count=1,
        )
    )

    assert table_escape.error_category == "scope_violation"
    assert column_escape.error_category == "scope_violation"
    assert environment.snapshot().database_digest == initial.database_digest
    assert environment.snapshot().attempted_row_count == 0


def test_broad_actual_match_is_rejected_before_any_row_is_written() -> None:
    payload = fixture().model_dump(mode="json")
    payload["private"]["tables"][0]["rows"][1]["tenant_id"] = "tenant-a"
    payload["private"]["expected"]["tables"]["orders"][1]["tenant_id"] = "tenant-a"
    environment = DataOpsEnvironment(DataOpsTaskFixture.model_validate(payload), run_id="run-broad")
    initial = environment.snapshot()
    environment.execute(action("begin_transaction", transaction_id="tx-1"))

    result = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-broad",
            table="orders",
            where={"tenant_id": "tenant-a"},
            values={"status": "ready"},
            expected_match_count=1,
        )
    )

    assert result.error_category == "cardinality_mismatch"
    assert result.payload == {}
    assert environment.snapshot().database_digest == initial.database_digest
    assert environment.snapshot().attempted_row_count == 0
    assert environment.audit[-1].matched_row_count == 2
    assert environment.audit[-1].cardinality_checked_before_write is True


def test_mutation_after_validation_invalidates_it_and_terminal_rejects_more_work() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-state")
    environment.execute(action("begin_transaction", transaction_id="tx-1"))
    environment.execute(update())
    environment.execute(action("validate_constraints", transaction_id="tx-1"))
    second = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-2",
            table="orders",
            where={"id": 1},
            values={"status": "ready-again"},
            expected_match_count=1,
        )
    )
    commit = environment.execute(action("commit_transaction", transaction_id="tx-1"))
    rollback = environment.execute(
        action(
            "rollback_transaction",
            transaction_id="tx-1",
            reason_category="validation_failed",
        )
    )
    after_terminal = environment.execute(action("validate_constraints", transaction_id="tx-1"))

    assert second.status == "ok"
    assert commit.error_category == "validation_required"
    assert rollback.status == "ok"
    assert after_terminal.error_category == "transaction_terminal"
    assert environment.snapshot().committed_row_count == 0


def test_validation_rejects_protected_row_change_before_commit() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-protected")
    environment.execute(action("begin_transaction", transaction_id="tx-1"))
    changed = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-protected",
            table="orders",
            where={"id": 2},
            values={"status": "corrupted"},
            expected_match_count=1,
        )
    )
    validation = environment.execute(action("validate_constraints", transaction_id="tx-1"))
    commit = environment.execute(action("commit_transaction", transaction_id="tx-1"))

    assert changed.status == "ok"
    assert validation.error_category == "constraint_validation_failed"
    assert commit.error_category == "validation_required"


def test_wrong_column_value_type_is_rejected_before_sqlite_coercion() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-types")
    initial = environment.snapshot()
    environment.execute(action("begin_transaction", transaction_id="tx-1"))

    result = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-1",
            operation_id="op-wrong-type",
            table="orders",
            where={"id": "1"},
            values={"status": "ready"},
            expected_match_count=1,
        )
    )

    assert result.error_category == "value_type_mismatch"
    assert environment.snapshot().database_digest == initial.database_digest
    assert environment.snapshot().attempted_row_count == 0


def test_wrong_and_second_transaction_are_rejected_without_mutation() -> None:
    environment = DataOpsEnvironment(fixture(), run_id="run-transaction-identity")
    initial = environment.snapshot()
    first = environment.execute(action("begin_transaction", transaction_id="tx-1"))
    second = environment.execute(action("begin_transaction", transaction_id="tx-2"))
    wrong = environment.execute(
        action(
            "update_rows",
            transaction_id="tx-2",
            operation_id="op-wrong-transaction",
            table="orders",
            where={"id": 1},
            values={"status": "ready"},
            expected_match_count=1,
        )
    )

    assert first.status == "ok"
    assert second.error_category == "transaction_state"
    assert wrong.error_category == "transaction_mismatch"
    assert environment.snapshot().database_digest == initial.database_digest
    assert environment.snapshot().attempted_row_count == 0
