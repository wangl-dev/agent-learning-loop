"""Normalize strict raw M1-M4 and diagnostic results into M5A records."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from pydantic import JsonValue

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.corpus import validate_workspace_corpus
from agent_learning_loop.dataops_corpus import validate_dataops_corpus
from agent_learning_loop.dataops_schemas import (
    DATAOPS_FULL_CHECK_NAMES,
    BeginTransactionArguments,
    CommitTransactionArguments,
    DataOpsAction,
    DataOpsAuditRecord,
    DataOpsRunResult,
    DataOpsSnapshot,
    DataOpsTableFixture,
    DataOpsTableScope,
    DataOpsTaskFixture,
    DataOpsToolResult,
    DataOpsVerifierResult,
    DescribeTableArguments,
    InsertRowArguments,
    QueryRowsArguments,
    RollbackTransactionArguments,
    UpdateRowsArguments,
    ValidateConstraintsArguments,
)
from agent_learning_loop.dataops_schemas import (
    TOOL_ARGUMENT_MODELS as DATAOPS_ARGUMENT_MODELS,
)
from agent_learning_loop.eval_bundle import sha256_file
from agent_learning_loop.eval_schemas import (
    EvalCell,
    NormalizedEvalRecord,
    RecoveryDiagnosticArtifact,
    RecoveryEvalCell,
    ReliabilityEvalCell,
    SystemEvalCell,
)
from agent_learning_loop.incident_corpus import validate_incident_corpus
from agent_learning_loop.incident_schemas import (
    IncidentAction,
    IncidentApprovalRule,
    IncidentAuditRecord,
    IncidentRunResult,
    IncidentSnapshot,
    IncidentTaskFixture,
    IncidentToolResult,
)
from agent_learning_loop.incident_verifier import IncidentStateVerifier
from agent_learning_loop.runtime_schemas import RuntimeEvent, RuntimeResult, RuntimeState
from agent_learning_loop.schemas import (
    Action,
    Event,
    RunResult,
    ToolResult,
    VerifierCheck,
    VerifierResult,
    WorkspaceSnapshot,
)
from agent_learning_loop.verifier import WorkspaceStateVerifier


class EvalRecordValidationError(ValueError):
    """Raw evidence does not match its pre-registered cell."""


def _verifier_is_consistent(verifier: VerifierResult) -> bool:
    names = [check.name for check in verifier.checks]
    passed = bool(verifier.checks) and all(check.passed for check in verifier.checks)
    return all(
        (
            len(names) == len(set(names)),
            verifier.passed == passed,
            verifier.score == (1.0 if passed else 0.0),
        )
    )


def _read_workspace_snapshot(root: Path) -> WorkspaceSnapshot:
    if not root.is_dir() or root.is_symlink():
        raise EvalRecordValidationError("workspace_artifact_missing_or_unsafe")
    resolved = root.resolve(strict=True)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise EvalRecordValidationError("workspace_artifact_symlink")
        path.resolve(strict=True).relative_to(resolved)
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return WorkspaceSnapshot(files=files)


def _workspace_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise EvalRecordValidationError("workspace_projection_path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise EvalRecordValidationError("workspace_projection_path")
    return path


def _project_workspace_step(files: dict[str, str], action: Action) -> ToolResult:
    arguments = action.arguments
    if action.tool_name == "list_files":
        if set(arguments) != {"path"}:
            raise EvalRecordValidationError("workspace_projection_arguments")
        directory = _workspace_relative_path(arguments["path"])
        prefix = "" if str(directory) == "." else f"{directory.as_posix().rstrip('/')}/"
        if prefix and not any(path.startswith(prefix) for path in files):
            raise EvalRecordValidationError("workspace_projection_directory")
        return ToolResult.model_validate(
            {
                "status": "ok",
                "payload": {"paths": sorted(path for path in files if path.startswith(prefix))},
            }
        )
    if action.tool_name == "read_text":
        if set(arguments) != {"path"}:
            raise EvalRecordValidationError("workspace_projection_arguments")
        path = _workspace_relative_path(arguments["path"]).as_posix()
        if path not in files:
            raise EvalRecordValidationError("workspace_projection_read")
        return ToolResult(status="ok", payload={"content": files[path]})
    if set(arguments) != {"path", "content"}:
        raise EvalRecordValidationError("workspace_projection_arguments")
    path = _workspace_relative_path(arguments["path"]).as_posix()
    content = arguments["content"]
    if not isinstance(content, str):
        raise EvalRecordValidationError("workspace_projection_write")
    files[path] = content
    return ToolResult(
        status="ok",
        payload={"path": path, "bytes_written": len(content.encode("utf-8"))},
    )


def _validate_workspace_raw(root: Path, result: RunResult) -> None:
    corpus = validate_workspace_corpus()
    fixture = next((item for item in corpus.fixtures if item.task.task_id == result.task_id), None)
    catalog = next((item for item in corpus.catalogs if item.task_id == result.task_id), None)
    if fixture is None or catalog is None:
        raise EvalRecordValidationError("workspace_packaged_identity")
    events = [
        Event.model_validate_json(line)
        for line in (root / result.events_file).read_text(encoding="utf-8").splitlines()
    ]
    expected_actions = [entry.action for entry in catalog.actions]
    if len(events) != 2 + (2 * len(expected_actions)):
        raise EvalRecordValidationError("workspace_event_count")
    actions: list[Action] = []
    tool_results: list[ToolResult] = []
    for index in range(len(expected_actions)):
        selected = events[1 + (2 * index)]
        completed = events[2 + (2 * index)]
        if (
            selected.event_kind != "action_selected"
            or completed.event_kind != "tool_completed"
            or selected.step_index != index
            or completed.step_index != index
        ):
            raise EvalRecordValidationError("workspace_event_action_order")
        actions.append(Action.model_validate(selected.payload))
        tool_results.append(ToolResult.model_validate(completed.payload))
    started = events[0]
    finished = events[-1]
    if (
        started.event_kind != "task_started"
        or finished.event_kind != "run_finished"
        or any(event.run_id != result.run_id for event in events)
        or any(event.task_id != result.task_id for event in events)
        or started.payload.get("instruction") != fixture.task.instruction
        or started.payload.get("fixture_id") != fixture.task.fixture_id
        or started.payload.get("environment_kind") != fixture.task.environment_kind
        or started.payload.get("allowed_tools") != list(fixture.task.allowed_tools)
        or finished.step_index != len(expected_actions)
        or finished.payload.get("outcome") != result.outcome
        or finished.payload.get("verifier_passed") != result.verifier.passed
        or actions != expected_actions
        or any(item.status != "ok" for item in tool_results)
    ):
        raise EvalRecordValidationError("workspace_event_result_binding")
    projected_files = dict(fixture.private.setup.files)
    for action, tool_result in zip(actions, tool_results, strict=True):
        if tool_result != _project_workspace_step(projected_files, action):
            raise EvalRecordValidationError("workspace_tool_result_projection")
    initial = WorkspaceSnapshot(files=dict(fixture.private.setup.files))
    final = _read_workspace_snapshot(root / result.workspace_dir)
    if final != WorkspaceSnapshot(files=projected_files):
        raise EvalRecordValidationError("workspace_final_projection")
    recalculated = WorkspaceStateVerifier().verify(initial, final, fixture.private.expected)
    expected_outcome = "passed" if recalculated.passed else "failed"
    if result.verifier != recalculated or result.outcome != expected_outcome:
        raise EvalRecordValidationError("workspace_final_state_binding")


def _incident_target(action: IncidentAction) -> str:
    if action.tool_name in {"acknowledge_incident", "escalate_incident"}:
        return "incident"
    value = action.arguments.get(
        "service", action.arguments.get("target", action.arguments.get("key", "task"))
    )
    return value if isinstance(value, str) and value else "task"


def _incident_action_fingerprint(action: IncidentAction) -> str | None:
    if action.tool_name == "request_approval":
        return canonical_sha256(
            {
                "tool_name": action.arguments.get("tool_name"),
                "target": action.arguments.get("target"),
                "arguments": action.arguments.get("arguments"),
            }
        )
    if action.tool_name in {"set_feature_flag", "restart_simulated_service"}:
        return canonical_sha256(
            {
                "tool_name": action.tool_name,
                "target": _incident_target(action),
                "arguments": {
                    key: value
                    for key, value in action.arguments.items()
                    if key not in {"approval_id", "operation_id"}
                },
            }
        )
    return None


_INCIDENT_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)\s*([:=])\s*([^\s,;]+)"
)
_INCIDENT_BEARER_TOKEN = re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~-]+)")


def _redact_incident_log(line: str) -> str:
    redacted = _INCIDENT_SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", line)
    return _INCIDENT_BEARER_TOKEN.sub(r"\1 [REDACTED]", redacted)


class _IncidentProjection:
    def __init__(self, fixture: IncidentTaskFixture, *, run_id: str) -> None:
        self.fixture = fixture
        self.run_id = run_id
        self.services = dict(fixture.private.services)
        self.feature_flags = dict(fixture.private.feature_flags)
        self.incident_status = fixture.private.incident_status
        self.restart_counts = {name: 0 for name in self.services}
        self.flag_mutations = {name: 0 for name in self.feature_flags}
        self.sequence = 0
        self.operations: dict[str, tuple[str, IncidentToolResult]] = {}
        self.grants: dict[str, tuple[IncidentApprovalRule, str]] = {}

    def snapshot(self) -> IncidentSnapshot:
        return IncidentSnapshot(
            services=self.services,
            feature_flags=self.feature_flags,
            incident_status=self.incident_status,
            restart_counts=self.restart_counts,
            feature_flag_mutations=self.flag_mutations,
        )

    def _record(
        self,
        action: IncidentAction,
        *,
        category: str,
        decision: str,
        target: str,
        approval_id: str | None = None,
        operation_id: str | None = None,
        fingerprint: str | None = None,
        idempotency_hit: bool = False,
        physical_mutation: bool = False,
        tool_name: object | None = None,
    ) -> IncidentAuditRecord:
        record = IncidentAuditRecord.model_validate(
            {
                "sequence": self.sequence,
                "run_id": self.run_id,
                "task_id": self.fixture.task.task_id,
                "category": category,
                "target": target,
                "tool_name": action.tool_name if tool_name is None else tool_name,
                "approval_id": approval_id,
                "operation_id": operation_id,
                "decision": decision,
                "action_fingerprint": fingerprint,
                "idempotency_hit": idempotency_hit,
                "physical_mutation": physical_mutation,
            }
        )
        self.sequence += 1
        return record

    def _reject(
        self,
        action: IncidentAction,
        category: str,
        *,
        target: str,
        approval_id: str | None = None,
        operation_id: str | None = None,
        fingerprint: str | None = None,
    ) -> tuple[IncidentToolResult, IncidentAuditRecord]:
        result = IncidentToolResult(status="rejected", error_category=category)
        record = self._record(
            action,
            category="execution",
            decision="rejected",
            target=target,
            approval_id=approval_id,
            operation_id=operation_id,
            fingerprint=fingerprint,
        )
        return result, record

    def _request_approval(
        self, action: IncidentAction
    ) -> tuple[IncidentToolResult, IncidentAuditRecord]:
        if self.incident_status in {"acknowledged", "escalated"}:
            return self._reject(action, "incident_terminal", target="incident")
        tool_name = cast(str, action.arguments["tool_name"])
        target = cast(str, action.arguments["target"])
        arguments = cast(dict[str, JsonValue], action.arguments["arguments"])
        fingerprint = canonical_sha256(
            {"tool_name": tool_name, "target": target, "arguments": arguments}
        )
        matching = next(
            (
                rule
                for rule in self.fixture.private.approvals
                if rule.tool_name == tool_name
                and rule.target == target
                and canonical_sha256(rule.canonical_action()) == fingerprint
            ),
            None,
        )
        if matching is None:
            return (
                IncidentToolResult(status="rejected", error_category="approval_denied"),
                self._record(
                    action,
                    category="approval",
                    decision="denied",
                    target=target,
                    fingerprint=fingerprint,
                    tool_name=tool_name,
                ),
            )
        record = self._record(
            action,
            category="approval",
            decision=matching.decision,
            target=target,
            approval_id=matching.approval_id,
            fingerprint=fingerprint,
            tool_name=matching.tool_name,
        )
        if matching.decision == "denied":
            return (
                IncidentToolResult(status="rejected", error_category="approval_denied"),
                record,
            )
        self.grants[matching.approval_id] = (matching, fingerprint)
        return (
            IncidentToolResult(
                status="ok",
                payload={"approval_id": matching.approval_id, "decision": "approved"},
            ),
            record,
        )

    def _high_impact(
        self, action: IncidentAction, *, target: str
    ) -> tuple[IncidentToolResult, IncidentAuditRecord]:
        approval_id = cast(str, action.arguments["approval_id"])
        operation_id = cast(str, action.arguments["operation_id"])
        approved_request = {
            "tool_name": action.tool_name,
            "target": target,
            "arguments": {
                key: value
                for key, value in action.arguments.items()
                if key not in {"approval_id", "operation_id"}
            },
        }
        approval_fingerprint = canonical_sha256(approved_request)
        operation_fingerprint = canonical_sha256({**approved_request, "approval_id": approval_id})
        previous = self.operations.get(operation_id)
        if previous is not None:
            if previous[0] != operation_fingerprint:
                return self._reject(
                    action,
                    "idempotency_conflict",
                    target=target,
                    approval_id=approval_id,
                    operation_id=operation_id,
                    fingerprint=approval_fingerprint,
                )
            return (
                previous[1].model_copy(update={"idempotency_hit": True}),
                self._record(
                    action,
                    category="execution",
                    decision="executed",
                    target=target,
                    approval_id=approval_id,
                    operation_id=operation_id,
                    fingerprint=approval_fingerprint,
                    idempotency_hit=True,
                ),
            )
        if self.incident_status in {"acknowledged", "escalated"}:
            return self._reject(
                action,
                "incident_terminal",
                target=target,
                approval_id=approval_id,
                operation_id=operation_id,
                fingerprint=approval_fingerprint,
            )
        grant = self.grants.get(approval_id)
        if (
            grant is None
            or grant[0].tool_name != action.tool_name
            or grant[0].target != target
            or grant[1] != approval_fingerprint
        ):
            return self._reject(
                action,
                "approval_mismatch",
                target=target,
                approval_id=approval_id,
                operation_id=operation_id,
                fingerprint=approval_fingerprint,
            )
        if action.tool_name == "restart_simulated_service":
            self.services[target] = "healthy"
            self.restart_counts[target] += 1
            result = IncidentToolResult(
                status="ok", payload={"service": target, "state": "healthy"}
            )
        else:
            enabled = cast(bool, action.arguments["enabled"])
            self.feature_flags[target] = enabled
            self.flag_mutations[target] += 1
            service_name = target.split(".", maxsplit=1)[0]
            if service_name in self.services:
                self.services[service_name] = "healthy"
            result = IncidentToolResult(status="ok", payload={"key": target, "enabled": enabled})
        if self.incident_status == "open":
            self.incident_status = "mitigated"
        self.operations[operation_id] = (operation_fingerprint, result)
        return (
            result,
            self._record(
                action,
                category="execution",
                decision="executed",
                target=target,
                approval_id=approval_id,
                operation_id=operation_id,
                fingerprint=approval_fingerprint,
                physical_mutation=True,
            ),
        )

    def step(self, action: IncidentAction) -> tuple[IncidentToolResult, IncidentAuditRecord]:
        target = _incident_target(action)
        if action.tool_name == "get_service_status":
            if target not in self.services:
                return self._reject(action, "unknown_service", target=target)
            return (
                IncidentToolResult(
                    status="ok", payload={"service": target, "state": self.services[target]}
                ),
                self._record(
                    action,
                    category="observation",
                    decision="executed",
                    target=target,
                ),
            )
        if action.tool_name == "read_service_logs":
            if target not in self.services:
                return self._reject(action, "unknown_service", target=target)
            return (
                IncidentToolResult(
                    status="ok",
                    payload={
                        "service": target,
                        "lines": [
                            _redact_incident_log(line)
                            for line in self.fixture.private.logs.get(target, [])
                        ],
                    },
                ),
                self._record(
                    action,
                    category="observation",
                    decision="executed",
                    target=target,
                ),
            )
        if action.tool_name == "inspect_service_config":
            if target not in self.feature_flags:
                return self._reject(action, "unknown_config", target=target)
            return (
                IncidentToolResult(
                    status="ok",
                    payload={"key": target, "enabled": self.feature_flags[target]},
                ),
                self._record(
                    action,
                    category="observation",
                    decision="executed",
                    target=target,
                ),
            )
        if action.tool_name == "request_approval":
            return self._request_approval(action)
        if action.tool_name == "restart_simulated_service":
            if target not in self.services:
                return self._reject(action, "unknown_service", target=target)
            return self._high_impact(action, target=target)
        if action.tool_name == "set_feature_flag":
            enabled = action.arguments.get("enabled")
            if target not in self.feature_flags or not isinstance(enabled, bool):
                return self._reject(action, "invalid_config_mutation", target=target)
            return self._high_impact(action, target=target)
        if action.tool_name == "acknowledge_incident":
            if self.incident_status in {"acknowledged", "escalated"}:
                return self._reject(action, "incident_terminal", target="incident")
            if not all(state == "healthy" for state in self.services.values()):
                return self._reject(action, "incident_not_mitigated", target="incident")
            self.incident_status = "acknowledged"
            return (
                IncidentToolResult(status="ok", payload={"incident_status": "acknowledged"}),
                self._record(
                    action,
                    category="acknowledgement",
                    decision="acknowledged",
                    target="incident",
                ),
            )
        if self.incident_status in {"acknowledged", "escalated"}:
            return self._reject(action, "incident_terminal", target="incident")
        reason = cast(str, action.arguments["reason_category"])
        self.incident_status = "escalated"
        return (
            IncidentToolResult(
                status="ok",
                payload={"incident_status": "escalated", "reason_category": reason},
            ),
            self._record(
                action,
                category="escalation",
                decision="escalated",
                target="incident",
            ),
        )


def _validate_incident_raw(root: Path, result: IncidentRunResult) -> str:
    corpus = validate_incident_corpus()
    fixture = next((item for item in corpus.fixtures if item.task.task_id == result.task_id), None)
    catalog = next((item for item in corpus.catalogs if item.task_id == result.task_id), None)
    if fixture is None or catalog is None:
        raise EvalRecordValidationError("incident_packaged_identity")
    event_lines = (root / result.events_file).read_text(encoding="utf-8").splitlines()
    if len(event_lines) != 2 * len(catalog.actions):
        raise EvalRecordValidationError("incident_event_count")
    actions = [
        IncidentAction.model_validate_json(event_lines[index])
        for index in range(0, len(event_lines), 2)
    ]
    tool_results = [
        IncidentToolResult.model_validate_json(event_lines[index])
        for index in range(1, len(event_lines), 2)
    ]
    expected_actions = [entry.action for entry in catalog.actions]
    if actions != expected_actions:
        raise EvalRecordValidationError("incident_event_catalog_order")
    audit = [
        IncidentAuditRecord.model_validate_json(line)
        for line in (root / result.audit_file).read_text(encoding="utf-8").splitlines()
    ]
    if len(audit) != len(actions):
        raise EvalRecordValidationError("incident_audit_action_count")

    projection = _IncidentProjection(fixture, run_id=result.run_id)
    initial = projection.snapshot()
    for action, tool_result, record in zip(actions, tool_results, audit, strict=True):
        expected_result, expected_record = projection.step(action)
        if tool_result != expected_result or record != expected_record:
            raise EvalRecordValidationError("incident_step_projection_binding")

    final = projection.snapshot()
    recalculated = IncidentStateVerifier().verify(
        initial,
        final,
        fixture.private.expected,
        audit,
        run_id=result.run_id,
        task_id=result.task_id,
    )
    if recalculated != result.verifier:
        raise EvalRecordValidationError("incident_projection_verifier_binding")
    return final.incident_status


def _ordered_dataops_tables(
    fixture: DataOpsTaskFixture,
    rows_by_table: dict[str, list[dict[str, JsonValue]]],
) -> dict[str, list[dict[str, JsonValue]]]:
    ordered: dict[str, list[dict[str, JsonValue]]] = {}

    def primary_key_order(row: dict[str, JsonValue], name: str) -> tuple[int, float | str]:
        value = row[name]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, float(value))
        if isinstance(value, str):
            return (1, value)
        raise EvalRecordValidationError("dataops_primary_key_type")

    for table in sorted(fixture.private.tables, key=lambda item: item.name):
        primary_key = next(column.name for column in table.columns if column.primary_key)
        ordered[table.name] = sorted(
            (dict(row) for row in rows_by_table[table.name]),
            key=lambda row: primary_key_order(row, primary_key),
        )
    return ordered


class _DataOpsProjection:
    def __init__(self, fixture: DataOpsTaskFixture, *, run_id: str) -> None:
        self.fixture = fixture
        self.run_id = run_id
        self.rows = {
            table.name: [dict(row) for row in table.rows] for table in fixture.private.tables
        }
        self.initial_rows = deepcopy(self.rows)
        self.state = "idle"
        self.transaction_id: str | None = None
        self.attempted_row_count = 0
        self.committed_row_count = 0
        self.attempted_by_table: Counter[str] = Counter()
        self.attempted_by_operation: Counter[str] = Counter()
        self.committed_by_table: Counter[str] = Counter()
        self.committed_by_operation: Counter[str] = Counter()
        self.sequence = 0
        self.operations: dict[str, tuple[str, DataOpsToolResult]] = {}

    def snapshot(self) -> DataOpsSnapshot:
        tables = _ordered_dataops_tables(self.fixture, self.rows)
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

    def _scope(self, table: str) -> DataOpsTableScope | None:
        return next((scope for scope in self.fixture.task.scope if scope.table == table), None)

    def _table(self, table: str) -> DataOpsTableFixture | None:
        return next(
            (candidate for candidate in self.fixture.private.tables if candidate.name == table),
            None,
        )

    @staticmethod
    def _primary_key(table: DataOpsTableFixture) -> str:
        return next(column.name for column in table.columns if column.primary_key)

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

    @staticmethod
    def _matches_where(row: dict[str, JsonValue], where: dict[str, JsonValue]) -> bool:
        return all(value is not None and row[column] == value for column, value in where.items())

    def _constraints_valid(self) -> bool:
        table_by_name = {table.name: table for table in self.fixture.private.tables}
        for table in self.fixture.private.tables:
            expected_columns = {column.name for column in table.columns}
            for row in self.rows[table.name]:
                if set(row) != expected_columns or not self._mapping_types_match(table, row):
                    return False
            for column in table.columns:
                if column.primary_key or column.unique:
                    values = [
                        row[column.name]
                        for row in self.rows[table.name]
                        if row[column.name] is not None
                    ]
                    if len(values) != len(set((type(value).__name__, value) for value in values)):
                        return False
                if column.references_table is None or column.references_column is None:
                    continue
                target = table_by_name[column.references_table]
                target_values = {row[column.references_column] for row in self.rows[target.name]}
                if any(
                    row[column.name] is not None and row[column.name] not in target_values
                    for row in self.rows[table.name]
                ):
                    return False
        for protected in self.fixture.private.expected.protected_rows:
            initial = [
                row
                for row in self.initial_rows[protected.table]
                if self._matches_where(row, protected.where)
            ]
            current = [
                row
                for row in self.rows[protected.table]
                if self._matches_where(row, protected.where)
            ]
            if current != initial:
                return False
        return True

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
    ) -> DataOpsAuditRecord:
        record = DataOpsAuditRecord.model_validate(
            {
                "sequence": self.sequence,
                "run_id": self.run_id,
                "task_id": self.fixture.task.task_id,
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
                "after_digest": self.snapshot().database_digest,
                "validation_succeeded": validation_succeeded,
                "cardinality_checked_before_write": cardinality_checked,
                "idempotency_hit": idempotency_hit,
                "terminal_outcome": terminal_outcome,
            }
        )
        self.sequence += 1
        return record

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
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        result = DataOpsToolResult(status="rejected", error_category=category)
        return (
            result,
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
            ),
        )

    def _remember_rejection(
        self,
        action: DataOpsAction,
        operation_id: str,
        category: str,
        before: str,
        *,
        transaction_id: str | None,
        table: str | None,
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        result, record = self._rejected(
            action,
            category,
            before,
            transaction_id=transaction_id,
            operation_id=operation_id,
            table=table,
        )
        self.operations[operation_id] = (self._fingerprint(action), result)
        return result, record

    def _transaction_error(self, transaction_id: str) -> str | None:
        if self.state == "idle":
            return "transaction_required"
        if self.state in {"committed", "rolled_back"}:
            return "transaction_terminal"
        if transaction_id != self.transaction_id:
            return "transaction_mismatch"
        return None

    def _operation_replay(
        self, action: DataOpsAction, operation_id: str, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord] | None:
        previous = self.operations.get(operation_id)
        if previous is None:
            return None
        transaction_id = cast(str, action.arguments["transaction_id"])
        table = cast(str, action.arguments["table"])
        if previous[0] != self._fingerprint(action):
            return self._rejected(
                action,
                "operation_conflict",
                before,
                transaction_id=transaction_id,
                operation_id=operation_id,
                table=table,
            )
        result = previous[1].model_copy(update={"idempotency_hit": True})
        return (
            result,
            self._record(
                action,
                result,
                before,
                decision="idempotent",
                transaction_id=transaction_id,
                operation_id=operation_id,
                table=table,
                idempotency_hit=True,
            ),
        )

    def _describe(
        self, action: DataOpsAction, arguments: DescribeTableArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
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
        return result, self._record(action, result, before, table=arguments.table)

    def _query(
        self, action: DataOpsAction, arguments: QueryRowsArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
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
        ordered = _ordered_dataops_tables(self.fixture, self.rows)[arguments.table]
        selected = [row for row in ordered if self._matches_where(row, arguments.where)][
            : arguments.limit
        ]
        payload_rows = [{column: row[column] for column in arguments.columns} for row in selected]
        result = DataOpsToolResult.model_validate(
            {
                "status": "ok",
                "payload": {"rows": payload_rows, "count": len(payload_rows)},
            }
        )
        return (
            result,
            self._record(action, result, before, table=arguments.table, matched=len(payload_rows)),
        )

    def _begin(
        self, action: DataOpsAction, arguments: BeginTransactionArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        if self.state != "idle":
            return self._rejected(action, "transaction_state", before)
        self.state = "active"
        self.transaction_id = arguments.transaction_id
        result = DataOpsToolResult(status="ok", payload={"transaction_state": "active"})
        return (
            result,
            self._record(
                action,
                result,
                before,
                transaction_id=arguments.transaction_id,
            ),
        )

    def _update(
        self, action: DataOpsAction, arguments: UpdateRowsArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
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
        ordered = _ordered_dataops_tables(self.fixture, self.rows)[arguments.table]
        matched_rows = [row for row in ordered if self._matches_where(row, arguments.where)]
        matched = len(matched_rows)
        if matched != arguments.expected_match_count or matched > scope.max_mutated_rows:
            result, record = self._rejected(
                action,
                "cardinality_mismatch",
                before,
                transaction_id=arguments.transaction_id,
                operation_id=arguments.operation_id,
                table=arguments.table,
                matched=matched,
                cardinality_checked=True,
            )
            self.operations[arguments.operation_id] = (self._fingerprint(action), result)
            return result, record
        primary_key = self._primary_key(table)
        primary_keys = [row[primary_key] for row in matched_rows]
        matched_ids = {(type(value).__name__, value) for value in primary_keys}
        for row in self.rows[arguments.table]:
            value = row[primary_key]
            if (type(value).__name__, value) in matched_ids:
                row.update(arguments.values)
        if not self._constraints_valid():
            raise EvalRecordValidationError("dataops_projected_update_constraint")
        changed = matched
        self.attempted_row_count += changed
        self.attempted_by_table[arguments.table] += changed
        self.attempted_by_operation[arguments.operation_id] += changed
        self.state = "active"
        result = DataOpsToolResult(
            status="ok",
            payload={"matched_row_count": matched, "changed_row_count": changed},
        )
        self.operations[arguments.operation_id] = (self._fingerprint(action), result)
        return (
            result,
            self._record(
                action,
                result,
                before,
                transaction_id=arguments.transaction_id,
                operation_id=arguments.operation_id,
                table=arguments.table,
                columns=sorted(arguments.values),
                matched=matched,
                changed=changed,
                primary_keys=primary_keys,
                cardinality_checked=True,
            ),
        )

    def _insert(
        self, action: DataOpsAction, arguments: InsertRowArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
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
        expected_columns = {column.name for column in table.columns} if table else set()
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
        self.rows[arguments.table].append(dict(arguments.row))
        if not self._constraints_valid():
            self.rows[arguments.table].pop()
            return self._remember_rejection(
                action,
                arguments.operation_id,
                "constraint_conflict",
                before,
                transaction_id=arguments.transaction_id,
                table=arguments.table,
            )
        self.attempted_row_count += 1
        self.attempted_by_table[arguments.table] += 1
        self.attempted_by_operation[arguments.operation_id] += 1
        self.state = "active"
        result = DataOpsToolResult(status="ok", payload={"changed_row_count": 1})
        self.operations[arguments.operation_id] = (self._fingerprint(action), result)
        primary_key = self._primary_key(table)
        return (
            result,
            self._record(
                action,
                result,
                before,
                transaction_id=arguments.transaction_id,
                operation_id=arguments.operation_id,
                table=arguments.table,
                columns=sorted(arguments.row),
                changed=1,
                primary_keys=[arguments.row[primary_key]],
                cardinality_checked=True,
            ),
        )

    def _validate(
        self,
        action: DataOpsAction,
        arguments: ValidateConstraintsArguments,
        before: str,
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        transaction_error = self._transaction_error(arguments.transaction_id)
        if transaction_error is not None:
            return self._rejected(action, transaction_error, before)
        if not self._constraints_valid():
            return self._rejected(action, "constraint_validation_failed", before)
        self.state = "validated"
        result = DataOpsToolResult(status="ok", payload={"constraints_valid": True})
        return (
            result,
            self._record(
                action,
                result,
                before,
                transaction_id=arguments.transaction_id,
                validation_succeeded=True,
            ),
        )

    def _commit(
        self, action: DataOpsAction, arguments: CommitTransactionArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        if self.state != "validated" or arguments.transaction_id != self.transaction_id:
            category = "validation_required" if self.state == "active" else "transaction_state"
            return self._rejected(action, category, before)
        self.state = "committed"
        self.committed_row_count = self.attempted_row_count
        self.committed_by_table = Counter(self.attempted_by_table)
        self.committed_by_operation = Counter(self.attempted_by_operation)
        result = DataOpsToolResult(
            status="ok",
            payload={
                "terminal_state": "committed",
                "committed_row_count": self.committed_row_count,
            },
        )
        return (
            result,
            self._record(
                action,
                result,
                before,
                transaction_id=arguments.transaction_id,
                terminal_outcome="committed",
            ),
        )

    def _rollback(
        self, action: DataOpsAction, arguments: RollbackTransactionArguments, before: str
    ) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        transaction_error = self._transaction_error(arguments.transaction_id)
        if transaction_error is not None:
            return self._rejected(action, transaction_error, before)
        self.rows = deepcopy(self.initial_rows)
        self.state = "rolled_back"
        self.committed_row_count = 0
        self.committed_by_table.clear()
        self.committed_by_operation.clear()
        result = DataOpsToolResult(
            status="ok",
            payload={"terminal_state": "rolled_back", "committed_row_count": 0},
        )
        return (
            result,
            self._record(
                action,
                result,
                before,
                transaction_id=arguments.transaction_id,
                terminal_outcome="rolled_back",
            ),
        )

    def step(self, action: DataOpsAction) -> tuple[DataOpsToolResult, DataOpsAuditRecord]:
        before = self.snapshot().database_digest
        if action.tool_name not in self.fixture.task.allowed_tools:
            return self._rejected(action, "tool_not_allowed", before)
        arguments = DATAOPS_ARGUMENT_MODELS[action.tool_name].model_validate(action.arguments)
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


def _nonzero_counts(values: Counter[str] | dict[str, int]) -> dict[str, int]:
    return {key: value for key, value in values.items() if value > 0}


def _validate_dataops_raw(root: Path, result: DataOpsRunResult) -> None:
    corpus = validate_dataops_corpus()
    fixture = next((item for item in corpus.fixtures if item.task.task_id == result.task_id), None)
    catalog = next((item for item in corpus.catalogs if item.task_id == result.task_id), None)
    if fixture is None or catalog is None:
        raise EvalRecordValidationError("dataops_packaged_identity")
    event_lines = (root / result.events_file).read_text(encoding="utf-8").splitlines()
    if len(event_lines) != 2 * len(catalog.actions):
        raise EvalRecordValidationError("dataops_event_count")
    actions = [
        DataOpsAction.model_validate_json(event_lines[index])
        for index in range(0, len(event_lines), 2)
    ]
    tool_results = [
        DataOpsToolResult.model_validate_json(event_lines[index])
        for index in range(1, len(event_lines), 2)
    ]
    expected_actions = [entry.action for entry in catalog.actions]
    if actions != expected_actions:
        raise EvalRecordValidationError("dataops_event_catalog_order")
    audit = [
        DataOpsAuditRecord.model_validate_json(line)
        for line in (root / result.audit_file).read_text(encoding="utf-8").splitlines()
    ]
    if len(audit) != len(actions):
        raise EvalRecordValidationError("dataops_audit_action_count")

    projection = _DataOpsProjection(fixture, run_id=result.run_id)
    initial = projection.snapshot()
    for action, tool_result, record in zip(actions, tool_results, audit, strict=True):
        expected_result, expected_record = projection.step(action)
        if tool_result != expected_result or record != expected_record:
            raise EvalRecordValidationError("dataops_step_projection_binding")

    final = projection.snapshot()
    expected = fixture.private.expected
    expected_tables = _ordered_dataops_tables(
        fixture, {name: [dict(row) for row in rows] for name, rows in expected.tables.items()}
    )
    expected_checks = [
        VerifierCheck(name=name, passed=True, detail=f"{name} passed")
        for name in DATAOPS_FULL_CHECK_NAMES
    ]
    expected_verifier = DataOpsVerifierResult(
        run_id=result.run_id,
        task_id=result.task_id,
        passed=True,
        score=1.0,
        checks=expected_checks,
        terminal_state=final.terminal_state,
        attempted_row_count=final.attempted_row_count,
        committed_row_count=final.committed_row_count,
        split=result.split,
    )
    if (
        final.tables != expected_tables
        or final.terminal_state != expected.terminal_state
        or final.attempted_row_count != expected.exact_attempted_row_count
        or final.committed_row_count != expected.exact_committed_row_count
        or _nonzero_counts(projection.attempted_by_table)
        != _nonzero_counts(expected.exact_attempted_by_table)
        or _nonzero_counts(projection.attempted_by_operation)
        != _nonzero_counts(expected.exact_attempted_by_operation)
        or _nonzero_counts(projection.committed_by_table)
        != _nonzero_counts(expected.exact_committed_by_table)
        or _nonzero_counts(projection.committed_by_operation)
        != _nonzero_counts(expected.exact_committed_by_operation)
        or result.terminal_state != final.terminal_state
        or result.attempted_row_count != final.attempted_row_count
        or result.committed_row_count != final.committed_row_count
        or result.outcome != "passed"
        or result.verifier != expected_verifier
        or initial.tables != _ordered_dataops_tables(fixture, projection.initial_rows)
    ):
        raise EvalRecordValidationError("dataops_projection_result_binding")


def _validate_runtime_events(root: Path, result: RuntimeResult) -> None:
    events = [
        RuntimeEvent.model_validate_json(line)
        for line in (root / result.events_file).read_text(encoding="utf-8").splitlines()
    ]
    if not events:
        raise EvalRecordValidationError("runtime_events_empty")
    started = events[0]
    finished = events[-1]
    if (
        [event.sequence for event in events] != list(range(len(events)))
        or started.event_kind != "run_started"
        or finished.event_kind != "run_finished"
        or any(event.run_id != result.run_id for event in events)
        or any(event.task_id != result.task_id for event in events)
        or started.payload.get("mode") != result.config.mode.value
        or started.payload.get("schedule_id") != result.schedule_id
        or started.payload.get("schedule_fingerprint") != result.schedule_fingerprint
        or started.payload.get("seed") != result.config.seed
        or finished.payload.get("terminal_state") != result.terminal_state.value
        or finished.payload.get("verifier_passed") != result.verifier.passed
        or finished.step_index != result.usage.steps
    ):
        raise EvalRecordValidationError("runtime_event_result_binding")
    attempts = [event for event in events if event.event_kind == "attempt_started"]
    hits = [event for event in events if event.event_kind == "idempotency_hit"]
    retries = [event for event in events if event.event_kind == "retry_scheduled"]
    before_failures = [
        event
        for event in events
        if event.event_kind == "failure_injected"
        and event.payload.get("injection_phase") == "before_execution"
    ]
    physical = len(attempts) - len(hits) - len(before_failures)
    write_attempts = sum(event.payload.get("tool_name") == "write_text" for event in attempts)
    blocked_writes = sum(
        event.payload.get("tool_name") == "write_text" for event in before_failures
    )
    physical_writes = write_attempts - blocked_writes - len(hits)
    backoff = 0.0
    for event in retries:
        value = event.payload.get("backoff")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise EvalRecordValidationError("runtime_retry_backoff_type")
        backoff += float(value)
    if (
        result.usage.tool_calls != len(attempts)
        or result.usage.physical_executions != physical
        or result.usage.side_effect_executions != physical_writes
        or result.usage.duplicate_side_effects != max(physical_writes - 1, 0)
        or result.usage.retries != len(retries)
        or result.usage.idempotency_hits != len(hits)
        or result.usage.backoff_seconds != backoff
        or result.usage.elapsed_seconds != backoff
    ):
        raise EvalRecordValidationError("runtime_usage_event_binding")
    injected_categories = [
        event.payload.get("error_category")
        for event in events
        if event.event_kind == "failure_injected"
    ]
    if result.terminal_state is RuntimeState.SUCCEEDED:
        if result.error is not None:
            raise EvalRecordValidationError("runtime_success_error_contradiction")
    elif result.error is None or result.error.category not in injected_categories:
        raise EvalRecordValidationError("runtime_error_event_binding")


def cell_config_fingerprint(cell: EvalCell) -> str:
    if isinstance(cell, (ReliabilityEvalCell, RecoveryEvalCell)):
        return canonical_sha256(cell.runtime_config.model_dump(mode="json"))
    payload = cell.model_dump(
        mode="json",
        include={"kind", "suite_id", "environment", "resource_fingerprint"},
    )
    return canonical_sha256(payload)


def expected_eval_run_id(cell_id: str) -> str:
    return "eval-" + "-".join(part for part in cell_id.replace(".", "-").split("-") if part)


def normalize_result(
    root: Path,
    cell: EvalCell,
    source_commit: str,
    raw_relative: str,
) -> NormalizedEvalRecord:
    raw_path = root / Path(raw_relative)
    if not raw_path.is_file() or raw_path.is_symlink():
        raise EvalRecordValidationError("missing_or_unsafe_raw_result")
    try:
        if isinstance(cell, SystemEvalCell):
            return _normalize_system(root, cell, source_commit, raw_relative)
        if isinstance(cell, ReliabilityEvalCell):
            return _normalize_reliability(root, cell, source_commit, raw_relative)
        if isinstance(cell, RecoveryEvalCell):
            return _normalize_recovery(root, cell, source_commit, raw_relative)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvalRecordValidationError(f"invalid_raw_result:{cell.cell_id}") from exc
    raise EvalRecordValidationError("unknown_eval_cell")


def _base(
    root: Path,
    cell: EvalCell,
    source_commit: str,
    raw_relative: str,
) -> dict[str, object]:
    return {
        "kind": cell.kind,
        "suite_id": cell.suite_id,
        "cell_id": cell.cell_id,
        "pair_id": cell.pair_id,
        "arm": cell.arm,
        "source_commit": source_commit,
        "environment": cell.environment,
        "task_id": cell.task_id,
        "split": cell.split,
        "tags": cell.tags,
        "seed": cell.seed,
        "resource_id": cell.resource_id,
        "resource_fingerprint": cell.resource_fingerprint,
        "schedule_id": getattr(cell, "schedule_id", None),
        "schedule_fingerprint": getattr(cell, "schedule_fingerprint", None),
        "config_fingerprint": cell_config_fingerprint(cell),
        "raw_result_path": raw_relative,
        "raw_result_sha256": sha256_file(root / Path(raw_relative)),
    }


def _normalize_system(
    root: Path,
    cell: SystemEvalCell,
    source_commit: str,
    raw_relative: str,
) -> NormalizedEvalRecord:
    text = (root / Path(raw_relative)).read_text(encoding="utf-8")
    common = _base(root, cell, source_commit, raw_relative)
    terminal: str
    if cell.environment == "workspace":
        workspace_result = RunResult.model_validate_json(text)
        passed = workspace_result.outcome == "passed" and workspace_result.verifier.passed
        if (
            workspace_result.task_id != cell.task_id
            or workspace_result.run_id != expected_eval_run_id(cell.cell_id)
            or workspace_result.events_file != "events.jsonl"
            or workspace_result.workspace_dir != "workspace"
            or workspace_result.limitation != RunResult.model_fields["limitation"].default
        ):
            raise EvalRecordValidationError("workspace_task_identity")
        if not _verifier_is_consistent(workspace_result.verifier):
            raise EvalRecordValidationError("workspace_verifier_contradiction")
        _validate_workspace_raw(root / Path(raw_relative).parent, workspace_result)
        terminal = workspace_result.outcome
        dataops_attempted = dataops_committed = None
        incident_terminal = None
        incident_safety = None
    elif cell.environment == "incident":
        incident_result = IncidentRunResult.model_validate_json(text)
        if (
            incident_result.task_id != cell.task_id
            or incident_result.run_id != expected_eval_run_id(cell.cell_id)
        ):
            raise EvalRecordValidationError("incident_task_identity")
        terminal_state = _validate_incident_raw(root / Path(raw_relative).parent, incident_result)
        passed = incident_result.outcome == "passed" and incident_result.verifier.passed
        terminal = terminal_state
        dataops_attempted = dataops_committed = None
        incident_terminal = terminal_state
        incident_safety = incident_result.verifier.passed
    else:
        dataops_result = DataOpsRunResult.model_validate_json(text)
        if (
            dataops_result.task_id != cell.task_id
            or dataops_result.run_id != expected_eval_run_id(cell.cell_id)
            or dataops_result.split != cell.split
        ):
            raise EvalRecordValidationError("dataops_task_or_split_identity")
        _validate_dataops_raw(root / Path(raw_relative).parent, dataops_result)
        passed = dataops_result.outcome == "passed" and dataops_result.verifier.passed
        terminal = dataops_result.terminal_state
        dataops_attempted = dataops_result.attempted_row_count
        dataops_committed = dataops_result.committed_row_count
        incident_terminal = None
        incident_safety = None
    contract = (
        passed == cell.oracle.verifier_state_success
        and ("passed" if passed else "failed") == cell.oracle.outcome
    )
    return NormalizedEvalRecord.model_validate(
        {
            **common,
            "cell_contract_passed": contract,
            "verifier_state_success": passed,
            "runtime_completion_success": "N/A",
            "terminal": terminal,
            "error_category": None if passed else "verifier_failed",
            "dataops_attempted": dataops_attempted,
            "dataops_committed": dataops_committed,
            "incident_terminal": incident_terminal,
            "incident_safety_success": incident_safety,
        }
    )


def _normalize_reliability(
    root: Path,
    cell: ReliabilityEvalCell,
    source_commit: str,
    raw_relative: str,
) -> NormalizedEvalRecord:
    result = RuntimeResult.model_validate_json(
        (root / Path(raw_relative)).read_text(encoding="utf-8")
    )
    if not _verifier_is_consistent(result.verifier):
        raise EvalRecordValidationError("runtime_verifier_contradiction")
    _validate_runtime_events(root / Path(raw_relative).parent, result)
    if (
        result.task_id != cell.task_id
        or result.run_id != expected_eval_run_id(cell.cell_id)
        or result.events_file != "events.jsonl"
        or result.workspace_dir != "workspace"
        or result.limitation != RuntimeResult.model_fields["limitation"].default
        or result.config != cell.runtime_config
        or result.schedule_id != cell.schedule_id
        or result.schedule_fingerprint != cell.schedule_fingerprint
    ):
        raise EvalRecordValidationError("runtime_cell_identity")
    error_category = result.error.category if result.error else None
    completed = result.terminal_state is RuntimeState.SUCCEEDED
    oracle = cell.oracle
    contract = all(
        (
            result.verifier.passed == oracle.verifier_state_success,
            completed == oracle.runtime_completion_success,
            result.terminal_state is oracle.terminal_state,
            error_category == oracle.error_category,
            result.usage.steps == oracle.steps,
            result.usage.tool_calls == oracle.tool_calls,
            result.usage.physical_executions == oracle.physical_executions,
            result.usage.side_effect_executions == oracle.physical_write_executions,
            result.usage.side_effect_executions == oracle.side_effect_executions,
            result.usage.duplicate_side_effects == oracle.duplicate_side_effects,
            result.usage.retries == oracle.retries,
            result.usage.idempotency_hits == oracle.idempotency_hits,
        )
    )
    return NormalizedEvalRecord.model_validate(
        {
            **_base(root, cell, source_commit, raw_relative),
            "cell_contract_passed": contract,
            "verifier_state_success": result.verifier.passed,
            "runtime_completion_success": completed,
            "terminal": result.terminal_state.value,
            "error_category": error_category,
            "steps": result.usage.steps,
            "tool_calls": result.usage.tool_calls,
            "physical_executions": result.usage.physical_executions,
            "physical_write_executions": result.usage.side_effect_executions,
            "side_effect_executions": result.usage.side_effect_executions,
            "duplicate_side_effects": result.usage.duplicate_side_effects,
            "retries": result.usage.retries,
            "idempotency_hits": result.usage.idempotency_hits,
        }
    )


def _normalize_recovery(
    root: Path,
    cell: RecoveryEvalCell,
    source_commit: str,
    raw_relative: str,
) -> NormalizedEvalRecord:
    artifact = RecoveryDiagnosticArtifact.model_validate_json(
        (root / Path(raw_relative)).read_text(encoding="utf-8")
    )
    if artifact.cell_id != cell.cell_id or artifact.diagnostic != cell.diagnostic:
        raise EvalRecordValidationError("recovery_cell_identity")
    oracle = cell.oracle
    contract = all(
        (
            artifact.passed == oracle.diagnostic_passed,
            artifact.verifier_state_success == oracle.verifier_state_success,
            artifact.runtime_completion_success == oracle.runtime_completion_success,
            artifact.terminal == oracle.terminal,
        )
    )
    return NormalizedEvalRecord.model_validate(
        {
            **_base(root, cell, source_commit, raw_relative),
            "cell_contract_passed": contract,
            "verifier_state_success": artifact.verifier_state_success,
            "runtime_completion_success": artifact.runtime_completion_success,
            "terminal": artifact.terminal,
            "error_category": artifact.error_category,
            "physical_executions": artifact.physical_executions,
            "physical_write_executions": artifact.physical_write_executions,
            "duplicate_side_effects": artifact.duplicate_side_effects,
            "diagnostic": artifact.diagnostic,
        }
    )
