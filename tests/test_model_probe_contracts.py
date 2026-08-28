from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agent_learning_loop.model_probe_backend import render_qwen_prompt
from agent_learning_loop.model_probe_schemas import (
    CapacityEvidence,
    GenerationConfig,
    ModelBackendInput,
    ModelGeneration,
    ModelRuntimeMetrics,
    ProbeMessage,
    ProbeTaskContext,
    require_completed_qwen_runtime,
)
from agent_learning_loop.model_probe_specs import load_probe_contract, select_model_spec
from agent_learning_loop.model_probe_tools import (
    build_tool_definitions,
    validate_public_tool_arguments,
)


def test_fixed_model_revisions_and_generation_contract() -> None:
    contract = load_probe_contract()
    by_id = {spec.model_id: spec for spec in contract.models}

    assert contract.public_source_commit == (
        "65d6c441f4e2be1e2dce3e363bc87f593aab221a"
    )
    assert by_id["Qwen/Qwen3-0.6B"].revision == (
        "c1899de289a04d12100db370d81485cdf75e47ca"
    )
    assert by_id["Qwen/Qwen3-1.7B"].revision == (
        "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
    )
    fake = by_id["agent-learning-loop/fake-tool-model"]
    assert fake.expected_torch_version == "not-installed"
    assert fake.expected_transformers_version == "not-installed"
    for model_id in ("Qwen/Qwen3-0.6B", "Qwen/Qwen3-1.7B"):
        spec = by_id[model_id]
        assert spec.expected_torch_version == "2.7.1+cu126"
        assert spec.expected_transformers_version == "4.53.3"
        assert spec.license == "Apache-2.0"
        assert spec.device == "cuda"
        assert spec.precision == "bfloat16"
        assert spec.batch_size == 1
        assert spec.trust_remote_code is False
        assert spec.generation.model_dump(mode="json") == {
            "do_sample": True,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "repetition_penalty": 1.05,
            "max_new_tokens": 512,
            "seed": 17,
        }


def _valid_qwen_generation() -> ModelGeneration:
    spec = select_model_spec("Qwen/Qwen3-1.7B")
    return ModelGeneration(
        raw_generation="plain answer",
        input_tokens=128,
        output_tokens=2,
        finish_reason="stop",
        formatted_prompt_sha256="1" * 64,
        chat_template_sha256=spec.chat_template_sha256,
        metrics=ModelRuntimeMetrics(
            torch_version="2.7.1+cu126",
            transformers_version="4.53.3",
            cuda_runtime="12.6",
            driver_version="566.26",
            gpu_name="NVIDIA test CUDA GPU",
            wall_time_ms=1,
            peak_allocated_bytes=1_000,
            peak_reserved_bytes=2_000,
            free_vram_bytes=4_000,
            total_vram_bytes=8_000,
        ),
    )


def test_completed_qwen_runtime_accepts_honest_stop_and_length() -> None:
    spec = select_model_spec("Qwen/Qwen3-1.7B")
    stopped = _valid_qwen_generation()
    require_completed_qwen_runtime(spec, stopped)
    require_completed_qwen_runtime(
        spec,
        stopped.model_copy(
            update={
                "finish_reason": "length",
                "output_tokens": spec.generation.max_new_tokens,
            }
        ),
    )


@pytest.mark.parametrize(
    ("generation_update", "metrics_update"),
    [
        ({"finish_reason": "capacity_blocked"}, {}),
        ({"input_tokens": 0}, {}),
        ({"output_tokens": 0}, {}),
        ({"finish_reason": "length", "output_tokens": 2}, {}),
        ({"finish_reason": "stop", "output_tokens": 512}, {}),
        ({}, {"cuda_runtime": "not-used"}),
        ({}, {"torch_version": "fake"}),
        ({}, {"transformers_version": "fabricated"}),
        ({}, {"driver_version": "not-used", "gpu_name": "fake-cpu"}),
        ({}, {"total_vram_bytes": 0}),
        ({}, {"peak_allocated_bytes": 3_000, "peak_reserved_bytes": 2_000}),
        ({}, {"free_vram_bytes": 9_000}),
    ],
)
def test_completed_qwen_runtime_rejects_impossible_evidence(
    generation_update: dict[str, object],
    metrics_update: dict[str, object],
) -> None:
    spec = select_model_spec("Qwen/Qwen3-1.7B")
    generation = _valid_qwen_generation()
    if metrics_update:
        generation_update = {
            **generation_update,
            "metrics": generation.metrics.model_copy(update=metrics_update),
        }

    with pytest.raises(ValueError, match="qwen_generation_runtime"):
        require_completed_qwen_runtime(
            spec,
            generation.model_copy(update=generation_update),
        )


