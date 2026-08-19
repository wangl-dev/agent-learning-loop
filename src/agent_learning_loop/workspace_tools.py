"""Validated adapters for the three M1 Workspace tools."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, ValidationError

from agent_learning_loop.protocols import WorkspaceOperationsProtocol
from agent_learning_loop.schemas import Action, StrictModel, ToolResult
from agent_learning_loop.workspace import WorkspaceError


class ListFilesArguments(StrictModel):
    path: str = "."


class ReadTextArguments(StrictModel):
    path: str


class WriteTextArguments(StrictModel):
    path: str
    content: str


class ListFilesPayload(StrictModel):
    paths: list[str]


class ReadTextPayload(StrictModel):
    content: str


class WriteTextPayload(StrictModel):
    path: str
    bytes_written: int = Field(ge=0)


class ListFilesTool:
    name: ClassVar[str] = "list_files"

    def execute(
        self, environment: WorkspaceOperationsProtocol, action: Action
    ) -> ToolResult:
        if action.tool_name != self.name:
            return _error("action selected a different tool")
        try:
            arguments = ListFilesArguments.model_validate(action.arguments)
            return ToolResult(
                status="ok",
                payload=ListFilesPayload(
                    paths=environment.list_files(arguments.path)
                ).model_dump(mode="json"),
            )
        except ValidationError:
            return _error("invalid list_files arguments")
        except (WorkspaceError, OSError, UnicodeError):
            return _error("Workspace list operation rejected")


class ReadTextTool:
    name: ClassVar[str] = "read_text"

    def execute(
        self, environment: WorkspaceOperationsProtocol, action: Action
    ) -> ToolResult:
        if action.tool_name != self.name:
            return _error("action selected a different tool")
        try:
            arguments = ReadTextArguments.model_validate(action.arguments)
            return ToolResult(
                status="ok",
                payload=ReadTextPayload(
                    content=environment.read_text(arguments.path)
                ).model_dump(mode="json"),
            )
        except ValidationError:
            return _error("invalid read_text arguments")
        except (WorkspaceError, OSError, UnicodeError):
            return _error("Workspace read operation rejected")


class WriteTextTool:
    name: ClassVar[str] = "write_text"

    def execute(
        self, environment: WorkspaceOperationsProtocol, action: Action
    ) -> ToolResult:
        if action.tool_name != self.name:
            return _error("action selected a different tool")
        try:
            arguments = WriteTextArguments.model_validate(action.arguments)
            environment.write_text(arguments.path, arguments.content)
            return ToolResult(
                status="ok",
                payload=WriteTextPayload(
                    path=arguments.path,
                    bytes_written=len(arguments.content.encode("utf-8")),
                ).model_dump(mode="json"),
            )
        except ValidationError:
            return _error("invalid write_text arguments")
        except (WorkspaceError, OSError, UnicodeError):
            return _error("Workspace write operation rejected")


def _error(message: str) -> ToolResult:
    return ToolResult(status="error", payload={"message": message})
