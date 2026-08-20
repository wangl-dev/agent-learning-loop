"""Minimal reliability metadata for the three existing Workspace tools."""

from __future__ import annotations

from dataclasses import dataclass

from agent_learning_loop.runtime_schemas import ErrorCategory
from agent_learning_loop.schemas import ToolName


@dataclass(frozen=True)
class ToolMetadata:
    name: ToolName
    side_effecting: bool
    retryable_categories: frozenset[ErrorCategory]


TOOL_METADATA: dict[ToolName, ToolMetadata] = {
    "list_files": ToolMetadata(
        name="list_files",
        side_effecting=False,
        retryable_categories=frozenset(
            {ErrorCategory.TOOL_TRANSIENT, ErrorCategory.TIMEOUT}
        ),
    ),
    "read_text": ToolMetadata(
        name="read_text",
        side_effecting=False,
        retryable_categories=frozenset(
            {ErrorCategory.TOOL_TRANSIENT, ErrorCategory.TIMEOUT}
        ),
    ),
    "write_text": ToolMetadata(
        name="write_text",
        side_effecting=True,
        retryable_categories=frozenset({ErrorCategory.TOOL_TRANSIENT}),
    ),
}
