"""Small structural contracts for replaceable M1 components."""

from __future__ import annotations

from typing import Protocol

from agent_learning_loop.schemas import (
    Action,
    Observation,
    Task,
    ToolResult,
    VerifierResult,
    WorkspaceExpectedState,
    WorkspaceSetup,
    WorkspaceSnapshot,
)


class EnvironmentProtocol(Protocol):
    """Initialize, expose public state, snapshot, and close an environment."""

    def reset(self, setup: WorkspaceSetup, *, task_id: str) -> Observation: ...

    def observe(
        self, *, task_id: str, step_index: int, last_tool_result: ToolResult | None
    ) -> Observation: ...

    def snapshot(self) -> WorkspaceSnapshot: ...

    def close(self) -> None: ...


class WorkspaceOperationsProtocol(Protocol):
    """The three file operations available to M1 tools."""

    def list_files(self, path: str) -> list[str]: ...

    def read_text(self, path: str) -> str: ...

    def write_text(self, path: str, content: str) -> None: ...


class ToolProtocol(Protocol):
    """Validate and execute one structured action."""

    @property
    def name(self) -> str: ...

    def execute(
        self, environment: WorkspaceOperationsProtocol, action: Action
    ) -> ToolResult: ...


class PolicyProtocol(Protocol):
    """Choose the next action from public task and observation data."""

    def decide(self, task: Task, observation: Observation) -> Action | None: ...


class VerifierProtocol(Protocol):
    """Judge final state independently from the policy's own claim."""

    def verify(
        self,
        initial: WorkspaceSnapshot,
        final: WorkspaceSnapshot,
        expected: WorkspaceExpectedState,
    ) -> VerifierResult: ...
