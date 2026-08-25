"""State, transaction, effect, and audit verification for DataOps v1."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from pydantic import JsonValue

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.dataops_schemas import (
    DATAOPS_FULL_CHECK_NAMES,
    CorpusSplit,
    DataOpsAction,
    DataOpsAuditRecord,
    DataOpsExpectedState,
    DataOpsSnapshot,
    DataOpsTableScope,
    DataOpsTaskFixture,
    DataOpsVerifierResult,
)
from agent_learning_loop.schemas import VerifierCheck


def _check(name: str, passed: bool, detail: str) -> VerifierCheck:
    return VerifierCheck(name=name, passed=passed, detail=detail)


class DataOpsStateVerifier:
    """Compare private state with externally anchored audit evidence."""

    def verify(
        self,
        initial: DataOpsSnapshot,
        final: DataOpsSnapshot,
        expected: DataOpsExpectedState,
        scopes: list[DataOpsTableScope],
        audit: list[DataOpsAuditRecord],
        expected_actions: list[DataOpsAction],
        *,
        run_id: str,
        task_id: str,
        trusted_fixture: DataOpsTaskFixture,
        trusted_split: CorpusSplit,
    ) -> DataOpsVerifierResult:
        trusted_initial, trusted_final, trusted_audit = self._trusted_projection(
            trusted_fixture,
            expected_actions,
            run_id=run_id,
        )
        scope_by_table = {scope.table: scope for scope in scopes}
        mutation_records = [
            record
            for record in audit
            if record.tool_name in {"update_rows", "insert_row"}
            and record.decision == "executed"
            and record.changed_row_count > 0
        ]
        terminal_records = [
            record for record in audit if record.terminal_outcome in {"committed", "rolled_back"}
        ]
        context_ok = (
            trusted_fixture.task.task_id == task_id
            and all(record.run_id == run_id and record.task_id == task_id for record in audit)
        )
        transaction_values = {
            record.transaction_id for record in audit if record.transaction_id is not None
        }
        begin_records = [record for record in audit if record.tool_name == "begin_transaction"]
        transaction_ok = (
            final.transaction_id is not None
            and transaction_values == {final.transaction_id}
            and len(begin_records) == 1
            and begin_records[0].decision == "executed"
            and len(terminal_records) == 1
            and final.transaction_id == trusted_final.transaction_id
            and len(audit) == len(trusted_audit)
            and all(
                record.transaction_id == trusted.transaction_id
                for record, trusted in zip(audit, trusted_audit, strict=True)
            )
        )
        target_ok = final.terminal_state == expected.terminal_state and (
            canonical_sha256(final.tables) == canonical_sha256(expected.tables)
        ) and final.tables == trusted_final.tables
        protected_ok = all(
            self._selected_rows(initial, item.table, item.where)
            == self._selected_rows(final, item.table, item.where)
            for item in expected.protected_rows
        )
        scope_ok = all(
            record.table in scope_by_table
            and (
                (
                    record.tool_name == "update_rows"
                    and set(record.columns) <= set(scope_by_table[record.table].mutable_columns)
                )
                or (
                    record.tool_name == "insert_row"
                    and scope_by_table[record.table].allow_insert
                    and set(record.columns) <= set(scope_by_table[record.table].readable_columns)
                )
            )
            and record.changed_row_count <= scope_by_table[record.table].max_mutated_rows
            for record in mutation_records
            if record.table is not None
        ) and all(record.table is not None for record in mutation_records)
        audited_attempted = sum(record.changed_row_count for record in mutation_records)
        attempted_by_table: Counter[str] = Counter()
        attempted_by_operation: Counter[str] = Counter()
        for record in mutation_records:
            if record.table is not None:
                attempted_by_table[record.table] += record.changed_row_count
            if record.operation_id is not None:
                attempted_by_operation[record.operation_id] += record.changed_row_count
        audited_operation_ids = {
            record.operation_id for record in audit if record.operation_id is not None
        }
        attempted_ok = (
            final.attempted_row_count == expected.exact_attempted_row_count
            and final.attempted_row_count == trusted_final.attempted_row_count
            and audited_attempted == expected.exact_attempted_row_count
            and dict(attempted_by_table)
            == {
                table: count
                for table, count in expected.exact_attempted_by_table.items()
                if count > 0
            }
            and all(
                attempted_by_operation.get(operation_id, 0) == count
                and operation_id in audited_operation_ids
                for operation_id, count in expected.exact_attempted_by_operation.items()
            )
        )
        committed_ok = (
            final.committed_row_count == expected.exact_committed_row_count
            and final.committed_row_count == trusted_final.committed_row_count
        )
        if final.terminal_state == "committed":
            committed_ok = (
                committed_ok
                and audited_attempted == final.committed_row_count
                and expected.exact_committed_by_table == expected.exact_attempted_by_table
                and expected.exact_committed_by_operation == expected.exact_attempted_by_operation
            )
        else:
            committed_ok = (
                committed_ok
                and final.committed_row_count == 0
                and all(count == 0 for count in expected.exact_committed_by_table.values())
                and all(count == 0 for count in expected.exact_committed_by_operation.values())
            )
        audit_ok = (
            self._audit_context_order(
                audit,
                run_id=run_id,
                task_id=task_id,
                initial_digest=initial.database_digest,
                final_digest=final.database_digest,
            )
            and self._audit_matches_actions(audit, expected_actions)
            and audit == trusted_audit
        )
        last_mutation_sequence = max((record.sequence for record in mutation_records), default=-1)
        successful_validations = [
            record
            for record in audit
            if record.tool_name == "validate_constraints"
            and record.decision == "executed"
            and record.validation_succeeded
        ]
        if final.terminal_state == "committed" and terminal_records:
            terminal_sequence = terminal_records[0].sequence
            validation_ok = any(
                last_mutation_sequence < record.sequence < terminal_sequence
                for record in successful_validations
            )
        else:
            validation_ok = True
        rollback_ok = final.terminal_state != "rolled_back" or (
            final.database_digest == initial.database_digest
            and final.committed_row_count == 0
            and len(terminal_records) == 1
            and terminal_records[0].terminal_outcome == "rolled_back"
        )
        idempotency_ok = self._idempotency(audit)
        cardinality_ok = all(
            record.cardinality_checked_before_write
            for record in audit
            if record.tool_name == "update_rows"
            and (record.decision == "executed" or record.error_category == "cardinality_mismatch")
        )
        terminal_ok = (
            len(terminal_records) == 1
            and terminal_records[0].terminal_outcome == final.terminal_state
            and terminal_records[0].decision == "executed"
        )
        results = {
            "trusted_context": context_ok,
            "transaction_identity": transaction_ok,
            "target_final": target_ok,
            "protected_state": protected_ok,
            "mutation_scope": scope_ok,
            "attempted_effects": attempted_ok,
            "committed_effects": committed_ok,
            "audit_context_order": audit_ok,
            "validation_before_commit": validation_ok,
            "rollback_restoration": rollback_ok,
            "operation_idempotency": idempotency_ok,
            "cardinality_before_write": cardinality_ok,
            "terminal_audit_record": terminal_ok,
            "result_consistency": initial == trusted_initial and final == trusted_final,
        }
        checks = [
            _check(name, results[name], f"{name} {'passed' if results[name] else 'failed'}")
            for name in DATAOPS_FULL_CHECK_NAMES
        ]
        passed = all(check.passed for check in checks)
        return DataOpsVerifierResult(
            run_id=run_id,
            task_id=task_id,
            passed=passed,
            score=1.0 if passed else 0.0,
            checks=checks,
            terminal_state=final.terminal_state,
            attempted_row_count=final.attempted_row_count,
            committed_row_count=final.committed_row_count,
            split=trusted_split,
        )

    def verify_audit(
        self,
        audit: list[DataOpsAuditRecord],
        *,
        run_id: str,
        task_id: str,
    ) -> DataOpsVerifierResult:
        checks = [
            _check(
                "audit_context_order",
                self._audit_context_order(audit, run_id=run_id, task_id=task_id),
                "audit sequence and trusted context",
            ),
            _check(
                "operation_idempotency",
                self._idempotency(audit),
                "operation identifiers and physical effects",
            ),
            _check(
                "terminal_audit_record",
                sum(record.terminal_outcome is not None for record in audit) == 1,
                "one terminal transaction record",
            ),
        ]
        passed = all(check.passed for check in checks)
        return DataOpsVerifierResult(
            run_id=run_id,
            task_id=task_id,
            passed=passed,
            score=1.0 if passed else 0.0,
            checks=checks,
        )

    @staticmethod
    def _selected_rows(
        snapshot: DataOpsSnapshot,
        table: str,
        where: Mapping[str, object],
    ) -> list[dict[str, JsonValue]]:
        return [
            row
            for row in snapshot.tables.get(table, [])
            if all(row.get(column) == value for column, value in where.items())
        ]

    @staticmethod
    def _audit_context_order(
        audit: list[DataOpsAuditRecord],
        *,
        run_id: str,
        task_id: str,
        initial_digest: str | None = None,
        final_digest: str | None = None,
    ) -> bool:
        if not audit:
            return False
        if [record.sequence for record in audit] != list(range(len(audit))):
            return False
        if any(record.run_id != run_id or record.task_id != task_id for record in audit):
            return False
        if initial_digest is not None and audit[0].before_digest != initial_digest:
            return False
        if final_digest is not None and audit[-1].after_digest != final_digest:
            return False
        return all(
            previous.after_digest == current.before_digest
            for previous, current in zip(audit, audit[1:], strict=False)
        )

    @staticmethod
    def _idempotency(audit: list[DataOpsAuditRecord]) -> bool:
        physical = [
            record
            for record in audit
            if record.operation_id is not None
            and record.decision == "executed"
            and record.changed_row_count > 0
        ]
        counts = Counter(record.operation_id for record in physical)
        if any(count != 1 for count in counts.values()):
            return False
        by_operation = {record.operation_id: record for record in physical}
        for record in audit:
            if record.decision == "idempotent":
                original = by_operation.get(record.operation_id)
                if (
                    original is None
                    or record.action_fingerprint != original.action_fingerprint
                    or record.changed_row_count != 0
                    or not record.idempotency_hit
                ):
                    return False
            if record.decision == "rejected" and record.changed_row_count != 0:
                return False
        return True

    @staticmethod
    def _audit_matches_actions(
        audit: list[DataOpsAuditRecord], expected_actions: list[DataOpsAction]
    ) -> bool:
        if len(audit) != len(expected_actions):
            return False
        return all(
            record.tool_name == action.tool_name
            and record.action_fingerprint == canonical_sha256(action.model_dump(mode="json"))
            for record, action in zip(audit, expected_actions, strict=True)
        )

    @staticmethod
    def _trusted_projection(
        fixture: DataOpsTaskFixture,
        expected_actions: list[DataOpsAction],
        *,
        run_id: str,
    ) -> tuple[DataOpsSnapshot, DataOpsSnapshot, list[DataOpsAuditRecord]]:
        from agent_learning_loop.dataops_environment import DataOpsEnvironment

        with DataOpsEnvironment(fixture, run_id=run_id) as environment:
            initial = environment.snapshot()
            for action in expected_actions:
                environment.execute(action)
            final = environment.snapshot()
            audit = list(environment.audit)
        return initial, final, audit