def test_generation_fingerprint_separates_two_seeds() -> None:
    first = GenerationConfig(
        do_sample=True,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repetition_penalty=1.05,
        max_new_tokens=512,
        seed=17,
    )
    second = first.model_copy(update={"seed": 18})

    from agent_learning_loop.canonical import canonical_sha256

    assert canonical_sha256(first.model_dump(mode="json")) != canonical_sha256(
        second.model_dump(mode="json")
    )


def test_capacity_evidence_requires_possible_vram_relationships() -> None:
    honest = {
        "category": "cuda_out_of_memory",
        "free_vram_bytes": 128,
        "total_vram_bytes": 8_000,
        "peak_allocated_bytes": 7_000,
        "peak_reserved_bytes": 7_500,
    }
    evidence = CapacityEvidence.model_validate(honest)
    assert evidence.free_vram_bytes + evidence.peak_reserved_bytes <= 8_000

    for update in (
        {"total_vram_bytes": 0},
        {"free_vram_bytes": 8_001},
        {"peak_allocated_bytes": 7_600},
        {"peak_reserved_bytes": 8_001},
    ):
        with pytest.raises(ValidationError, match="capacity_vram_consistency"):
            CapacityEvidence.model_validate({**honest, **update})

    recovered_free = CapacityEvidence.model_validate(
        {**honest, "free_vram_bytes": 4_000}
    )
    assert recovered_free.free_vram_bytes + recovered_free.peak_reserved_bytes > 8_000


def test_tool_definitions_are_frozen_pydantic_schemas_and_scope_is_strict() -> None:
    context = ProbeTaskContext(
        instruction="Migrate one row.",
        allowed_tools=["update_rows"],
        public_scope=[
            {
                "table": "records",
                "readable_columns": ["id", "value"],
                "mutable_columns": ["value"],
                "predicate_columns": ["id"],
                "max_mutated_rows": 1,
                "allow_insert": False,
            }
        ],
        constraints=["exact cardinality before write"],
    )
    definitions = build_tool_definitions("dataops", context.allowed_tools)
    parameters = definitions[0].function["parameters"]

    assert isinstance(parameters, dict)
    assert parameters["additionalProperties"] is False
    honest = {
        "transaction_id": "tx-1",
        "operation_id": "op-1",
        "table": "records",
        "where": {"id": 1},
        "values": {"value": "current"},
        "expected_match_count": 1,
    }
    assert validate_public_tool_arguments("dataops", context, "update_rows", honest) is None
    assert (
        validate_public_tool_arguments(
            "dataops", context, "update_rows", {**honest, "table": "secrets"}
        )
        == "scope_violation"
    )
    assert (
        validate_public_tool_arguments(
            "dataops", context, "update_rows", {**honest, "expected_match_count": 2}
        )
        == "scope_violation"
    )
    assert (
        validate_public_tool_arguments(
            "dataops", context, "update_rows", {**honest, "extra": "x"}
        )
        == "arguments_schema_invalid"
    )


def test_workspace_scope_rejects_absolute_and_parent_paths() -> None:
    context = ProbeTaskContext(
        instruction="Read one file.",
        allowed_tools=["read_text"],
        constraints=["Stay inside the Workspace."],
    )
    for path in ("../private.txt", "C:\\Users\\name\\private.txt", "/home/name/private.txt"):
        assert (
            validate_public_tool_arguments(
                "workspace", context, "read_text", {"path": path}
            )
            == "scope_violation"
        )
    assert (
        validate_public_tool_arguments(
            "workspace", context, "read_text", {"path": "input/title.txt"}
        )
        is None
    )


def test_qwen_formatter_uses_tools_generation_prompt_and_disables_thinking() -> None:
    calls: list[dict[str, Any]] = []

    class FakeTokenizer:
        def apply_chat_template(self, messages: object, **kwargs: object) -> str:
            calls.append({"messages": messages, **kwargs})
            return "formatted"

    tools = build_tool_definitions("workspace", ["read_text"])
    model_input = ModelBackendInput(
        prefix_id="probe.workspace.example.step-1",
        prompt_fingerprint="0" * 64,
        messages=[
            ProbeMessage(role="system", content="one call"),
            ProbeMessage(role="user", content="read input/title.txt"),
        ],
        tools=tools,
    )

    assert render_qwen_prompt(FakeTokenizer(), model_input) == "formatted"
    assert calls == [
        {
            "messages": [message.model_dump(mode="json") for message in model_input.messages],
            "tools": [tool.model_dump(mode="json") for tool in tools],
            "add_generation_prompt": True,
            "enable_thinking": False,
            "tokenize": False,
        }
    ]
