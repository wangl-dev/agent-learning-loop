from __future__ import annotations

from copy import deepcopy

from test_dataops_environment import action, fixture, update

from agent_learning_loop.dataops_corpus import validate_dataops_corpus
from agent_learning_loop.dataops_environment import DataOpsEnvironment
from agent_learning_loop.dataops_schemas import (
    DataOpsAction,
    DataOpsAuditRecord,
    DataOpsSnapshot,
    DataOpsVerifierResult,
)
from agent_learning_loop.dataops_verifier import DataOpsStateVerifier


def committed_run() -> tuple[DataOpsSnapshot, DataOpsSnapshot, list[DataOpsAuditRecord]]:
    environment = DataOpsEnvironment(fixture(), run_id="run-commit")
    initial = environment.snapshot()
    environment.execute(action("begin_transaction", transaction_id="tx-1"))
    environment.execute(update())
    environment.execute(action("validate_constraints", transaction_id="tx-1"))
    environment.execute(action("commit_transaction", transaction_id="tx-1"))
    final = environment.snapshot()
    audit = list(environment.audit)
    environment.close()
    return initial, final, audit


def committed_actions() -> list[DataOpsAction]:
    return [
        action("begin_transaction", transaction_id="tx-1"),
        update(),
        action("validate_constraints", transaction_id="tx-1"),
        action("commit_transaction", transaction_id="tx-1"),
    ]


def verify(
    initial: DataOpsSnapshot,
    final: DataOpsSnapshot,
    audit: list[DataOpsAuditRecord],
) -> DataOpsVerifierResult:
    selected = fixture()
    return DataOpsStateVerifier().verify(
        initial,
        final,
        selected.private.expected,
        selected.task.scope,
        audit,
        committed_actions(),
        run_id="run-commit",
        task_id=selected.task.task_id,
        trusted_fixture=selected,
        trusted_split="train",
    )


def test_honest_commit_has_all_fourteen_checks_and_passes() -> None:
    initial, final, audit = committed_run()

    result = verify(initial, final, audit)

    assert result.passed is True
    assert len(result.checks) == 14


def test_whole_audit_context_swap_is_rejected_by_external_run_and_task_anchor() -> None:
    initial, final, audit = committed_run()
    swapped = [
        record.model_copy(update={"run_id": "run-other", "task_id": "dataops.other"})
        for record in audit
    ]

    result = verify(initial, final, swapped)

    assert result.passed is False
    assert next(check for check in result.checks if check.name == "trusted_context").passed is False


def test_joint_counter_and_mutation_audit_deletion_cannot_forge_committed_result() -> None:
    initial, final, audit = committed_run()
    final = final.model_copy(update={"attempted_row_count": 0, "committed_row_count": 0})
    shortened = [record for record in audit if record.tool_name != "update_rows"]
    shortened = [
        record.model_copy(update={"sequence": index}) for index, record in enumerate(shortened)
    ]

    result = verify(initial, final, shortened)

    assert result.passed is False
    assert (
        next(check for check in result.checks if check.name == "attempted_effects").passed is False
    )


def test_terminal_record_deletion_is_rejected_even_when_final_state_is_correct() -> None:
    initial, final, audit = committed_run()
    shortened = audit[:-1]

    result = verify(initial, final, shortened)

    assert result.passed is False
    assert (
        next(check for check in result.checks if check.name == "terminal_audit_record").passed
        is False
    )


def test_commit_without_post_mutation_validation_evidence_is_rejected() -> None:
    initial, final, audit = committed_run()
    shortened = [record for record in audit if record.tool_name != "validate_constraints"]
    shortened = [
        record.model_copy(update={"sequence": index}) for index, record in enumerate(shortened)
    ]

    result = verify(initial, final, shortened)

    assert result.passed is False
    check = next(check for check in result.checks if check.name == "validation_before_commit")
    assert check.passed is False


def test_protected_neighbor_change_is_rejected_even_with_target_row_correct() -> None:
    initial, final, audit = committed_run()
    tables = deepcopy(final.tables)
    tables["orders"][1]["status"] = "corrupted"
    tampered = final.model_copy(update={"tables": tables})

    result = verify(initial, tampered, audit)

    assert result.passed is False
    assert next(check for check in result.checks if check.name == "protected_state").passed is False


def test_audit_only_verifier_rejects_missing_or_swapped_terminal_context() -> None:
    _initial, _final, audit = committed_run()
    swapped = [record.model_copy(update={"run_id": "run-other"}) for record in audit[:-1]]

    result = DataOpsStateVerifier().verify_audit(
        swapped,
        run_id="run-commit",
        task_id=fixture().task.task_id,
    )

    assert result.passed is False


def test_duplicate_physical_effect_and_cardinality_evidence_tampering_are_rejected() -> None:
    initial, final, audit = committed_run()
    mutation = next(record for record in audit if record.tool_name == "update_rows")
    duplicate = mutation.model_copy(update={"sequence": mutation.sequence + 1})
    duplicated = [*audit[: mutation.sequence + 1], duplicate, *audit[mutation.sequence + 1 :]]
    duplicated = [
        record.model_copy(update={"sequence": index}) for index, record in enumerate(duplicated)
    ]

    duplicate_result = verify(initial, final, duplicated)

    assert duplicate_result.passed is False
    idempotency = next(
        check for check in duplicate_result.checks if check.name == "operation_idempotency"
    )
    assert idempotency.passed is False

    cardinality_tamper = [
        record.model_copy(update={"cardinality_checked_before_write": False})
        if record.tool_name == "update_rows"
        else record
        for record in audit
    ]
    cardinality_result = verify(initial, final, cardinality_tamper)
    cardinality = next(
        check for check in cardinality_result.checks if check.name == "cardinality_before_write"
    )
    assert cardinality_result.passed is False
    assert cardinality.passed is False


