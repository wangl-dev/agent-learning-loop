"""Strict public contracts for the synchronous M2 Runtime artifacts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from agent_learning_loop.schemas import Action, StrictModel, ToolName, ToolResult, VerifierResult


class RuntimeMode(StrEnum):
    NAIVE = "naive"
    RETRY_ONLY = "retry_only"
    SAFEGUARDED = "safeguarded"


class RuntimeState(StrEnum):
    CREATED = "CREATED"
    RESETTING = "RESETTING"
    READY = "READY"
    DECIDING = "DECIDING"
    VALIDATING_ACTION = "VALIDATING_ACTION"
    EXECUTING_TOOL = "EXECUTING_TOOL"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMED_OUT = "TIMED_OUT"


TERMINAL_STATES = frozenset(
    {
        RuntimeState.SUCCEEDED,
        RuntimeState.FAILED,
        RuntimeState.REJECTED,
        RuntimeState.BUDGET_EXHAUSTED,
        RuntimeState.TIMED_OUT,
    }
)


class ErrorCategory(StrEnum):
    SCHEMA_VALIDATION = "schema_validation"
    POLICY_REJECTION = "policy_rejection"
    TOOL_TRANSIENT = "tool_transient"
    TOOL_PERMANENT = "tool_permanent"
    ENVIRONMENT_ERROR = "environment_error"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INTERNAL_ERROR = "internal_error"


class ErrorPhase(StrEnum):
    RESETTING = "resetting"
    DECIDING = "deciding"
    VALIDATING_ACTION = "validating_action"
    EXECUTING_TOOL = "executing_tool"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    INTERNAL = "internal"


RuntimeEventKind = Literal[
    "run_started",
    "runtime_state_changed",
    "attempt_started",
    "attempt_completed",
    "failure_injected",
    "retry_scheduled",
    "idempotency_hit",
    "idempotency_conflict",
    "run_finished",
]


class RuntimeConfig(StrictModel):
    """The real switches and budgets used for one M2 run."""

    schema_version: Literal["2"] = "2"
    mode: RuntimeMode
    max_steps: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0.0)
    retry_enabled: bool
    max_attempts: int = Field(ge=1)
    retry_backoff_seconds: list[float]
    idempotency_enabled: bool
    schedule_id: str = Field(min_length=1)
    schedule_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    seed: int

    @model_validator(mode="after")
    def switches_match_mode(self) -> Self:
        expected = {
            RuntimeMode.NAIVE: (False, False, 1),
            RuntimeMode.RETRY_ONLY: (True, False, 2),
            RuntimeMode.SAFEGUARDED: (True, True, 2),
        }[self.mode]
        actual = (self.retry_enabled, self.idempotency_enabled, self.max_attempts)
        if actual != expected:
            raise ValueError("Runtime mode does not match its retry/idempotency switches")
        if any(delay < 0.0 for delay in self.retry_backoff_seconds):
            raise ValueError("retry backoff values must be non-negative")
        if self.retry_enabled and len(self.retry_backoff_seconds) < self.max_attempts - 1:
            raise ValueError("retry backoff plan must cover every allowed retry")
        if not self.retry_enabled and self.retry_backoff_seconds:
            raise ValueError("naive mode cannot carry a retry backoff plan")
        return self

    @classmethod
    def for_mode(
        cls,
        mode: RuntimeMode,
        *,
        schedule_id: str,
        seed: int,
        schedule_fingerprint: str | None = None,
        max_steps: int = 8,
        max_tool_calls: int = 12,
        timeout_seconds: float = 30.0,
        retry_backoff_seconds: list[float] | None = None,
    ) -> RuntimeConfig:
        retry_enabled = mode is not RuntimeMode.NAIVE
        return cls(
            mode=mode,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout_seconds,
            retry_enabled=retry_enabled,
            max_attempts=2 if retry_enabled else 1,
            retry_backoff_seconds=(
                list(retry_backoff_seconds or [0.0]) if retry_enabled else []
            ),
            idempotency_enabled=mode is RuntimeMode.SAFEGUARDED,
            schedule_id=schedule_id,
            schedule_fingerprint=schedule_fingerprint,
            seed=seed,
        )


class ErrorRecord(StrictModel):
    """A stable public failure record without raw exception details."""

    schema_version: Literal["2"] = "2"
    category: ErrorCategory
    phase: ErrorPhase
    retryable: bool
    step_index: int = Field(ge=0)
    attempt: int = Field(ge=0)
    tool_name: ToolName | None = None
    detail: str = Field(min_length=1, max_length=160)

    @field_validator("detail")
    @classmethod
    def reject_exception_or_path_details(cls, detail: str) -> str:
        blocked = (
            "traceback",
            "most recent call",
            "\\" + "users" + "\\",
            "/" + "home" + "/",
        )
        if any(token in detail.lower() for token in blocked):
            raise ValueError("public error detail contains private exception or path data")
        return detail


class BudgetUsage(StrictModel):
    schema_version: Literal["2"] = "2"
    steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    physical_executions: int = Field(default=0, ge=0)
    side_effect_executions: int = Field(default=0, ge=0)
    duplicate_side_effects: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    idempotency_hits: int = Field(default=0, ge=0)
    backoff_seconds: float = Field(default=0.0, ge=0.0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)


class ToolInvocation(StrictModel):
    schema_version: Literal["2"] = "2"
    action: Action
    idempotency_key: str | None = Field(default=None, min_length=1)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class IdempotencyLookup(StrictModel):
    schema_version: Literal["2"] = "2"
    result: ToolResult | None = None
    conflict: bool = False


class RuntimeEvent(StrictModel):
    schema_version: Literal["2"] = "2"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    step_index: int = Field(ge=0)
    attempt: int = Field(default=0, ge=0)
    event_kind: RuntimeEventKind
    payload: dict[str, JsonValue]


class RuntimeResult(StrictModel):
    schema_version: Literal["2"] = "2"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    terminal_state: RuntimeState
    config: RuntimeConfig
    error: ErrorRecord | None = None
    verifier: VerifierResult
    usage: BudgetUsage
    schedule_id: str = Field(min_length=1)
    schedule_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    events_file: str = "events.jsonl"
    workspace_dir: str = "workspace"
    limitation: str = (
        "Controlled synchronous M2 Runtime experiment; no model or production timeout claim."
    )
