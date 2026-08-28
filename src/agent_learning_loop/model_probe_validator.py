"""Zero-execution reconstruction of one M7C-A probe bundle."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from agent_learning_loop.canonical import canonical_json_bytes, canonical_sha256
from agent_learning_loop.eval_bundle import canonical_json_text
from agent_learning_loop.model_probe_projection import (
    build_validation_prefixes,
    source_eval_manifest,
)
from agent_learning_loop.model_probe_runner import (
    PROBE_ARTIFACT_PATHS,
    derive_probe_record,
    render_probe_report,
    summarize_probe_records,
)
from agent_learning_loop.model_probe_schemas import (
    LocalModelSpec,
    ModelBackendInput,
    ProbeBundleManifest,
    ProbeSummary,
    ProbeValidationResult,
    RawProbeRecord,
    StrictProbeRecord,
    ValidationPrefix,
    require_completed_qwen_runtime,
)
from agent_learning_loop.model_probe_specs import load_probe_contract, select_model_spec


class ModelProbeValidationError(ValueError):
    """A probe bundle is malformed, inconsistent, or not fixed-source derived."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path, model: type[RawProbeRecord] | type[StrictProbeRecord]) -> list[object]:
    try:
        raw = path.read_bytes()
        if b"\r" in raw or (raw and not raw.endswith(b"\n")):
            raise ModelProbeValidationError("probe_jsonl_encoding")
        return [model.model_validate_json(line) for line in raw.splitlines()]
    except ModelProbeValidationError:
        raise
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ModelProbeValidationError("invalid_probe_jsonl") from exc


def _expected_fake_generation(prefix: ValidationPrefix) -> str:
    action = prefix.reference_action
    payload = canonical_json_bytes(
        {"name": action.tool_name, "arguments": action.arguments}
    ).decode("utf-8")
    return f"<tool_call>{payload}</tool_call>"


def _validate_fake_runtime(
    raw: RawProbeRecord, prefix: ValidationPrefix, spec: LocalModelSpec
) -> None:
    if raw.generation.raw_generation != _expected_fake_generation(prefix):
        raise ModelProbeValidationError("fake_generation_identity")
    model_input = ModelBackendInput(
        prefix_id=prefix.prefix_id,
        prompt_fingerprint=prefix.prompt_fingerprint,
        messages=prefix.messages,
        tools=prefix.tools,
    )
    metrics = raw.generation.metrics
    if (
        raw.generation.input_tokens
        != len(canonical_json_bytes(model_input.model_dump(mode="json")))
        or raw.generation.output_tokens
        != len(raw.generation.raw_generation.encode("utf-8"))
        or raw.generation.finish_reason != "fake"
        or raw.generation.formatted_prompt_sha256 != prefix.prompt_fingerprint
        or raw.generation.chat_template_sha256 != spec.chat_template_sha256
        or metrics.model_dump(mode="json")
        != {
            "torch_version": spec.expected_torch_version,
            "transformers_version": spec.expected_transformers_version,
            "cuda_runtime": "not-used",
            "driver_version": "not-used",
            "gpu_name": "fake-cpu",
            "wall_time_ms": 0,
            "peak_allocated_bytes": 0,
            "peak_reserved_bytes": 0,
            "free_vram_bytes": 0,
            "total_vram_bytes": 0,
        }
    ):
        raise ModelProbeValidationError("fake_generation_metrics")


