"""Task-scoped SQLite environment for the DataOps v1 contract."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.dataops_schemas import (
    TOOL_ARGUMENT_MODELS,
    BeginTransactionArguments,
    CommitTransactionArguments,
    DataOpsAction,
    DataOpsAuditRecord,
    DataOpsSnapshot,
    DataOpsTableFixture,
    DataOpsTableScope,
    DataOpsTaskFixture,
    DataOpsToolResult,
    DescribeTableArguments,
    InsertRowArguments,
    QueryRowsArguments,
    RollbackTransactionArguments,
    UpdateRowsArguments,
    ValidateConstraintsArguments,
)


def _quoted(identifier: str) -> str:
    if not identifier or not identifier[0].isalpha() or not identifier.replace("_", "a").isalnum():
        raise ValueError("invalid_identifier")
    return f'"{identifier}"'


def _where_clause(where: dict[str, JsonValue]) -> tuple[str, list[JsonValue]]:
    ordered = sorted(where)
    clause = " AND ".join(f"{_quoted(column)} = ?" for column in ordered)
    return clause, [where[column] for column in ordered]


class DataOpsEnvironment:
    """Execute structured DataOps actions against one disposable SQLite file."""

    def __init__(
        self,
        fixture: DataOpsTaskFixture,
        *,
        run_id: str,
        database_directory: Path | None = None,
    ) -> None:
        self.fixture = fixture
        self.run_id = run_id
        self.task_id = fixture.task.task_id
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        if database_directory is None:
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="all-dataops-")
            database_directory = Path(self._temporary_directory.name)
        database_directory.mkdir(parents=True, exist_ok=True)
        self._database_path = database_directory / "task.sqlite"
        self._connection = sqlite3.connect(self._database_path, isolation_level=None)
        self._closed = False
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_database()
        self.state: str = "idle"
        self.transaction_id: str | None = None
        self.attempted_row_count = 0
        self.committed_row_count = 0
        self.audit: list[DataOpsAuditRecord] = []
        self._operations: dict[str, tuple[str, DataOpsToolResult]] = {}
        self._initial_tables = self._read_all_tables()
        self._initial_digest = canonical_sha256(self._initial_tables)

    def __enter__(self) -> DataOpsEnvironment:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, sqlite3.Error):
            pass

    def close(self) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            self._connection.close()
            self._closed = True
        finally:
            if self._temporary_directory is not None:
                self._temporary_directory.cleanup()
                self._temporary_directory = None

    def snapshot(self) -> DataOpsSnapshot:
        tables = self._read_all_tables()
        return DataOpsSnapshot(
            terminal_state=cast(
                Literal["idle", "active", "validated", "committed", "rolled_back"],
                self.state,
            ),
            transaction_id=self.transaction_id,
            tables=tables,
            database_digest=canonical_sha256(tables),
            attempted_row_count=self.attempted_row_count,
            committed_row_count=self.committed_row_count,
        )

    def execute(self, action: DataOpsAction) -> DataOpsToolResult:
        before = self.snapshot().database_digest
        if action.tool_name not in self.fixture.task.allowed_tools:
            return self._rejected(action, "tool_not_allowed", before)
        arguments = TOOL_ARGUMENT_MODELS[action.tool_name].model_validate(action.arguments)
        if action.tool_name == "describe_table":
            return self._describe(action, cast(DescribeTableArguments, arguments), before)
        if action.tool_name == "query_rows":
            return self._query(action, cast(QueryRowsArguments, arguments), before)
        if action.tool_name == "begin_transaction":
            return self._begin(action, cast(BeginTransactionArguments, arguments), before)
        if action.tool_name == "update_rows":
            return self._update(action, cast(UpdateRowsArguments, arguments), before)
        if action.tool_name == "insert_row":
            return self._insert(action, cast(InsertRowArguments, arguments), before)
        if action.tool_name == "validate_constraints":
            return self._validate(action, cast(ValidateConstraintsArguments, arguments), before)
        if action.tool_name == "commit_transaction":
            return self._commit(action, cast(CommitTransactionArguments, arguments), before)
        return self._rollback(action, cast(RollbackTransactionArguments, arguments), before)

    def _create_database(self) -> None:
        for table in self.fixture.private.tables:
            definitions: list[str] = []
            for column in table.columns:
                pieces = [_quoted(column.name), column.type.upper()]
                if column.primary_key:
                    pieces.append("PRIMARY KEY")
                if column.not_null:
                    pieces.append("NOT NULL")
                if column.unique:
                    pieces.append("UNIQUE")
                if column.references_table is not None and column.references_column is not None:
                    pieces.append(
                        f"REFERENCES {_quoted(column.references_table)}"
                        f"({_quoted(column.references_column)})"
                    )
                definitions.append(" ".join(pieces))
            self._connection.execute(
                f"CREATE TABLE {_quoted(table.name)} ({', '.join(definitions)})"
            )
            columns = [column.name for column in table.columns]
            placeholders = ", ".join("?" for _ in columns)
            statement = (
                f"INSERT INTO {_quoted(table.name)} "
                f"({', '.join(_quoted(column) for column in columns)}) VALUES ({placeholders})"
            )
            for row in table.rows:
                self._connection.execute(statement, [row[column] for column in columns])

    def _read_all_tables(self) -> dict[str, list[dict[str, JsonValue]]]:
        result: dict[str, list[dict[str, JsonValue]]] = {}
        for table in sorted(self.fixture.private.tables, key=lambda item: item.name):
            primary_key = self._primary_key(table)
            rows = self._connection.execute(
                f"SELECT * FROM {_quoted(table.name)} ORDER BY {_quoted(primary_key)}"
            ).fetchall()
            result[table.name] = [
                {key: cast(JsonValue, row[key]) for key in row.keys()} for row in rows
            ]
        return result

    def _scope(self, table: str) -> DataOpsTableScope | None:
        return next((scope for scope in self.fixture.task.scope if scope.table == table), None)

    def _table(self, table: str) -> DataOpsTableFixture | None:
        return next((item for item in self.fixture.private.tables if item.name == table), None)

    @staticmethod
    def _primary_key(table: DataOpsTableFixture) -> str:
        return next(column.name for column in table.columns if column.primary_key)

    def _describe(
        self,
        action: DataOpsAction,
        arguments: DescribeTableArguments,
        before: str,
    ) -> DataOpsToolResult:
        scope = self._scope(arguments.table)
        table = self._table(arguments.table)
        if scope is None or table is None:
            return self._rejected(action, "scope_violation", before, table=arguments.table)
        columns = [
            {
                "name": column.name,
                "type": column.type,
                "primary_key": column.primary_key,
                "not_null": column.not_null,
                "unique": column.unique,
                "references_table": column.references_table,
                "references_column": column.references_column,
            }
            for column in table.columns
            if column.name in scope.readable_columns
        ]
        result = DataOpsToolResult.model_validate(
            {
                "status": "ok",
                "payload": {
                    "table": arguments.table,
                    "columns": columns,
                    "public_constraints": self.fixture.task.public_constraints,
                },
            }
        )
        self._record(action, result, before, table=arguments.table)
        return result

    def _query(
        self,
        action: DataOpsAction,
        arguments: QueryRowsArguments,
        before: str,
    ) -> DataOpsToolResult:
        scope = self._scope(arguments.table)
        table = self._table(arguments.table)
        if scope is None or table is None:
            return self._rejected(action, "scope_violation", before, table=arguments.table)
        if not set(arguments.columns) <= set(scope.readable_columns) or not set(
            arguments.where
        ) <= set(scope.predicate_columns):
            return self._rejected(action, "scope_violation", before, table=arguments.table)
        if not self._mapping_types_match(table, arguments.where):
            return self._rejected(action, "value_type_mismatch", before, table=arguments.table)
        primary_key = self._primary_key(table)
        clause, values = _where_clause(arguments.where)
        where_sql = f" WHERE {clause}" if clause else ""
        rows = self._connection.execute(
            f"SELECT {', '.join(_quoted(column) for column in arguments.columns)} "
            f"FROM {_quoted(arguments.table)}{where_sql} "
            f"ORDER BY {_quoted(primary_key)} LIMIT ?",
            [*values, arguments.limit],
        ).fetchall()
        payload_rows = [{key: cast(JsonValue, row[key]) for key in row.keys()} for row in rows]
        result = DataOpsToolResult.model_validate(
            {"status": "ok", "payload": {"rows": payload_rows, "count": len(rows)}}
        )
        self._record(action, result, before, table=arguments.table, matched=len(rows))
        return result

    def _begin(
        self,
        action: DataOpsAction,
        arguments: BeginTransactionArguments,
        before: str,
    ) -> DataOpsToolResult:
        if self.state != "idle":
            return self._rejected(action, "transaction_state", before)
        self._connection.execute("BEGIN")
        self.state = "active"
        self.transaction_id = arguments.transaction_id
        result = DataOpsToolResult(status="ok", payload={"transaction_state": "active"})
        self._record(action, result, before, transaction_id=arguments.transaction_id)
        return result

    def _update(
        self,
        action: DataOpsAction,
        arguments: UpdateRowsArguments,
        before: str,
    ) -> DataOpsToolResult:
        replay = self._operation_replay(action, arguments.operation_id, before)
        if replay is not None:
            return replay
        transaction_error = self._transaction_error(arguments.transaction_id)
        if transaction_error is not None:
            return self._remember_rejection(
                action,
                arguments.operation_id,
                transaction_error,
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        scope = self._scope(arguments.table)
        table = self._table(arguments.table)
        if (
            scope is None
            or table is None
            or not set(arguments.where) <= set(scope.predicate_columns)
            or not set(arguments.values) <= set(scope.mutable_columns)
        ):
            return self._remember_rejection(
                action,
                arguments.operation_id,
                "scope_violation",
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        if not self._mapping_types_match(table, arguments.where) or not self._mapping_types_match(
            table, arguments.values
        ):
            return self._remember_rejection(
                action,
                arguments.operation_id,
                "value_type_mismatch",
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        clause, where_values = _where_clause(arguments.where)
        primary_key = self._primary_key(table)
        matched_rows = self._connection.execute(
            f"SELECT {_quoted(primary_key)} FROM {_quoted(arguments.table)} "
            f"WHERE {clause} ORDER BY {_quoted(primary_key)}",
            where_values,
        ).fetchall()
        matched = len(matched_rows)
        if matched != arguments.expected_match_count or matched > scope.max_mutated_rows:
            result = self._rejected(
                action,
                "cardinality_mismatch",
                before,
                transaction_id=arguments.transaction_id,
                operation_id=arguments.operation_id,
                table=arguments.table,
                matched=matched,
                cardinality_checked=True,
            )
            self._operations[arguments.operation_id] = (self._fingerprint(action), result)
            return result
        primary_keys = [cast(JsonValue, row[0]) for row in matched_rows]
        ordered_values = sorted(arguments.values)
        set_clause = ", ".join(f"{_quoted(column)} = ?" for column in ordered_values)
        cursor = self._connection.execute(
            f"UPDATE {_quoted(arguments.table)} SET {set_clause} WHERE {clause}",
            [*(arguments.values[column] for column in ordered_values), *where_values],
        )
        changed = cursor.rowcount
        self.attempted_row_count += changed
        self.state = "active"
        result = DataOpsToolResult(
            status="ok",
            payload={"matched_row_count": matched, "changed_row_count": changed},
        )
        self._operations[arguments.operation_id] = (self._fingerprint(action), result)
        self._record(
            action,
            result,
            before,
            transaction_id=arguments.transaction_id,
            operation_id=arguments.operation_id,
            table=arguments.table,
            columns=ordered_values,
            matched=matched,
            changed=changed,
            primary_keys=primary_keys,
            cardinality_checked=True,
        )
        return result

    def _insert(
        self,
        action: DataOpsAction,
        arguments: InsertRowArguments,
        before: str,
    ) -> DataOpsToolResult:
        replay = self._operation_replay(action, arguments.operation_id, before)
        if replay is not None:
            return replay
        transaction_error = self._transaction_error(arguments.transaction_id)
        if transaction_error is not None:
            return self._remember_rejection(
                action,
                arguments.operation_id,
                transaction_error,
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        scope = self._scope(arguments.table)
        table = self._table(arguments.table)
        expected_columns = {column.name for column in table.columns} if table is not None else set()
        if (
            scope is None
            or table is None
            or not scope.allow_insert
            or set(arguments.row) != expected_columns
        ):
            return self._remember_rejection(
                action,
                arguments.operation_id,
                "scope_violation",
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        if not self._mapping_types_match(table, arguments.row):
            return self._remember_rejection(
                action,
                arguments.operation_id,
                "value_type_mismatch",
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        columns = sorted(arguments.row)
        try:
            self._connection.execute(
                f"INSERT INTO {_quoted(arguments.table)} "
                f"({', '.join(_quoted(column) for column in columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [arguments.row[column] for column in columns],
            )
        except sqlite3.IntegrityError:
            return self._remember_rejection(
                action,
                arguments.operation_id,
                "constraint_conflict",
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        self.attempted_row_count += 1
        self.state = "active"
        result = DataOpsToolResult(status="ok", payload={"changed_row_count": 1})
        self._operations[arguments.operation_id] = (self._fingerprint(action), result)
        primary_key = self._primary_key(table)
        self._record(
            action,
            result,
            before,
            transaction_id=arguments.transaction_id,
            operation_id=arguments.operation_id,
            table=arguments.table,
            columns=columns,
            matched=0,
            changed=1,
            primary_keys=[arguments.row[primary_key]],
            cardinality_checked=True,
        )
        return result

    def _validate(
        self,
        action: DataOpsAction,
        arguments: ValidateConstraintsArguments,
        before: str,
    ) -> DataOpsToolResult:
        transaction_error = self._transaction_error(arguments.transaction_id)
        if transaction_error is not None:
            return self._rejected(action, transaction_error, before)
        foreign_keys = self._connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = self._connection.execute("PRAGMA integrity_check").fetchone()
        if (
            foreign_keys
            or integrity is None
            or integrity[0] != "ok"
            or not self._protected_rows_unchanged()
        ):
            return self._rejected(action, "constraint_validation_failed", before)
        self.state = "validated"
        result = DataOpsToolResult(status="ok", payload={"constraints_valid": True})
        self._record(
            action,
            result,
            before,
            transaction_id=arguments.transaction_id,
            validation_succeeded=True,
        )
        return result

    def _commit(
        self,
        action: DataOpsAction,
        arguments: CommitTransactionArguments,
        before: str,
    ) -> DataOpsToolResult:
        if self.state != "validated" or arguments.transaction_id != self.transaction_id:
            category = "validation_required" if self.state == "active" else "transaction_state"
            return self._rejected(action, category, before)
        self._connection.execute("COMMIT")
        self.state = "committed"
        self.committed_row_count = self.attempted_row_count
        result = DataOpsToolResult(
            status="ok",
            payload={
                "terminal_state": "committed",
                "committed_row_count": self.committed_row_count,
            },
        )
        self._record(
            action,
            result,
            before,
            transaction_id=arguments.transaction_id,
            terminal_outcome="committed",
        )
        return result

    def _rollback(
        self,
        action: DataOpsAction,
        arguments: RollbackTransactionArguments,
        before: str,
    ) -> DataOpsToolResult:
        transaction_error = self._transaction_error(arguments.transaction_id)
        if transaction_error is not None:
            return self._rejected(action, transaction_error, before)
        self._connection.execute("ROLLBACK")
        self.state = "rolled_back"
        self.committed_row_count = 0
        result = DataOpsToolResult(
            status="ok",
            payload={"terminal_state": "rolled_back", "committed_row_count": 0},
        )
        self._record(
            action,
            result,
            before,
            transaction_id=arguments.transaction_id,
            terminal_outcome="rolled_back",
        )
        return result

    def _transaction_error(self, transaction_id: str) -> str | None:
        if self.state == "idle":
            return "transaction_required"
        if self.state in {"committed", "rolled_back"}:
            return "transaction_terminal"
        if transaction_id != self.transaction_id:
            return "transaction_mismatch"
        return None

    @staticmethod
    def _mapping_types_match(table: DataOpsTableFixture, values: dict[str, JsonValue]) -> bool:
        columns = {column.name: column for column in table.columns}
        for name, value in values.items():
            column = columns[name]
            if value is None:
                if column.not_null or column.primary_key:
                    return False
            elif column.type == "text" and not isinstance(value, str):
                return False
            elif column.type == "integer" and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                return False
            elif column.type == "real" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                return False
        return True

    def _protected_rows_unchanged(self) -> bool:
        current = self._read_all_tables()
        for protected in self.fixture.private.expected.protected_rows:
            initial_rows = [
                row
                for row in self._initial_tables[protected.table]
                if all(row.get(name) == value for name, value in protected.where.items())
            ]
            current_rows = [
                row
                for row in current[protected.table]
                if all(row.get(name) == value for name, value in protected.where.items())
            ]
            if current_rows != initial_rows:
                return False
        return True

    def _operation_replay(
        self, action: DataOpsAction, operation_id: str, before: str
    ) -> DataOpsToolResult | None:
        previous = self._operations.get(operation_id)
        if previous is None:
            return None
        fingerprint, result = previous
        if fingerprint != self._fingerprint(action):
            return self._rejected(
                action,
                "operation_conflict",
                before,
                transaction_id=cast(str | None, action.arguments.get("transaction_id")),
                operation_id=operation_id,
                table=cast(str | None, action.arguments.get("table")),
            )
        replay = result.model_copy(update={"idempotency_hit": True})
        self._record(
            action,
            replay,
            before,
            transaction_id=cast(str | None, action.arguments.get("transaction_id")),
            operation_id=operation_id,
            table=cast(str | None, action.arguments.get("table")),
            decision="idempotent",
            idempotency_hit=True,
        )
        return replay

    def _remember_rejection(
        self,
        action: DataOpsAction,
        operation_id: str,
        category: str,
        before: str,
        *,
        transaction_id: str | None = None,
        table: str | None = None,
    ) -> DataOpsToolResult:
        result = self._rejected(
            action,
            category,
            before,
            transaction_id=transaction_id,
            operation_id=operation_id,
            table=table,
        )
        self._operations[operation_id] = (self._fingerprint(action), result)
        return result

    def _rejected(
        self,
        action: DataOpsAction,
        category: str,
        before: str,
        *,
        transaction_id: str | None = None,
        operation_id: str | None = None,
        table: str | None = None,
        matched: int = 0,
        cardinality_checked: bool = False,
    ) -> DataOpsToolResult:
        result = DataOpsToolResult(status="rejected", error_category=category)
        self._record(
            action,
            result,
            before,
            decision="rejected",
            transaction_id=transaction_id,
            operation_id=operation_id,
            table=table,
            matched=matched,
            cardinality_checked=cardinality_checked,
        )
        return result

    @staticmethod
    def _fingerprint(action: DataOpsAction) -> str:
        return canonical_sha256(action.model_dump(mode="json"))

    def _record(
        self,
        action: DataOpsAction,
        result: DataOpsToolResult,
        before: str,
        *,
        decision: str = "executed",
        transaction_id: str | None = None,
        operation_id: str | None = None,
        table: str | None = None,
        columns: list[str] | None = None,
        matched: int = 0,
        changed: int = 0,
        primary_keys: list[JsonValue] | None = None,
        validation_succeeded: bool = False,
        cardinality_checked: bool = False,
        idempotency_hit: bool = False,
        terminal_outcome: str | None = None,
    ) -> None:
        after = self.snapshot().database_digest
        self.audit.append(
            DataOpsAuditRecord.model_validate(
                {
                    "sequence": len(self.audit),
                    "run_id": self.run_id,
                    "task_id": self.task_id,
                    "tool_name": action.tool_name,
                    "decision": decision,
                    "error_category": result.error_category,
                    "transaction_id": transaction_id,
                    "operation_id": operation_id,
                    "table": table,
                    "columns": columns or [],
                    "action_fingerprint": self._fingerprint(action),
                    "matched_row_count": matched,
                    "changed_row_count": changed,
                    "primary_key_digest": (
                        canonical_sha256(primary_keys) if primary_keys is not None else None
                    ),
                    "before_digest": before,
                    "after_digest": after,
                    "validation_succeeded": validation_succeeded,
                    "cardinality_checked_before_write": cardinality_checked,
                    "idempotency_hit": idempotency_hit,
                    "terminal_outcome": terminal_outcome,
                }
            )
        )
