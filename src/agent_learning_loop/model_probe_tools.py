"""Public tool-schema projection and non-executing argument validation."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import cast

from pydantic import JsonValue, ValidationError

from agent_learning_loop.dataops_schemas import (
    TOOL_ARGUMENT_MODELS as DATAOPS_ARGUMENT_MODELS,
)
from agent_learning_loop.dataops_schemas import (
    DataOpsTableScope,
)
from agent_learning_loop.incident_schemas import (
    TOOL_ARGUMENT_MODELS as INCIDENT_ARGUMENT_MODELS,
)
from agent_learning_loop.model_probe_schemas import (
    ProbeEnvironment,
    ProbeTaskContext,
    ProbeToolDefinition,
)
from agent_learning_loop.schemas import StrictModel
from agent_learning_loop.workspace_tools import (
    ListFilesArguments,
    ReadTextArguments,
    WriteTextArguments,
)

WORKSPACE_ARGUMENT_MODELS: dict[str, type[StrictModel]] = {
    "list_files": ListFilesArguments,
    "read_text": ReadTextArguments,
    "write_text": WriteTextArguments,
}

_DESCRIPTIONS = {
    "list_files": "List UTF-8 Workspace paths under one safe relative directory.",
    "read_text": "Read one UTF-8 Workspace file by safe relative path.",
    "write_text": "Write one UTF-8 Workspace file by safe relative path.",
    "get_service_status": "Read the simulated state of one service.",
    "read_service_logs": "Read public simulated log entries for one service.",
    "inspect_service_config": "Read one simulated feature flag.",
    "request_approval": "Request approval for one exact high-impact simulated action.",
    "set_feature_flag": "Apply one approved simulated feature-flag change.",
    "restart_simulated_service": "Apply one approved simulated service restart.",
    "acknowledge_incident": "Acknowledge a recovered simulated incident.",
    "escalate_incident": "Escalate a simulated incident with a fixed reason category.",
    "describe_table": "Describe public columns for one task-scoped table.",
    "query_rows": "Query task-scoped columns with structured filters.",
    "begin_transaction": "Begin one task-local transaction.",
    "update_rows": "Update an exact task-scoped row cardinality in the transaction.",
    "insert_row": "Insert one task-scoped structured row in the transaction.",
    "validate_constraints": "Validate the active task-local transaction.",
    "commit_transaction": "Commit a validated task-local transaction.",
    "rollback_transaction": "Rollback the active task-local transaction.",
}


def _argument_models(environment: ProbeEnvironment) -> dict[str, type[StrictModel]]:
    if environment == "workspace":
        return WORKSPACE_ARGUMENT_MODELS
    if environment == "incident":
        return cast(dict[str, type[StrictModel]], INCIDENT_ARGUMENT_MODELS)
    return cast(dict[str, type[StrictModel]], DATAOPS_ARGUMENT_MODELS)


def build_tool_definitions(
    environment: ProbeEnvironment, allowed_tools: list[str]
) -> list[ProbeToolDefinition]:
    """Derive Qwen-compatible function schemas from the frozen Pydantic contracts."""
    models = _argument_models(environment)
    definitions: list[ProbeToolDefinition] = []
    for name in allowed_tools:
        model = models.get(name)
        if model is None:
            raise ValueError("unknown_public_tool_contract")
        parameters = cast(dict[str, JsonValue], model.model_json_schema())
        definitions.append(
            ProbeToolDefinition.model_validate(
                {
                    "function": {
                        "name": name,
                        "description": _DESCRIPTIONS[name],
                        "parameters": parameters,
                    }
                }
            )
        )
    return definitions


def _safe_workspace_path(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    windows = PureWindowsPath(value)
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    return (
        not windows.is_absolute()
        and windows.drive == ""
        and not posix.is_absolute()
        and ".." not in posix.parts
    )


def _scope_by_table(context: ProbeTaskContext) -> dict[str, DataOpsTableScope]:
    scopes = [DataOpsTableScope.model_validate(item) for item in context.public_scope]
    return {scope.table: scope for scope in scopes}


def _dataops_scope_error(
    tool_name: str,
    arguments: dict[str, JsonValue],
    context: ProbeTaskContext,
) -> bool:
    if tool_name not in {"describe_table", "query_rows", "update_rows", "insert_row"}:
        return False
    table_value = arguments.get("table")
    if not isinstance(table_value, str):
        return True
    scope = _scope_by_table(context).get(table_value)
    if scope is None:
        return True
    if tool_name == "query_rows":
        columns = arguments.get("columns")
        where = arguments.get("where")
        return not (
            isinstance(columns, list)
            and all(isinstance(item, str) for item in columns)
            and set(cast(list[str], columns)) <= set(scope.readable_columns)
            and isinstance(where, dict)
            and set(where) <= set(scope.predicate_columns)
        )
    if tool_name == "update_rows":
        where = arguments.get("where")
        values = arguments.get("values")
        count = arguments.get("expected_match_count")
        return not (
            isinstance(where, dict)
            and set(where) <= set(scope.predicate_columns)
            and isinstance(values, dict)
            and set(values) <= set(scope.mutable_columns)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count <= scope.max_mutated_rows
        )
    if tool_name == "insert_row":
        row = arguments.get("row")
        return not (
            scope.allow_insert
            and isinstance(row, dict)
            and set(row) <= set(scope.readable_columns)
        )
    return False


def validate_public_tool_arguments(
    environment: ProbeEnvironment,
    context: ProbeTaskContext,
    tool_name: str,
    arguments: dict[str, object],
) -> str | None:
    """Validate schema and public scope without importing or calling an Environment."""
    if tool_name not in context.allowed_tools:
        return "arguments_schema_invalid"
    model = _argument_models(environment).get(tool_name)
    if model is None:
        return "arguments_schema_invalid"
    try:
        model.model_validate(arguments)
    except (ValidationError, ValueError, TypeError):
        return "arguments_schema_invalid"
    if environment == "workspace":
        path = arguments.get("path")
        if isinstance(path, str) and not _safe_workspace_path(path):
            return "scope_violation"
    if environment == "dataops" and _dataops_scope_error(
        tool_name,
        cast(dict[str, JsonValue], arguments),
        context,
    ):
        return "scope_violation"
    return None
