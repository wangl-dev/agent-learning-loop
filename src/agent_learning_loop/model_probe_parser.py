"""Pure Hermes-style single-tool-call parsing for M7C-A."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from agent_learning_loop.model_probe_schemas import (
    ProbeAction,
    ToolCallParseResult,
)

ArgumentsValidator = Callable[[str, dict[str, object]], str | None]

_COMPLETE_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _result(
    category: str,
    *,
    one: bool = False,
    json_valid: bool = False,
    known: bool = False,
    arguments_valid: bool = False,
    exact: bool = False,
    action: ProbeAction | None = None,
) -> ToolCallParseResult:
    return ToolCallParseResult.model_validate(
        {
            "category": category,
            "one_tool_call_detected": one,
            "json_object_valid": json_valid,
            "known_allowed_tool": known,
            "arguments_schema_valid": arguments_valid,
            "exact_reference_match": exact,
            "action": action,
        }
    )


def parse_tool_call(
    raw_generation: str,
    *,
    allowed_tools: Sequence[str],
    validate_arguments: ArgumentsValidator,
    reference_action: ProbeAction,
) -> ToolCallParseResult:
    """Parse exactly one wrapped object and compare it with one trusted reference."""
    stripped = raw_generation.strip()
    matches = list(_COMPLETE_CALL.finditer(stripped))
    if not matches:
        if "<tool_call>" in stripped or "</tool_call>" in stripped:
            return _result("incomplete_tool_call")
        return _result("no_tool_call")
    if len(matches) != 1:
        return _result("multiple_tool_calls")
    match = matches[0]
    if match.span() != (0, len(stripped)):
        return _result("text_outside_tool_call", one=True)
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return _result("invalid_json", one=True)
    if not isinstance(payload, dict):
        return _result("tool_call_not_object", one=True)
    if set(payload) != {"name", "arguments"} or not isinstance(payload["name"], str):
        return _result("invalid_json", one=True)
    arguments = payload["arguments"]
    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        return _result("arguments_not_object", one=True, json_valid=True)
    tool_name = payload["name"]
    if tool_name not in allowed_tools:
        return _result("unknown_tool", one=True, json_valid=True)
    error = validate_arguments(tool_name, arguments)
    if error is not None:
        category = "scope_violation" if error == "scope_violation" else "arguments_schema_invalid"
        return _result(category, one=True, json_valid=True, known=True)
    action = ProbeAction.model_validate(
        {"tool_name": tool_name, "arguments": arguments}
    )
    exact = action == reference_action
    return _result(
        "exact_match" if exact else "reference_mismatch",
        one=True,
        json_valid=True,
        known=True,
        arguments_valid=True,
        exact=exact,
        action=action,
    )
