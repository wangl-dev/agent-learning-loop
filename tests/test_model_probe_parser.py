from __future__ import annotations

import json

import pytest

from agent_learning_loop.model_probe_parser import parse_tool_call
from agent_learning_loop.model_probe_schemas import ProbeAction

REFERENCE = ProbeAction(tool_name="read_text", arguments={"path": "input/title.txt"})


def _validate(tool_name: str, arguments: dict[str, object]) -> str | None:
    if tool_name == "read_text" and set(arguments) == {"path"}:
        path = arguments["path"]
        if isinstance(path, str) and path in {"input/title.txt", "input/other.txt"}:
            return None
        return "scope_violation"
    if tool_name == "write_text" and set(arguments) == {"path", "content"}:
        if all(isinstance(arguments[key], str) for key in arguments):
            return None
    return "arguments_schema_invalid"


@pytest.mark.parametrize(
    ("raw", "category"),
    [
        ("plain answer", "no_tool_call"),
        ("<tool_call>{\"name\":\"read_text\"}", "incomplete_tool_call"),
        (
            "<tool_call>{}</tool_call><tool_call>{}</tool_call>",
            "multiple_tool_calls",
        ),
        ("<tool_call>{oops}</tool_call>", "invalid_json"),
        (
            '<tool_call>{"name":"read_text","arguments":"x"}</tool_call>',
            "arguments_not_object",
        ),
        (
            '<tool_call>{"name":"read_text","arguments":[]}</tool_call>',
            "arguments_not_object",
        ),
        (
            '<tool_call>{"name":"shell","arguments":{}}</tool_call>',
            "unknown_tool",
        ),
        (
            '<tool_call>{"name":"read_text","arguments":{}}</tool_call>',
            "arguments_schema_invalid",
        ),
        (
            '<tool_call>{"name":"read_text","arguments":{"path":"../private.txt"}}</tool_call>',
            "scope_violation",
        ),
        (
            '<tool_call>{"name":"read_text","arguments":{"path":"input/title.txt","extra":1}}</tool_call>',
            "arguments_schema_invalid",
        ),
        (
            "I will inspect it. "
            '<tool_call>{"name":"read_text","arguments":{"path":"input/title.txt"}}'
            "</tool_call>",
            "text_outside_tool_call",
        ),
    ],
)
def test_parser_rejects_malformed_or_out_of_contract_outputs(
    raw: str, category: str
) -> None:
    result = parse_tool_call(
        raw,
        allowed_tools=("read_text", "write_text"),
        validate_arguments=_validate,
        reference_action=REFERENCE,
    )

    assert result.category == category
    assert result.exact_reference_match is False


def test_parser_distinguishes_legal_mismatch_and_exact_match() -> None:
    mismatch = parse_tool_call(
        '<tool_call>{"name":"read_text","arguments":{"path":"input/other.txt"}}</tool_call>',
        allowed_tools=("read_text", "write_text"),
        validate_arguments=_validate,
        reference_action=REFERENCE,
    )
    exact = parse_tool_call(
        '<tool_call>{"name":"read_text","arguments":{"path":"input/title.txt"}}</tool_call>',
        allowed_tools=("read_text", "write_text"),
        validate_arguments=_validate,
        reference_action=REFERENCE,
    )

    assert mismatch.category == "reference_mismatch"
    assert mismatch.arguments_schema_valid is True
    assert mismatch.action is not None
    assert exact.category == "exact_match"
    assert exact.exact_reference_match is True
    assert exact.action == REFERENCE


def test_parser_requires_exact_wrapper_and_object_shape() -> None:
    payload = json.dumps(
        {"name": "read_text", "arguments": {"path": "input/title.txt"}},
        separators=(",", ":"),
    )
    exact = parse_tool_call(
        f"\n<tool_call>{payload}</tool_call>\n",
        allowed_tools=("read_text",),
        validate_arguments=_validate,
        reference_action=REFERENCE,
    )
    non_object = parse_tool_call(
        "<tool_call>[]</tool_call>",
        allowed_tools=("read_text",),
        validate_arguments=_validate,
        reference_action=REFERENCE,
    )

    assert exact.category == "exact_match"
    assert non_object.category == "tool_call_not_object"
