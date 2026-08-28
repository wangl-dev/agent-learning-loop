"""Provider-neutral backend boundary plus isolated fake and Qwen3 adapters."""

from __future__ import annotations

import gc
import hashlib
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agent_learning_loop.canonical import canonical_json_bytes
from agent_learning_loop.model_probe_schemas import (
    CapacityEvidence,
    LocalModelSpec,
    ModelBackendInput,
    ModelGeneration,
    ModelRuntimeMetrics,
)

QWEN_LICENSE_SHA256 = "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e"


class ModelProbeBackendError(RuntimeError):
    """A local model dependency, identity, or device boundary failed."""


class ModelProbeCapacityError(ModelProbeBackendError):
    """The required BF16 CUDA attempt exhausted device capacity."""

    def __init__(self, evidence: CapacityEvidence) -> None:
        super().__init__("cuda_out_of_memory")
        self.evidence = evidence


class ModelBackendProtocol(Protocol):
    """One already-projected prompt in, one raw generation plus measurements out."""

    @property
    def spec(self) -> LocalModelSpec: ...

    def generate(self, model_input: ModelBackendInput) -> ModelGeneration: ...

    def close(self) -> None: ...


def exact_fake_generation(tool_name: str, arguments: Mapping[str, object]) -> str:
    payload = canonical_json_bytes({"name": tool_name, "arguments": dict(arguments)})
    return f"<tool_call>{payload.decode('utf-8')}</tool_call>"


class FakeModelBackend:
    """Deterministic CI backend keyed only by a provider-neutral prompt fingerprint."""

    def __init__(
        self,
        spec: LocalModelSpec,
        responses: Mapping[str, str],
    ) -> None:
        if spec.backend_kind != "fake":
            raise ModelProbeBackendError("fake_backend_spec_mismatch")
        self._spec = spec
        self._responses = dict(responses)

    @property
    def spec(self) -> LocalModelSpec:
        return self._spec

    def generate(self, model_input: ModelBackendInput) -> ModelGeneration:
        try:
            raw = self._responses[model_input.prompt_fingerprint]
        except KeyError as exc:
            raise ModelProbeBackendError("fake_response_missing") from exc
        return ModelGeneration(
            raw_generation=raw,
            input_tokens=len(canonical_json_bytes(model_input.model_dump(mode="json"))),
            output_tokens=len(raw.encode("utf-8")),
            finish_reason="fake",
            formatted_prompt_sha256=model_input.prompt_fingerprint,
            chat_template_sha256=self.spec.chat_template_sha256,
            metrics=ModelRuntimeMetrics(
                torch_version=self.spec.expected_torch_version,
                transformers_version=self.spec.expected_transformers_version,
                cuda_runtime="not-used",
                driver_version="not-used",
                gpu_name="fake-cpu",
                wall_time_ms=0,
                peak_allocated_bytes=0,
                peak_reserved_bytes=0,
                free_vram_bytes=0,
                total_vram_bytes=0,
            ),
        )

    def close(self) -> None:
        return None


def render_qwen_prompt(tokenizer: object, model_input: ModelBackendInput) -> str:
    """Apply the official tokenizer template with tools and thinking disabled."""
    apply = getattr(tokenizer, "apply_chat_template", None)
    if not callable(apply):
        raise ModelProbeBackendError("qwen_chat_template_missing")
    messages = [message.model_dump(mode="json") for message in model_input.messages]
    tools = [tool.model_dump(mode="json") for tool in model_input.tools]
    rendered = apply(
        messages,
        tools=tools,
        add_generation_prompt=True,
        enable_thinking=False,
        tokenize=False,
    )
    if not isinstance(rendered, str) or not rendered:
        raise ModelProbeBackendError("qwen_chat_template_invalid")
    return rendered


def _driver_version() -> str:
    try:
        import subprocess

        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        value = completed.stdout.splitlines()[0].strip()
        return value or "unavailable"
    except (OSError, IndexError, RuntimeError, subprocess.SubprocessError):
        return "unavailable"


