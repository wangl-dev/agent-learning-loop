"""Field-minimized normalizers for the three frozen system trajectory shapes."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import JsonValue, ValidationError

from agent_learning_loop.action_catalog import ActionCatalog
from agent_learning_loop.dataops_catalog import DataOpsActionCatalog
from agent_learning_loop.dataops_schemas import (
    DataOpsAction,
    DataOpsTask,
    DataOpsToolResult,
)
from agent_learning_loop.incident_catalog import IncidentActionCatalog
from agent_learning_loop.incident_schemas import (
    IncidentAction,
    IncidentTask,
    IncidentToolResult,
)
from agent_learning_loop.schemas import Action, Event, Task, ToolResult
from agent_learning_loop.sft_schemas import (
    SftAssistantAction,
    SftToolResult,
    SftTurn,
)


class SftNormalizationError(ValueError):
    """Raw evidence cannot be projected into the fixed public SFT contract."""


def _jsonl_payloads(path: Path) -> list[object]:
    try:
        raw = path.read_bytes()
        if not raw or b"\r" in raw or not raw.endswith(b"\n"):
            raise SftNormalizationError("sft_events_encoding_or_line_ending")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        if not lines:
            raise SftNormalizationError("sft_events_empty")
        return [json.loads(line) for line in lines]
    except SftNormalizationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SftNormalizationError("invalid_sft_events") from exc


def _require_keys(payload: dict[str, JsonValue], expected: set[str]) -> None:
    if set(payload) != expected:
        raise SftNormalizationError("sft_tool_result_payload_fields")


def _is_int(value: JsonValue) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_string(value: JsonValue) -> bool:
    return isinstance(value, str) and bool(value)


def _workspace_result_shape(tool_name: str, result: ToolResult) -> None:
    if result.status != "ok":
        raise SftNormalizationError("workspace_non_success_result")
    if tool_name == "read_text":
        _require_keys(result.payload, {"content"})
        valid = isinstance(result.payload["content"], str)
    elif tool_name == "write_text":
        _require_keys(result.payload, {"path", "bytes_written"})
        valid = _is_nonempty_string(result.payload["path"]) and _is_int(
            result.payload["bytes_written"]
        )
    elif tool_name == "list_files":
        _require_keys(result.payload, {"paths"})
        paths = result.payload["paths"]
        valid = isinstance(paths, list) and all(
            _is_nonempty_string(item) for item in paths
        )
    else:
        raise SftNormalizationError("unknown_workspace_tool")
    if not valid:
        raise SftNormalizationError("workspace_tool_result_value")


def _incident_result_shape(tool_name: str, result: IncidentToolResult) -> None:
    if result.status == "ok" and result.error_category is not None:
        raise SftNormalizationError("incident_success_error_category")
    if result.status != "ok" and not result.error_category:
        raise SftNormalizationError("incident_failure_error_category")
    if tool_name == "get_service_status":
        _require_keys(result.payload, {"service", "state"})
        valid = all(_is_nonempty_string(value) for value in result.payload.values())
    elif tool_name == "read_service_logs":
        _require_keys(result.payload, {"service", "entries"})
        entries = result.payload["entries"]
        valid = _is_nonempty_string(result.payload["service"]) and isinstance(
            entries, list
        ) and all(isinstance(item, str) for item in entries)
    elif tool_name == "inspect_service_config":
        _require_keys(result.payload, {"key", "enabled"})
        valid = _is_nonempty_string(result.payload["key"]) and isinstance(
            result.payload["enabled"], bool
        )
    elif tool_name == "request_approval":
        if result.status == "ok":
            _require_keys(result.payload, {"approval_id", "decision"})
            valid = _is_nonempty_string(result.payload["approval_id"]) and result.payload[
                "decision"
            ] == "approved"
        else:
            _require_keys(result.payload, set())
            valid = result.status == "rejected" and result.error_category == "approval_denied"
    elif tool_name == "set_feature_flag":
        _require_keys(result.payload, {"key", "enabled"})
        valid = _is_nonempty_string(result.payload["key"]) and isinstance(
            result.payload["enabled"], bool
        )
    elif tool_name == "restart_simulated_service":
        _require_keys(result.payload, {"service", "state"})
        valid = all(_is_nonempty_string(value) for value in result.payload.values())
    elif tool_name == "acknowledge_incident":
        _require_keys(result.payload, {"incident_status"})
        valid = result.payload["incident_status"] == "acknowledged"
    elif tool_name == "escalate_incident":
        _require_keys(result.payload, {"incident_status", "reason_category"})
        valid = result.payload["incident_status"] == "escalated" and _is_nonempty_string(
            result.payload["reason_category"]
        )
    else:
        raise SftNormalizationError("unknown_incident_tool")
    if not valid:
        raise SftNormalizationError("incident_tool_result_value")


def _primitive_row(value: JsonValue) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and not isinstance(item, (dict, list))
        for key, item in value.items()
    )


def _dataops_result_shape(tool_name: str, result: DataOpsToolResult) -> None:
    if result.status == "ok" and result.error_category is not None:
        raise SftNormalizationError("dataops_success_error_category")
    if result.status != "ok" and not result.error_category:
        raise SftNormalizationError("dataops_failure_error_category")
    if tool_name == "describe_table":
        _require_keys(result.payload, {"table", "columns"})
        columns = result.payload["columns"]
        valid = _is_nonempty_string(result.payload["table"]) and isinstance(
            columns, list
        ) and all(_primitive_row(item) for item in columns)
    elif tool_name == "query_rows":
        _require_keys(result.payload, {"count", "rows"})
        rows = result.payload["rows"]
        valid = _is_int(result.payload["count"]) and isinstance(rows, list) and all(
            _primitive_row(item) for item in rows
        )
    elif tool_name == "begin_transaction":
        _require_keys(result.payload, {"transaction_state"})
        valid = result.payload["transaction_state"] == "active"
    elif tool_name == "update_rows":
        if result.status == "ok":
            _require_keys(result.payload, {"matched_row_count", "changed_row_count"})
            valid = _is_int(result.payload["matched_row_count"]) and _is_int(
                result.payload["changed_row_count"]
            )
        else:
            _require_keys(result.payload, set())
            valid = result.status == "rejected"
    elif tool_name == "insert_row":
        _require_keys(result.payload, {"changed_row_count"})
        valid = _is_int(result.payload["changed_row_count"])
    elif tool_name == "validate_constraints":
        _require_keys(result.payload, {"constraints_valid"})
        valid = isinstance(result.payload["constraints_valid"], bool)
    elif tool_name in {"commit_transaction", "rollback_transaction"}:
        _require_keys(result.payload, {"terminal_state", "committed_row_count"})
        expected_terminal = (
            "committed" if tool_name == "commit_transaction" else "rolled_back"
        )
        valid = result.payload["terminal_state"] == expected_terminal and _is_int(
            result.payload["committed_row_count"]
        )
    else:
        raise SftNormalizationError("unknown_dataops_tool")
    if not valid:
        raise SftNormalizationError("dataops_tool_result_value")


def _normalized_action(tool_name: str, arguments: dict[str, JsonValue]) -> SftAssistantAction:
    return SftAssistantAction(tool_name=tool_name, arguments=arguments)


def _normalized_result(
    tool_name: str,
    *,
    status: str,
    payload: dict[str, JsonValue],
    error_category: str | None,
    idempotency_hit: bool,
) -> SftToolResult:
    return SftToolResult.model_validate(
        {
            "tool_name": tool_name,
            "status": status,
            "payload": payload,
            "error_category": error_category,
            "idempotency_hit": idempotency_hit,
        }
    )


def normalize_workspace_trajectory(
    events_path: Path,
    task: Task,
    catalog: ActionCatalog,
) -> list[SftTurn]:
    """Project public Workspace actions/results without run or verifier fields."""
    try:
        events = [Event.model_validate(payload) for payload in _jsonl_payloads(events_path)]
        if len(events) != len(catalog.actions) * 2 + 2:
            raise SftNormalizationError("workspace_event_count")
        started, finished = events[0], events[-1]
        if (
            started.event_kind != "task_started"
            or started.step_index != 0
            or started.task_id != task.task_id
            or started.payload
            != {
                "instruction": task.instruction,
                "fixture_id": task.fixture_id,
                "environment_kind": task.environment_kind,
                "allowed_tools": list(task.allowed_tools),
            }
        ):
            raise SftNormalizationError("workspace_started_context")
        if (
            finished.event_kind != "run_finished"
            or finished.step_index != len(catalog.actions)
            or finished.payload != {"outcome": "passed", "verifier_passed": True}
        ):
            raise SftNormalizationError("workspace_finished_context")
        if any(
            event.run_id != started.run_id or event.task_id != task.task_id
            for event in events
        ):
            raise SftNormalizationError("workspace_event_context")
        turns: list[SftTurn] = []
        for offset, entry in enumerate(catalog.actions):
            action_event = events[1 + offset * 2]
            result_event = events[2 + offset * 2]
            if (
                action_event.event_kind != "action_selected"
                or result_event.event_kind != "tool_completed"
                or action_event.step_index != offset
                or result_event.step_index != offset
            ):
                raise SftNormalizationError("workspace_action_result_order")
            action = Action.model_validate(action_event.payload)
            result = ToolResult.model_validate(result_event.payload)
            if action != entry.action or action.tool_name not in task.allowed_tools:
                raise SftNormalizationError("workspace_action_catalog_binding")
            _workspace_result_shape(action.tool_name, result)
            turns.extend(
                [
                    _normalized_action(action.tool_name, action.arguments),
                    _normalized_result(
                        action.tool_name,
                        status=result.status,
                        payload=result.payload,
                        error_category=None,
                        idempotency_hit=False,
                    ),
                ]
            )
        return turns
    except SftNormalizationError:
        raise
    except (ValidationError, ValueError, TypeError) as exc:
        raise SftNormalizationError("invalid_workspace_sft_trajectory") from exc


def normalize_incident_trajectory(
    events_path: Path,
    task: IncidentTask,
    catalog: IncidentActionCatalog,
) -> list[SftTurn]:
    """Project strict Incident action/result pairs without copying audit records."""
    try:
        payloads = _jsonl_payloads(events_path)
        if len(payloads) != len(catalog.actions) * 2:
            raise SftNormalizationError("incident_event_count")
        turns: list[SftTurn] = []
        for offset, entry in enumerate(catalog.actions):
            action = IncidentAction.model_validate(payloads[offset * 2])
            result = IncidentToolResult.model_validate(payloads[offset * 2 + 1])
            if action != entry.action or action.tool_name not in task.allowed_tools:
                raise SftNormalizationError("incident_action_catalog_binding")
            _incident_result_shape(action.tool_name, result)
            turns.extend(
                [
                    _normalized_action(action.tool_name, action.arguments),
                    _normalized_result(
                        action.tool_name,
                        status=result.status,
                        payload=result.payload,
                        error_category=result.error_category,
                        idempotency_hit=result.idempotency_hit,
                    ),
                ]
            )
        return turns
    except SftNormalizationError:
        raise
    except (ValidationError, ValueError, TypeError) as exc:
        raise SftNormalizationError("invalid_incident_sft_trajectory") from exc


def normalize_dataops_trajectory(
    events_path: Path,
    task: DataOpsTask,
    catalog: DataOpsActionCatalog,
) -> list[SftTurn]:
    """Project strict DataOps pairs without snapshots, digests, or expected rows."""
    try:
        payloads = _jsonl_payloads(events_path)
        if len(payloads) != len(catalog.actions) * 2:
            raise SftNormalizationError("dataops_event_count")
        turns: list[SftTurn] = []
        for offset, entry in enumerate(catalog.actions):
            action = DataOpsAction.model_validate(payloads[offset * 2])
            result = DataOpsToolResult.model_validate(payloads[offset * 2 + 1])
            if action != entry.action or action.tool_name not in task.allowed_tools:
                raise SftNormalizationError("dataops_action_catalog_binding")
            _dataops_result_shape(action.tool_name, result)
            turns.extend(
                [
                    _normalized_action(action.tool_name, action.arguments),
                    _normalized_result(
                        action.tool_name,
                        status=result.status,
                        payload=result.payload,
                        error_category=result.error_category,
                        idempotency_hit=result.idempotency_hit,
                    ),
                ]
            )
        return turns
    except SftNormalizationError:
        raise
    except (ValidationError, ValueError, TypeError) as exc:
        raise SftNormalizationError("invalid_dataops_sft_trajectory") from exc