def validate_model_probe(
    bundle: Path,
    source_eval: Path,
) -> ProbeValidationResult:
    """Rebuild identities, parsing, metrics, and report without a backend or execution."""
    try:
        if bundle.is_symlink() or not bundle.is_dir():
            raise ModelProbeValidationError("invalid_probe_directory")
        actual = {
            path.relative_to(bundle).as_posix()
            for path in bundle.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        if actual != {"probe-manifest.json", *PROBE_ARTIFACT_PATHS}:
            raise ModelProbeValidationError("probe_artifact_inventory")
        manifest = ProbeBundleManifest.model_validate_json(
            (bundle / "probe-manifest.json").read_text(encoding="utf-8")
        )
        spec = select_model_spec(manifest.model_id)
        if (
            manifest.model_spec_id != spec.spec_id
            or manifest.backend_kind != spec.backend_kind
            or manifest.model_revision != spec.revision
            or manifest.chat_template_sha256 != spec.chat_template_sha256
            or manifest.generation_fingerprint != spec.generation_fingerprint
            or manifest.seed != spec.generation.seed
        ):
            raise ModelProbeValidationError("model_spec_identity")
        contract = load_probe_contract()
        if manifest.source_commit != contract.public_source_commit:
            raise ModelProbeValidationError("source_eval_public_commit")
        full_tasks = [item.task_id for item in contract.validation_tasks]
        allowed_selections = (full_tasks, list(contract.smoke_task_ids))
        if manifest.selected_task_ids not in allowed_selections:
            raise ModelProbeValidationError("probe_task_selection_identity")
        source = source_eval_manifest(source_eval)
        if (
            manifest.source_commit != source.source_commit
            or manifest.source_eval_fingerprint != source.bundle_fingerprint
        ):
            raise ModelProbeValidationError("source_eval_identity")
        prefixes = build_validation_prefixes(
            source_eval, selected_task_ids=manifest.selected_task_ids
        )
        if manifest.prefix_total != len(prefixes):
            raise ModelProbeValidationError("prefix_denominator")
        artifact_paths = [artifact.path for artifact in manifest.artifacts]
        if artifact_paths != list(PROBE_ARTIFACT_PATHS):
            raise ModelProbeValidationError("probe_artifact_manifest")
        for artifact in manifest.artifacts:
            if _sha256(bundle / artifact.path) != artifact.sha256:
                raise ModelProbeValidationError("probe_artifact_hash")
        raw_records = _read_jsonl(bundle / "raw-generations.jsonl", RawProbeRecord)
        saved_records = _read_jsonl(bundle / "records.jsonl", StrictProbeRecord)
        if manifest.status == "capacity_blocked":
            if (
                spec.model_id != "Qwen/Qwen3-1.7B"
                or manifest.capacity_evidence is None
                or raw_records
                or saved_records
            ):
                raise ModelProbeValidationError("capacity_evidence_identity")
            expected_summary = summarize_probe_records(
                prefixes, [], status="capacity_blocked"
            )
        else:
            if manifest.capacity_evidence is not None or len(raw_records) != len(prefixes):
                raise ModelProbeValidationError("completed_record_denominator")
            derived: list[StrictProbeRecord] = []
            for raw_object, prefix in zip(raw_records, prefixes, strict=True):
                raw = RawProbeRecord.model_validate(raw_object)
                if (
                    raw.prefix_id != prefix.prefix_id
                    or raw.task_id != prefix.task_id
                    or raw.environment != prefix.environment
                    or raw.step_index != prefix.step_index
                    or raw.prompt_fingerprint != prefix.prompt_fingerprint
                    or raw.reference_fingerprint != prefix.reference_fingerprint
                    or raw.model_spec_id != spec.spec_id
                    or raw.model_id != spec.model_id
                    or raw.model_revision != spec.revision
                    or raw.generation_fingerprint != spec.generation_fingerprint
                    or raw.seed != spec.generation.seed
                    or raw.generation.chat_template_sha256
                    != spec.chat_template_sha256
                ):
                    raise ModelProbeValidationError("raw_probe_identity")
                if spec.backend_kind == "fake":
                    _validate_fake_runtime(raw, prefix, spec)
                else:
                    try:
                        require_completed_qwen_runtime(spec, raw.generation)
                    except ValueError as exc:
                        raise ModelProbeValidationError(
                            "qwen_generation_runtime"
                        ) from exc
                derived.append(derive_probe_record(raw, prefix))
            normalized_saved = [
                StrictProbeRecord.model_validate(record) for record in saved_records
            ]
            if normalized_saved != derived:
                raise ModelProbeValidationError("records_not_raw_derived")
            expected_summary = summarize_probe_records(prefixes, derived)
        try:
            saved_summary = ProbeSummary.model_validate_json(
                (bundle / "summary.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise ModelProbeValidationError("invalid_probe_summary") from exc
        if saved_summary != expected_summary:
            raise ModelProbeValidationError("summary_not_records_derived")
        if manifest.bundle_fingerprint != canonical_sha256(
            manifest.model_dump(mode="json", exclude={"bundle_fingerprint"})
        ):
            raise ModelProbeValidationError("probe_bundle_fingerprint")
        expected_report = render_probe_report(manifest, expected_summary)
        if (bundle / "report.md").read_text(encoding="utf-8") != expected_report:
            raise ModelProbeValidationError("report_not_summary_derived")
        # Ensure the stored pretty JSON is deterministic too.
        if (bundle / "summary.json").read_text(encoding="utf-8") != canonical_json_text(
            expected_summary
        ):
            raise ModelProbeValidationError("summary_bytes")
        return ProbeValidationResult(
            status=manifest.status,
            task_total=expected_summary.task_total,
            prefix_total=expected_summary.prefix_total,
            exact_match_prefixes=expected_summary.exact_match_prefixes,
            all_prefix_exact_tasks=expected_summary.all_prefix_exact_tasks,
        )
    except ModelProbeValidationError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError, TypeError) as exc:
        raise ModelProbeValidationError("invalid_model_probe_bundle") from exc
