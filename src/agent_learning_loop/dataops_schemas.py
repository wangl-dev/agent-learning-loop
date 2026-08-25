"""Strict DataOps-only v1 contracts.

The public models accept structured values only. Database paths and SQL text are
deliberately absent: the runner owns the temporary SQLite lifecycle.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from agent_learning_loop.schemas import StrictModel, VerifierResult

DataOpsToolName = Literal[
    "describe_table",
    "query_rows",
    "begin_transaction",
    "update_rows",
    "insert_row",
    "validate_constraints",
    "commit_transaction",
    "rollback_transaction",
]
DATAOPS_TOOL_NAMES: tuple[DataOpsToolName, ...] = (
    "describe_table",
    "query_rows",
    "begin_transaction",
    "update_rows",
    "insert_row",
    "validate_constraints",
    "commit_transaction",
    "rollback_transaction",
)
TransactionState = Literal["idle", "active", "validated", "committed", "rolled_back"]
CorpusSplit = Literal["train", "validation", "test"]
RowValue = str | int | float | bool | None
RollbackReason = Literal[
    "ambiguous_match",
    "constraint_conflict",
    "stale_precondition",
    "validation_failed",
]

DATAOPS_FULL_CHECK_NAMES = (
    "trusted_context",
    "transaction_identity",
    "target_final",
    "protected_state",
    "mutation_scope",
    "attempted_effects",
    "committed_effects",
    "audit_context_order",
    "validation_before_commit",
    "rollback_restoration",
    "operation_idempotency",
    "cardinality_before_write",
    "terminal_audit_record",
    "result_consistency",
)


def _require_primitive_mapping(values: dict[str, JsonValue], category: str) -> None:
    if any(isinstance(value, (dict, list)) for value in values.values()):
        raise ValueError(category)


_SQL_LIKE = re.compile(
    r"(?i)(?:--|/\*|\*/|;|\b(?:select|insert|update|delete|drop|alter|create|pragma|attach|detach)\b\s+)"
)
_PRIVATE_LIKE = re.compile(
    r"(?i)(?:[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|\bsk-[a-z0-9_-]+|api[_-]?key|password\s*[:=])"
)
_ABSOLUTE_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|/|\\\\)")


def _require_safe_strings(values: dict[str, JsonValue], category: str) -> None:
    for value in values.values():
        if not isinstance(value, str):
            continue
        if _SQL_LIKE.search(value) or _ABSOLUTE_PATH.search(value):
            raise ValueError(category)


def _require_synthetic_rows(values: dict[str, JsonValue]) -> None:
    for value in values.values():
        if isinstance(value, str) and (_PRIVATE_LIKE.search(value) or _ABSOLUTE_PATH.search(value)):
            raise ValueError("private_or_path_like_fixture_value")


def _value_matches_column(column: DataOpsColumn, value: JsonValue) -> bool:
    if value is None:
        return not (column.not_null or column.primary_key)
    if column.type == "text":
        return isinstance(value, str)
    if column.type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_rows_match_columns(
    columns: list[DataOpsColumn],
    rows: list[dict[str, JsonValue]],
    *,
    column_error: str,
    type_error: str,
    uniqueness_error: str,
) -> None:
    column_by_name = {column.name: column for column in columns}
    expected_columns = set(column_by_name)
    unique_columns = [
        column for column in columns if column.primary_key or column.unique
    ]
    seen: dict[str, set[tuple[str, JsonValue]]] = {
        column.name: set() for column in unique_columns
    }
    for row in rows:
        _require_primitive_mapping(row, "nested_row_value")
        _require_synthetic_rows(row)
        if set(row) != expected_columns:
            raise ValueError(column_error)
        if any(not _value_matches_column(column, row[column.name]) for column in columns):
            raise ValueError(type_error)
        for column in unique_columns:
            value = row[column.name]
            if value is None:
                continue
            key = (type(value).__name__, value)
            if key in seen[column.name]:
                raise ValueError(uniqueness_error)
            seen[column.name].add(key)


class DataOpsTableScope(StrictModel):
    table: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    readable_columns: list[str] = Field(min_length=1)
    mutable_columns: list[str] = Field(default_factory=list)
    predicate_columns: list[str] = Field(min_length=1)
    max_mutated_rows: int = Field(gt=0)
    allow_insert: bool = False

    @model_validator(mode="after")
    def require_unique_identifiers(self) -> Self:
        groups = (self.readable_columns, self.mutable_columns, self.predicate_columns)
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("duplicate_scope_column")
        if not set(self.mutable_columns) <= set(self.readable_columns):
            raise ValueError("mutable_column_not_readable")
        if not set(self.predicate_columns) <= set(self.readable_columns):
            raise ValueError("predicate_column_not_readable")
        if any(
            not column or not column.replace("_", "a").isalnum()
            for group in groups
            for column in group
        ):
            raise ValueError("invalid_scope_identifier")
        return self


class DataOpsTask(StrictModel):
    schema_version: Literal["1"] = "1"
    task_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    environment_kind: Literal["dataops"] = "dataops"
    instruction: str = Field(min_length=1)
    allowed_tools: list[DataOpsToolName] = Field(min_length=1)
    fixture_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.v1$")
    scope: list[DataOpsTableScope] = Field(min_length=1)
    public_constraints: list[str] = Field(min_length=1)
    provenance: Literal["project-authored-synthetic"] = "project-authored-synthetic"

    @model_validator(mode="after")
    def require_unique_scope_and_tools(self) -> Self:
        tables = [scope.table for scope in self.scope]
        if len(tables) != len(set(tables)):
            raise ValueError("duplicate_table_scope")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("duplicate_allowed_tool")
        if self.fixture_id != f"{self.task_id}.v1":
            raise ValueError("fixture_identity")
        return self


class DataOpsColumn(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["integer", "real", "text"]
    primary_key: bool = False
    not_null: bool = False
    unique: bool = False
    references_table: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    references_column: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def require_complete_reference(self) -> Self:
        if (self.references_table is None) != (self.references_column is None):
            raise ValueError("incomplete_column_reference")
        return self


class DataOpsTableFixture(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    columns: list[DataOpsColumn] = Field(min_length=1)
    rows: list[dict[str, JsonValue]] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_consistent_rows(self) -> Self:
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)) or sum(column.primary_key for column in self.columns) != 1:
            raise ValueError("invalid_table_columns")
        _require_rows_match_columns(
            self.columns,
            self.rows,
            column_error="row_column_mismatch",
            type_error="row_value_type_mismatch",
            uniqueness_error="duplicate_unique_value",
        )
        return self


def _require_foreign_key_rows(
    table_by_name: dict[str, DataOpsTableFixture],
    rows_by_table: dict[str, list[dict[str, JsonValue]]],
    *,
    category: str,
) -> None:
    for table in table_by_name.values():
        for column in table.columns:
            if column.references_table is None or column.references_column is None:
                continue
            target = table_by_name[column.references_table]
            target_column = next(
                candidate
                for candidate in target.columns
                if candidate.name == column.references_column
            )
            if column.type != target_column.type:
                raise ValueError("reference_column_type_mismatch")
            target_values = [
                row[target_column.name] for row in rows_by_table[target.name]
            ]
            for row in rows_by_table[table.name]:
                value = row[column.name]
                if value is None:
                    continue
                if not any(
                    candidate is not None and candidate == value
                    for candidate in target_values
                ):
                    raise ValueError(category)


class DataOpsProtectedRows(StrictModel):
    table: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    where: dict[str, JsonValue] = Field(min_length=1)

    @model_validator(mode="after")
    def require_primitive_where(self) -> Self:
        _require_primitive_mapping(self.where, "nested_protected_filter")
        return self


class DataOpsExpectedState(StrictModel):
    terminal_state: Literal["committed", "rolled_back"]
    tables: dict[str, list[dict[str, JsonValue]]] = Field(min_length=1)
    exact_attempted_row_count: int = Field(ge=0)
    exact_committed_row_count: int = Field(ge=0)
    exact_attempted_by_table: dict[str, int] = Field(min_length=1)
    exact_committed_by_table: dict[str, int] = Field(min_length=1)
    exact_attempted_by_operation: dict[str, int] = Field(min_length=1)
    exact_committed_by_operation: dict[str, int] = Field(min_length=1)
    protected_rows: list[DataOpsProtectedRows] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_rollback_commits_nothing(self) -> Self:
        if self.terminal_state == "rolled_back" and self.exact_committed_row_count != 0:
            raise ValueError("rollback_committed_effect")
        count_maps = (
            self.exact_attempted_by_table,
            self.exact_committed_by_table,
            self.exact_attempted_by_operation,
            self.exact_committed_by_operation,
        )
        if any(count < 0 for counts in count_maps for count in counts.values()):
            raise ValueError("negative_expected_effect")
        if sum(self.exact_attempted_by_table.values()) != self.exact_attempted_row_count:
            raise ValueError("attempted_table_total_mismatch")
        if sum(self.exact_committed_by_table.values()) != self.exact_committed_row_count:
            raise ValueError("committed_table_total_mismatch")
        if sum(self.exact_attempted_by_operation.values()) != self.exact_attempted_row_count:
            raise ValueError("attempted_operation_total_mismatch")
        if sum(self.exact_committed_by_operation.values()) != self.exact_committed_row_count:
            raise ValueError("committed_operation_total_mismatch")
        for rows in self.tables.values():
            for row in rows:
                _require_primitive_mapping(row, "nested_expected_row")
        return self


class DataOpsPrivateFixture(StrictModel):
    tables: list[DataOpsTableFixture] = Field(min_length=1)
    expected: DataOpsExpectedState

    @model_validator(mode="after")
    def require_table_identity_and_expected_coverage(self) -> Self:
        table_names = [table.name for table in self.tables]
        if len(table_names) != len(set(table_names)):
            raise ValueError("duplicate_table")
        if set(self.expected.tables) != set(table_names):
            raise ValueError("expected_table_coverage")
        if set(self.expected.exact_attempted_by_table) != set(table_names) or set(
            self.expected.exact_committed_by_table
        ) != set(table_names):
            raise ValueError("expected_effect_table_coverage")
        if any(item.table not in table_names for item in self.expected.protected_rows):
            raise ValueError("protected_table_missing")
        table_by_name = {table.name: table for table in self.tables}
        for table_name, rows in self.expected.tables.items():
            _require_rows_match_columns(
                table_by_name[table_name].columns,
                rows,
                column_error="expected_row_column_mismatch",
                type_error="expected_row_value_type_mismatch",
                uniqueness_error="expected_duplicate_unique_value",
            )
        for protected in self.expected.protected_rows:
            table = table_by_name[protected.table]
            column_by_name = {column.name: column for column in table.columns}
            if not set(protected.where) <= set(column_by_name):
                raise ValueError("protected_filter_column_missing")
            if any(
                not _value_matches_column(column_by_name[name], value)
                for name, value in protected.where.items()
            ):
                raise ValueError("protected_filter_type_mismatch")
            if not any(
                all(row[name] == value for name, value in protected.where.items())
                for row in table.rows
            ):
                raise ValueError("protected_filter_empty")
        for table in self.tables:
            for column in table.columns:
                if column.references_table is None or column.references_column is None:
                    continue
                target = table_by_name.get(column.references_table)
                if target is None:
                    raise ValueError("reference_table_missing")
                target_column = next(
                    (
                        candidate
                        for candidate in target.columns
                        if candidate.name == column.references_column
                    ),
                    None,
                )
                if target_column is None or not (target_column.primary_key or target_column.unique):
                    raise ValueError("reference_column_invalid")
                if target_column.type != column.type:
                    raise ValueError("reference_column_type_mismatch")
        _require_foreign_key_rows(
            table_by_name,
            {table.name: table.rows for table in self.tables},
            category="initial_foreign_key_missing",
        )
        _require_foreign_key_rows(
            table_by_name,
            self.expected.tables,
            category="expected_foreign_key_missing",
        )
        return self


class DataOpsTaskFixture(StrictModel):
    schema_version: Literal["1"] = "1"
    task: DataOpsTask
    private: DataOpsPrivateFixture

    @model_validator(mode="after")
    def require_public_scope_matches_private_schema(self) -> Self:
        private_tables = {table.name: table for table in self.private.tables}
        for scope in self.task.scope:
            table = private_tables.get(scope.table)
            if table is None:
                raise ValueError("scope_table_missing")
            columns = {column.name for column in table.columns}
            referenced = (
                set(scope.readable_columns)
                | set(scope.mutable_columns)
                | set(scope.predicate_columns)
            )
            if not referenced <= columns:
                raise ValueError("scope_column_missing")
        return self


class DescribeTableArguments(StrictModel):
    table: str = Field(min_length=1)


class QueryRowsArguments(StrictModel):
    table: str = Field(min_length=1)
    columns: list[str] = Field(min_length=1)
    where: dict[str, JsonValue]
    limit: int = Field(gt=0, le=100)

    @model_validator(mode="after")
    def require_primitive_filters(self) -> Self:
        _require_primitive_mapping(self.where, "nested_query_filter")
        _require_safe_strings(self.where, "unsafe_query_value")
        return self


class BeginTransactionArguments(StrictModel):
    transaction_id: str = Field(min_length=1)


class UpdateRowsArguments(StrictModel):
    transaction_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    table: str = Field(min_length=1)
    where: dict[str, JsonValue] = Field(min_length=1)
    values: dict[str, JsonValue] = Field(min_length=1)
    expected_match_count: int = Field(gt=0)

    @model_validator(mode="after")
    def require_primitive_values(self) -> Self:
        _require_primitive_mapping(self.where, "nested_update_filter")
        _require_primitive_mapping(self.values, "nested_update_value")
        _require_safe_strings(self.where, "unsafe_update_filter")
        _require_safe_strings(self.values, "unsafe_update_value")
        return self


class InsertRowArguments(StrictModel):
    transaction_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    table: str = Field(min_length=1)
    row: dict[str, JsonValue] = Field(min_length=1)

    @model_validator(mode="after")
    def require_primitive_row(self) -> Self:
        _require_primitive_mapping(self.row, "nested_insert_value")
        _require_safe_strings(self.row, "unsafe_insert_value")
        return self


class ValidateConstraintsArguments(StrictModel):
    transaction_id: str = Field(min_length=1)


class CommitTransactionArguments(StrictModel):
    transaction_id: str = Field(min_length=1)


class RollbackTransactionArguments(StrictModel):
    transaction_id: str = Field(min_length=1)
    reason_category: RollbackReason


TOOL_ARGUMENT_MODELS: dict[DataOpsToolName, type[StrictModel]] = {
    "describe_table": DescribeTableArguments,
    "query_rows": QueryRowsArguments,
    "begin_transaction": BeginTransactionArguments,
    "update_rows": UpdateRowsArguments,
    "insert_row": InsertRowArguments,
    "validate_constraints": ValidateConstraintsArguments,
    "commit_transaction": CommitTransactionArguments,
    "rollback_transaction": RollbackTransactionArguments,
}


class DataOpsAction(StrictModel):
    schema_version: Literal["1"] = "1"
    tool_name: DataOpsToolName
    arguments: dict[str, JsonValue]

    @model_validator(mode="after")
    def require_tool_specific_arguments(self) -> Self:
        TOOL_ARGUMENT_MODELS[self.tool_name].model_validate(self.arguments)
        return self


class DataOpsToolResult(StrictModel):
    schema_version: Literal["1"] = "1"
    status: Literal["ok", "rejected", "error"]
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    error_category: str | None = None
    idempotency_hit: bool = False


class DataOpsSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    terminal_state: TransactionState
    transaction_id: str | None = None
    tables: dict[str, list[dict[str, JsonValue]]]
    database_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempted_row_count: int = Field(ge=0)
    committed_row_count: int = Field(ge=0)


class DataOpsAuditRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    sequence: int = Field(ge=0)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    tool_name: DataOpsToolName
    decision: Literal["executed", "rejected", "idempotent"]
    error_category: str | None = None
    transaction_id: str | None = None
    operation_id: str | None = None
    table: str | None = None
    columns: list[str] = Field(default_factory=list)
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    matched_row_count: int = Field(ge=0)
    changed_row_count: int = Field(ge=0)
    primary_key_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    before_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_succeeded: bool = False
    cardinality_checked_before_write: bool = False
    idempotency_hit: bool = False
    terminal_outcome: Literal["committed", "rolled_back"] | None = None


class DataOpsVerifierResult(VerifierResult):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    terminal_state: TransactionState | None = None
    attempted_row_count: int | None = Field(default=None, ge=0)
    committed_row_count: int | None = Field(default=None, ge=0)
    split: CorpusSplit | None = None

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> Self:
        if not self.checks:
            raise ValueError("dataops_verifier_checks_empty")
        names = [check.name for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("dataops_verifier_check_name_duplicate")
        checks_passed = all(check.passed for check in self.checks)
        if self.passed != checks_passed:
            raise ValueError("dataops_verifier_passed_checks_mismatch")
        expected_score = 1.0 if self.passed else 0.0
        if self.score != expected_score:
            raise ValueError("dataops_verifier_score_mismatch")
        summaries = (
            self.terminal_state,
            self.attempted_row_count,
            self.committed_row_count,
            self.split,
        )
        if any(value is not None for value in summaries) and any(
            value is None for value in summaries
        ):
            raise ValueError("dataops_verifier_summary_partial")
        return self


class DataOpsRunResult(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    split: CorpusSplit
    outcome: Literal["passed", "failed"]
    terminal_state: Literal["committed", "rolled_back"]
    attempted_row_count: int = Field(ge=0)
    committed_row_count: int = Field(ge=0)
    verifier: DataOpsVerifierResult
    events_file: Literal["events.jsonl"] = "events.jsonl"
    audit_file: Literal["audit.jsonl"] = "audit.jsonl"

    @model_validator(mode="after")
    def require_full_consistent_verdict(self) -> Self:
        if (self.outcome == "passed") != self.verifier.passed:
            raise ValueError("outcome_verifier_mismatch")
        if self.run_id != self.verifier.run_id or self.task_id != self.verifier.task_id:
            raise ValueError("verifier_context_mismatch")
        if (
            self.verifier.terminal_state is None
            or self.verifier.attempted_row_count is None
            or self.verifier.committed_row_count is None
            or self.verifier.split is None
            or self.terminal_state != self.verifier.terminal_state
            or self.attempted_row_count != self.verifier.attempted_row_count
            or self.committed_row_count != self.verifier.committed_row_count
            or self.split != self.verifier.split
        ):
            raise ValueError("verifier_summary_mismatch")
        names = [check.name for check in self.verifier.checks]
        if len(names) != len(DATAOPS_FULL_CHECK_NAMES) or set(names) != set(
            DATAOPS_FULL_CHECK_NAMES
        ):
            raise ValueError("dataops_full_verifier_check_set_mismatch")
        return self
