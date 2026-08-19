"""Strict M1 data contracts for the Workspace vertical slice."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SchemaVersion = Literal["1"]
ToolName = Literal["list_files", "read_text", "write_text"]
EventKind = Literal[
    "task_started",
    "action_selected",
    "tool_completed",
    "execution_failed",
    "run_finished",
]
TerminalOutcome = Literal["passed", "failed", "error"]


class StrictModel(BaseModel):
    """Reject unknown fields and implicit Python-side type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class Task(StrictModel):
    """The public task information visible to a policy."""

    schema_version: SchemaVersion = "1"
    task_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    environment_kind: Literal["workspace"] = "workspace"
    instruction: str = Field(min_length=1)
    allowed_tools: list[ToolName] = Field(min_length=1)
    fixture_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    provenance: str = Field(min_length=1)


class WorkspaceSetup(StrictModel):
    """Private files used to initialize one isolated Workspace."""

    files: dict[str, str]


class WorkspaceExpectedState(StrictModel):
    """Private verifier state that is never passed to the policy."""

    required_files: dict[str, str] = Field(default_factory=dict)
    unchanged_files: list[str] = Field(default_factory=list)
    allowed_mutations: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)


class WorkspacePrivateFixture(StrictModel):
    """Setup and expected state kept outside the public Task."""

    setup: WorkspaceSetup
    expected: WorkspaceExpectedState


class WorkspaceTaskFixture(StrictModel):
    """A versioned public task paired with private M1 fixture data."""

    task: Task
    private: WorkspacePrivateFixture


class Action(StrictModel):
    """One structured tool request chosen by a policy."""

    schema_version: SchemaVersion = "1"
    tool_name: ToolName
    arguments: dict[str, JsonValue]


class ToolResult(StrictModel):
    """A minimal M1 tool response without an M2 error taxonomy."""

    schema_version: SchemaVersion = "1"
    status: Literal["ok", "error"]
    payload: dict[str, JsonValue]


class Observation(StrictModel):
    """Public state available when the scripted policy chooses an action."""

    schema_version: SchemaVersion = "1"
    task_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    visible_paths: list[str]
    last_tool_result: ToolResult | None = None


class WorkspaceSnapshot(StrictModel):
    """The complete Workspace state used only by execution and verification."""

    schema_version: SchemaVersion = "1"
    files: dict[str, str]


class VerifierCheck(StrictModel):
    """One public pass/fail check without private expected values."""

    name: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class VerifierResult(StrictModel):
    """State-based verifier output."""

    schema_version: SchemaVersion = "1"
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    checks: list[VerifierCheck]


class Event(StrictModel):
    """One ordered, publicly inspectable M1 event."""

    schema_version: SchemaVersion = "1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    event_kind: EventKind
    payload: dict[str, JsonValue]


class RunResult(StrictModel):
    """The terminal result and relative references for one run."""

    schema_version: SchemaVersion = "1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    outcome: TerminalOutcome
    verifier: VerifierResult
    events_file: str = "events.jsonl"
    workspace_dir: str = "workspace"
    limitation: str = (
        "Fault-free M1 system-correctness slice; no reliability or model benchmark claim."
    )
