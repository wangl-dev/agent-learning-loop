"""Strict, provider-neutral contracts for the M7C-A next-action probe."""

from __future__ import annotations

import json
import re
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.schemas import StrictModel

ProbeEnvironment = Literal["workspace", "incident", "dataops"]
ProbeBackendKind = Literal["fake", "qwen3"]
ProbeRunStatus = Literal["completed", "capacity_blocked"]
ProbeFinishReason = Literal["stop", "length", "fake", "capacity_blocked"]
ProbeCategory = Literal[
    "no_tool_call",
    "incomplete_tool_call",
    "multiple_tool_calls",
    "text_outside_tool_call",
    "invalid_json",
    "tool_call_not_object",
    "arguments_not_object",
    "unknown_tool",
    "arguments_schema_invalid",
    "scope_violation",
    "reference_mismatch",
    "exact_match",
    "capacity_blocked",
]


class ProbeAction(StrictModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]


class ProbeMessage(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ProbeToolDefinition(StrictModel):
    type: Literal["function"] = "function"
    function: dict[str, JsonValue]

    @model_validator(mode="after")
    def require_function_contract(self) -> Self:
        if set(self.function) != {"name", "description", "parameters"}:
            raise ValueError("tool_definition_fields")
        if not isinstance(self.function["name"], str) or not self.function["name"]:
            raise ValueError("tool_definition_name")
        if not isinstance(self.function["description"], str):
            raise ValueError("tool_definition_description")
        if not isinstance(self.function["parameters"], dict):
            raise ValueError("tool_definition_parameters")
        return self

    @property
    def name(self) -> str:
        return str(self.function["name"])


class ProbeTaskContext(StrictModel):
    instruction: str = Field(min_length=1)
    allowed_tools: list[str] = Field(min_length=1)
    public_scope: list[dict[str, JsonValue]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


_MAX_REFERENCE_SCAN_CHARS = 65_536
_MAX_REFERENCE_JSON_VALUES = 1_024
_MAX_REFERENCE_JSON_NODES = 2_048
_MAX_ENCODED_JSON_DEPTH = 2


def _json_values_in_text(content: str) -> tuple[JsonValue, ...]:
    if len(content) > _MAX_REFERENCE_SCAN_CHARS:
        raise ValueError("reference_json_scan_limit")
    decoder = json.JSONDecoder()
    values: list[JsonValue] = []
    try:
        complete = decoder.decode(content)
    except json.JSONDecodeError:
        pass
    else:
        if (
            isinstance(complete, (dict, list, str, int, float, bool))
            or complete is None
        ):
            values.append(complete)
    candidate_count = 0
    for match in re.finditer(r'["{\[]', content):
        candidate_count += 1
        if candidate_count > _MAX_REFERENCE_JSON_VALUES:
            raise ValueError("reference_json_value_limit")
        try:
            value, _ = decoder.raw_decode(content, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            values.append(value)
    return tuple(values)


def _contains_reference_action(value: JsonValue, reference: ProbeAction) -> bool:
    pending: list[tuple[JsonValue, int]] = [(value, 0)]
    visited = 0
    while pending:
        current, encoded_depth = pending.pop()
        visited += 1
        if visited > _MAX_REFERENCE_JSON_NODES:
            raise ValueError("reference_json_node_limit")
        if isinstance(current, list):
            pending.extend((item, encoded_depth) for item in current)
            continue
        if isinstance(current, dict):
            arguments = current.get("arguments")
            if arguments == reference.arguments and (
                current.get("tool_name") == reference.tool_name
                or current.get("name") == reference.tool_name
            ):
                return True
            pending.extend((item, encoded_depth) for item in current.values())
            continue
        if isinstance(current, str):
            if len(current) > _MAX_REFERENCE_SCAN_CHARS:
                raise ValueError("reference_json_scan_limit")
            try:
                decoded = json.loads(current)
            except json.JSONDecodeError:
                continue
            if encoded_depth >= _MAX_ENCODED_JSON_DEPTH:
                raise ValueError("reference_json_depth_limit")
            if (
                isinstance(decoded, (dict, list, str, int, float, bool))
                or decoded is None
            ):
                pending.append((decoded, encoded_depth + 1))
    return False


class ValidationPrefix(StrictModel):
    schema_version: Literal["1"] = "1"
    prefix_id: str = Field(pattern=r"^probe\.[a-z0-9._-]+\.step-[1-9][0-9]*$")
    task_id: str = Field(min_length=1)
    environment: ProbeEnvironment
    step_index: int = Field(ge=1)
    task: ProbeTaskContext
    messages: list[ProbeMessage] = Field(min_length=2)
    tools: list[ProbeToolDefinition] = Field(min_length=1)
    reference_action: ProbeAction
    prompt_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        if self.reference_action.tool_name not in self.task.allowed_tools:
            raise ValueError("reference_tool_not_allowed")
        tool_names = [tool.name for tool in self.tools]
        if tool_names != self.task.allowed_tools or len(tool_names) != len(set(tool_names)):
            raise ValueError("tool_definition_identity")
        if self.reference_fingerprint != canonical_sha256(
            self.reference_action.model_dump(mode="json")
        ):
            raise ValueError("reference_fingerprint")
        expected_prompt = canonical_sha256(
            {
                "task_id": self.task_id,
                "environment": self.environment,
                "step_index": self.step_index,
                "task": self.task.model_dump(mode="json"),
                "messages": [message.model_dump(mode="json") for message in self.messages],
                "tools": [tool.model_dump(mode="json") for tool in self.tools],
            }
        )
        if self.prompt_fingerprint != expected_prompt:
            raise ValueError("prompt_fingerprint")
        if any(
            any(
                _contains_reference_action(value, self.reference_action)
                for value in _json_values_in_text(message.content)
            )
            for message in self.messages
        ):
            raise ValueError("reference_action_in_prompt")
        return self


class GenerationConfig(StrictModel):
    do_sample: Literal[True] = True
    temperature: float = Field(gt=0.0)
    top_p: float = Field(gt=0.0, le=1.0)
    top_k: int = Field(gt=0)
    repetition_penalty: float = Field(gt=0.0)
    max_new_tokens: int = Field(gt=0)
    seed: int = Field(ge=0)


class LocalModelSpec(StrictModel):
    schema_version: Literal["1"] = "1"
    spec_id: str = Field(min_length=1)
    backend_kind: ProbeBackendKind
    model_id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: Literal["Apache-2.0"]
    chat_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_input_tokens: Literal[4096] = 4096
    device: Literal["cuda", "cpu"]
    precision: Literal["bfloat16", "fake"]
    batch_size: Literal[1] = 1
    trust_remote_code: Literal[False] = False
    expected_torch_version: str = Field(min_length=1)
    expected_transformers_version: str = Field(min_length=1)
    generation: GenerationConfig

    @property
    def generation_fingerprint(self) -> str:
        return canonical_sha256(self.generation.model_dump(mode="json"))


class ProbeTaskRegistration(StrictModel):
    task_id: str
    environment: ProbeEnvironment
    cell_id: str


class ProbeContractSpec(StrictModel):
    schema_version: Literal["1"] = "1"
    contract_version: Literal["m7ca-v1"] = "m7ca-v1"
    public_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    validation_tasks: list[ProbeTaskRegistration] = Field(min_length=6, max_length=6)
    smoke_task_ids: list[str] = Field(min_length=3, max_length=3)
    models: list[LocalModelSpec] = Field(min_length=3, max_length=3)
    contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_registered_identity(self) -> Self:
        task_ids = [item.task_id for item in self.validation_tasks]
        cell_ids = [item.cell_id for item in self.validation_tasks]
        model_ids = [item.model_id for item in self.models]
        if len(task_ids) != len(set(task_ids)) or len(cell_ids) != len(set(cell_ids)):
            raise ValueError("duplicate_probe_task")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("duplicate_probe_model")
        if not set(self.smoke_task_ids) <= set(task_ids):
            raise ValueError("smoke_task_not_validation")
        payload = self.model_dump(mode="json", exclude={"contract_fingerprint"})
        if self.contract_fingerprint != canonical_sha256(payload):
            raise ValueError("probe_contract_fingerprint")
        return self


class ModelRuntimeMetrics(StrictModel):
    torch_version: str
    transformers_version: str
    cuda_runtime: str
    driver_version: str
    gpu_name: str
    wall_time_ms: int = Field(ge=0)
    peak_allocated_bytes: int = Field(ge=0)
    peak_reserved_bytes: int = Field(ge=0)
    free_vram_bytes: int = Field(ge=0)
    total_vram_bytes: int = Field(ge=0)


class ModelBackendInput(StrictModel):
    prefix_id: str
    prompt_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    messages: list[ProbeMessage] = Field(min_length=2)
    tools: list[ProbeToolDefinition] = Field(min_length=1)


class ModelGeneration(StrictModel):
    raw_generation: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    finish_reason: ProbeFinishReason
    formatted_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chat_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: ModelRuntimeMetrics


def require_completed_qwen_runtime(
    spec: LocalModelSpec,
    generation: ModelGeneration,
) -> None:
    """Reject internally impossible Qwen completion evidence without model imports."""
    if spec.backend_kind != "qwen3":
        return
    output_limit = spec.generation.max_new_tokens
    metrics = generation.metrics
    cuda_version = re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", metrics.cuda_runtime)
    driver_version = re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", metrics.driver_version)
    gpu_name = metrics.gpu_name.casefold()
    if (
        generation.finish_reason not in {"stop", "length"}
        or not 1 <= generation.input_tokens <= spec.max_input_tokens
        or not 1 <= generation.output_tokens <= output_limit
        or (generation.finish_reason == "length")
        != (generation.output_tokens == output_limit)
        or metrics.torch_version != spec.expected_torch_version
        or metrics.transformers_version != spec.expected_transformers_version
        or cuda_version is None
        or driver_version is None
        or not gpu_name
        or "cpu" in gpu_name
        or "fake" in gpu_name
        or metrics.wall_time_ms <= 0
        or metrics.total_vram_bytes <= 0
        or metrics.free_vram_bytes > metrics.total_vram_bytes
        or metrics.peak_allocated_bytes <= 0
        or metrics.peak_reserved_bytes < metrics.peak_allocated_bytes
        or metrics.peak_reserved_bytes > metrics.total_vram_bytes
    ):
        raise ValueError("qwen_generation_runtime")


class ToolCallParseResult(StrictModel):
    category: ProbeCategory
    one_tool_call_detected: bool
    json_object_valid: bool
    known_allowed_tool: bool
    arguments_schema_valid: bool
    exact_reference_match: bool
    action: ProbeAction | None = None


class RawProbeRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    prefix_id: str
    task_id: str
    environment: ProbeEnvironment
    step_index: int = Field(ge=1)
    prompt_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_spec_id: str
    model_id: str
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    generation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0)
    generation: ModelGeneration


class StrictProbeRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    prefix_id: str
    task_id: str
    environment: ProbeEnvironment
    step_index: int = Field(ge=1)
    prompt_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_generation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: ProbeCategory
    generation_completed: bool
    one_tool_call_detected: bool
    json_object_valid: bool
    known_allowed_tool: bool
    arguments_schema_valid: bool
    exact_reference_match: bool
    action: ProbeAction | None = None


class ProbeSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    status: ProbeRunStatus
    task_total: int = Field(ge=0)
    prefix_total: int = Field(ge=0)
    generation_completed: int = Field(ge=0)
    one_tool_call_detected: int = Field(ge=0)
    json_object_valid: int = Field(ge=0)
    known_allowed_tool: int = Field(ge=0)
    arguments_schema_valid: int = Field(ge=0)
    exact_match_prefixes: int = Field(ge=0)
    all_prefix_exact_tasks: int = Field(ge=0)
    environment_task_counts: dict[ProbeEnvironment, int]
    environment_prefix_counts: dict[ProbeEnvironment, int]
    failure_categories: dict[str, int]
    execution_calls: Literal[0] = 0
    limitation: str = (
        "Teacher-forced validation next-action feasibility; no action was executed and "
        "the result is not end-to-end task success."
    )


class ProbeArtifact(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CapacityEvidence(StrictModel):
    category: Literal["cuda_out_of_memory"]
    free_vram_bytes: int = Field(ge=0)
    total_vram_bytes: int = Field(ge=0)
    peak_allocated_bytes: int = Field(ge=0)
    peak_reserved_bytes: int = Field(ge=0)
    actions_produced: Literal[0] = 0

    @model_validator(mode="after")
    def require_vram_consistency(self) -> Self:
        if (
            self.total_vram_bytes <= 0
            or self.free_vram_bytes > self.total_vram_bytes
            or self.peak_allocated_bytes > self.peak_reserved_bytes
            or self.peak_reserved_bytes > self.total_vram_bytes
        ):
            raise ValueError("capacity_vram_consistency")
        return self


class ProbeBundleManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    contract_version: Literal["m7ca-v1"] = "m7ca-v1"
    status: ProbeRunStatus
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_eval_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_spec_id: str
    backend_kind: ProbeBackendKind
    model_id: str
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    chat_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0)
    selected_task_ids: list[str] = Field(min_length=1)
    prefix_total: int = Field(ge=0)
    artifacts: list[ProbeArtifact]
    capacity_evidence: CapacityEvidence | None = None
    bundle_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitation: str = (
        "Unkeyed hashes bind internal bytes but are not proof against an actor who can "
        "rewrite an entire real-model bundle."
    )


class ProbeValidationResult(StrictModel):
    schema_version: Literal["1"] = "1"
    valid: Literal[True] = True
    status: ProbeRunStatus
    task_total: int = Field(ge=0)
    prefix_total: int = Field(ge=0)
    exact_match_prefixes: int = Field(ge=0)
    all_prefix_exact_tasks: int = Field(ge=0)
    execution_calls: Literal[0] = 0