class Qwen3LocalBackend:
    """BF16, batch-one, CUDA-only Qwen3 adapter with no network fallback."""

    def __init__(self, spec: LocalModelSpec, snapshot_dir: Path) -> None:
        if spec.backend_kind != "qwen3" or spec.device != "cuda":
            raise ModelProbeBackendError("qwen_backend_spec_mismatch")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        try:
            import torch  # type: ignore[import-not-found]
            import transformers  # type: ignore[import-not-found]
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise ModelProbeBackendError("model_probe_extra_missing") from exc
        if snapshot_dir.name != spec.revision or not snapshot_dir.is_dir():
            raise ModelProbeBackendError("qwen_snapshot_revision_mismatch")
        license_path = snapshot_dir / "LICENSE"
        try:
            license_sha = hashlib.sha256(license_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ModelProbeBackendError("qwen_license_missing") from exc
        if license_sha != QWEN_LICENSE_SHA256:
            raise ModelProbeBackendError("qwen_license_mismatch")
        if not torch.cuda.is_available():
            raise ModelProbeBackendError("cuda_required_no_cpu_fallback")
        self._spec = spec
        self._torch = torch
        self._transformers = transformers
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                snapshot_dir,
                local_files_only=True,
                trust_remote_code=False,
            )
            template = self._tokenizer.chat_template
            if not isinstance(template, str) or hashlib.sha256(
                template.encode("utf-8")
            ).hexdigest() != spec.chat_template_sha256:
                raise ModelProbeBackendError("qwen_chat_template_identity")
            loaded_model: Any = AutoModelForCausalLM.from_pretrained(
                snapshot_dir,
                local_files_only=True,
                trust_remote_code=False,
                torch_dtype=torch.bfloat16,
            )
            loaded_model.to("cuda")
            loaded_model.eval()
            self._model = loaded_model
        except torch.OutOfMemoryError as exc:
            evidence = self._capacity_evidence()
            self.close()
            raise ModelProbeCapacityError(evidence) from exc
        except ModelProbeBackendError:
            self.close()
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            self.close()
            raise ModelProbeBackendError("qwen_model_load_failed") from exc

    @property
    def spec(self) -> LocalModelSpec:
        return self._spec

    def _capacity_evidence(self) -> CapacityEvidence:
        torch = self._torch
        try:
            free, total = torch.cuda.mem_get_info()
            peak_allocated = torch.cuda.max_memory_allocated()
            peak_reserved = torch.cuda.max_memory_reserved()
        except RuntimeError:
            free = total = peak_allocated = peak_reserved = 0
        return CapacityEvidence(
            category="cuda_out_of_memory",
            free_vram_bytes=int(free),
            total_vram_bytes=int(total),
            peak_allocated_bytes=int(peak_allocated),
            peak_reserved_bytes=int(peak_reserved),
        )

    def generate(self, model_input: ModelBackendInput) -> ModelGeneration:
        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model
        if tokenizer is None or model is None:
            raise ModelProbeBackendError("qwen_backend_closed")
        rendered = render_qwen_prompt(tokenizer, model_input)
        encoded = tokenizer(rendered, return_tensors="pt", add_special_tokens=False)
        input_tokens = int(encoded["input_ids"].shape[-1])
        if input_tokens > self.spec.max_input_tokens:
            raise ModelProbeBackendError("input_token_limit_exceeded")
        encoded = {name: tensor.to("cuda") for name, tensor in encoded.items()}
        config = self.spec.generation
        torch.manual_seed(config.seed)
        torch.cuda.manual_seed_all(config.seed)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.perf_counter()
        try:
            with torch.inference_mode():
                output = model.generate(
                    **encoded,
                    do_sample=config.do_sample,
                    temperature=config.temperature,
                    top_p=config.top_p,
                    top_k=config.top_k,
                    repetition_penalty=config.repetition_penalty,
                    max_new_tokens=config.max_new_tokens,
                    pad_token_id=tokenizer.eos_token_id,
                )
            torch.cuda.synchronize()
        except torch.OutOfMemoryError as exc:
            raise ModelProbeCapacityError(self._capacity_evidence()) from exc
        wall_time_ms = int(round((time.perf_counter() - started) * 1000))
        generated_ids = output[0, input_tokens:]
        output_tokens = int(generated_ids.shape[-1])
        raw = tokenizer.decode(generated_ids, skip_special_tokens=True)
        free, total = torch.cuda.mem_get_info()
        return ModelGeneration(
            raw_generation=raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=(
                "length" if output_tokens == config.max_new_tokens else "stop"
            ),
            formatted_prompt_sha256=hashlib.sha256(
                rendered.encode("utf-8")
            ).hexdigest(),
            chat_template_sha256=self.spec.chat_template_sha256,
            metrics=ModelRuntimeMetrics(
                torch_version=str(torch.__version__),
                transformers_version=str(self._transformers.__version__),
                cuda_runtime=str(torch.version.cuda),
                driver_version=_driver_version(),
                gpu_name=str(torch.cuda.get_device_name(0)),
                wall_time_ms=wall_time_ms,
                peak_allocated_bytes=int(torch.cuda.max_memory_allocated()),
                peak_reserved_bytes=int(torch.cuda.max_memory_reserved()),
                free_vram_bytes=int(free),
                total_vram_bytes=int(total),
            ),
        )

    def close(self) -> None:
        model = getattr(self, "_model", None)
        tokenizer = getattr(self, "_tokenizer", None)
        self._model = None
        self._tokenizer = None
        del model
        del tokenizer
        gc.collect()
        torch = getattr(self, "_torch", None)
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