def test_tool_table_fingerprint_and_digest_tampering_are_rejected() -> None:
    initial, final, audit = committed_run()
    mutation_index = next(
        index for index, record in enumerate(audit) if record.tool_name == "update_rows"
    )

    for tamper in (
        {"tool_name": "insert_row"},
        {"table": "other_table"},
        {"action_fingerprint": "f" * 64},
        {"after_digest": "e" * 64},
    ):
        tampered = list(audit)
        tampered[mutation_index] = tampered[mutation_index].model_copy(update=tamper)
        result = verify(initial, final, tampered)
        assert result.passed is False


def test_whole_digest_chain_replacement_is_rejected_by_snapshot_anchors() -> None:
    initial, final, audit = committed_run()
    replacement = "d" * 64
    tampered = [
        record.model_copy(update={"before_digest": replacement, "after_digest": replacement})
        for record in audit
    ]

    result = verify(initial, final, tampered)

    assert result.passed is False
    check = next(check for check in result.checks if check.name == "audit_context_order")
    assert check.passed is False


def test_joint_transaction_and_final_snapshot_identity_replacement_is_rejected() -> None:
    initial, final, audit = committed_run()
    tampered_audit = [
        record.model_copy(update={"transaction_id": "tx-forged"})
        if record.transaction_id is not None
        else record
        for record in audit
    ]
    tampered_final = final.model_copy(update={"transaction_id": "tx-forged"})

    result = verify(initial, tampered_final, tampered_audit)

    assert result.passed is False
    check = next(check for check in result.checks if check.name == "transaction_identity")
    assert check.passed is False


def test_mutation_columns_primary_key_digest_and_matched_count_are_exact() -> None:
    initial, final, audit = committed_run()
    mutation_index = next(
        index for index, record in enumerate(audit) if record.tool_name == "update_rows"
    )

    replacements: tuple[dict[str, object], ...] = (
        {"columns": []},
        {"primary_key_digest": "a" * 64},
        {"matched_row_count": 99},
    )
    for replacement in replacements:
        tampered = list(audit)
        tampered[mutation_index] = tampered[mutation_index].model_copy(update=replacement)
        assert verify(initial, final, tampered).passed is False


def test_ambiguous_actual_two_rows_cannot_be_relabelled_as_one() -> None:
    corpus = validate_dataops_corpus()
    selected = next(
        item
        for item in corpus.fixtures
        if item.task.task_id == "dataops.rollback-ambiguous-customer-match"
    )
    catalog = next(item for item in corpus.catalogs if item.task_id == selected.task.task_id)
    with DataOpsEnvironment(selected, run_id="run-ambiguous") as environment:
        initial = environment.snapshot()
        for entry in catalog.actions:
            environment.execute(entry.action)
        final = environment.snapshot()
        audit = list(environment.audit)
    mutation_index = next(
        index for index, record in enumerate(audit) if record.tool_name == "update_rows"
    )
    assert audit[mutation_index].matched_row_count == 2
    tampered = list(audit)
    tampered[mutation_index] = tampered[mutation_index].model_copy(
        update={"matched_row_count": 1}
    )

    result = DataOpsStateVerifier().verify(
        initial,
        final,
        selected.private.expected,
        selected.task.scope,
        tampered,
        [entry.action for entry in catalog.actions],
        run_id="run-ambiguous",
        task_id=selected.task.task_id,
        trusted_fixture=selected,
        trusted_split="train",
    )

    assert result.passed is False


def test_final_equal_initial_without_rollback_terminal_evidence_is_rejected() -> None:
    selected = fixture()
    selected_expected = selected.private.expected.model_copy(
        update={
            "terminal_state": "rolled_back",
            "tables": {"orders": selected.private.tables[0].rows},
            "exact_attempted_row_count": 1,
            "exact_committed_row_count": 0,
            "exact_committed_by_table": {"orders": 0},
            "exact_committed_by_operation": {"op-1": 0},
        }
    )
    environment = DataOpsEnvironment(selected, run_id="run-commit")
    initial = environment.snapshot()
    environment.execute(action("begin_transaction", transaction_id="tx-1"))
    environment.execute(update())
    environment.execute(
        action(
            "rollback_transaction",
            transaction_id="tx-1",
            reason_category="ambiguous_match",
        )
    )
    final = environment.snapshot()
    missing_terminal = list(environment.audit[:-1])
    environment.close()

    result = DataOpsStateVerifier().verify(
        initial,
        final,
        selected_expected,
        selected.task.scope,
        missing_terminal,
        [
            action("begin_transaction", transaction_id="tx-1"),
            update(),
            action(
                "rollback_transaction",
                transaction_id="tx-1",
                reason_category="ambiguous_match",
            ),
        ],
        run_id="run-commit",
        task_id=selected.task.task_id,
        trusted_fixture=selected,
        trusted_split="train",
    )

    assert final.database_digest == initial.database_digest
    assert result.passed is False
    terminal = next(check for check in result.checks if check.name == "terminal_audit_record")
    assert terminal.passed is False
