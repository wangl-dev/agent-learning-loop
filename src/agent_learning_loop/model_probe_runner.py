"""Run teacher-forced next-action probes without executing predicted actions."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.eval_bundle import canonical_json_text
from agent_learning_loop.model_probe_backend import (
    FakeModelBackend,
    ModelBackendProtocol,
    ModelProbeCapacityError,
    Qwen3LocalBackend,
    exact_fake_generation,
)
from agent_learning_loop.model_probe_parser import parse_tool_call
from agent_learning_loop.model_probe_projection import (
    build_validation_prefixes,
    source_eval_manifest,
)
from agent_learning_loop.model_probe_schemas import (
    CapacityEvidence,
    LocalModelSpec,
    ModelBackendInput,
    ModelGeneration,
    ProbeArtifact,
    ProbeBundleManifest,
    ProbeSummary,
    RawProbeRecord,
    StrictProbeRecord,
    ValidationPrefix,
    require_completed_qwen_runtime,
)
from agent_learning_loop.model_probe_specs import load_probe_contract, select_model_spec
from agent_learning_loop.model_probe_tools import validate_public_tool_arguments

PROBE_ARTIFACT_PATHS = (
    "raw-generations.jsonl",
    "records.jsonl",
    "summary.json",
    "report.md",
)


class ModelProbeRunError(RuntimeError):
    """The fixed probe could not produce a trustworthy complete bundle."""


@dataclass(frozen=True)
class ModelProbeRunOutcome:
    status: str
    manifest: ProbeBundleManifest
    summary: ProbeSummary


def _jsonl(models: Sequence[BaseModel]) -> str:
    return "".join(
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
        for model in models
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backend_input(prefix: ValidationPrefix) -> ModelBackendInput:
    return ModelBackendInput(
        prefix_id=prefix.prefix_id,
        prompt_fingerprint=prefix.prompt_fingerprint,
        messages=prefix.messages,
        tools=prefix.tools,
    )


def derive_probe_record(
    raw: RawProbeRecord,
    prefix: ValidationPrefix,
) -> StrictProbeRecord:
    """Derive every normalized field from one raw generation and trusted prefix."""
    parsed = parse_tool_call(
        raw.generation.raw_generation,
        allowed_tools=prefix.task.allowed_tools,
        validate_arguments=lambda name, arguments: validate_public_tool_arguments(
            prefix.environment, prefix.task, name, arguments
        ),
        reference_action=prefix.reference_action,
    )
    return StrictProbeRecord(
        prefix_id=prefix.prefix_id,
        task_id=prefix.task_id,
        environment=prefix.environment,
        step_index=prefix.step_index,
        prompt_fingerprint=prefix.prompt_fingerprint,
        reference_fingerprint=prefix.reference_fingerprint,
        raw_generation_sha256=hashlib.sha256(
            raw.generation.raw_generation.encode("utf-8")
        ).hexdigest(),
        category=parsed.category,
        generation_completed=True,
        one_tool_call_detected=parsed.one_tool_call_detected,
        json_object_valid=parsed.json_object_valid,
        known_allowed_tool=parsed.known_allowed_tool,
        arguments_schema_valid=parsed.arguments_schema_valid,
        exact_reference_match=parsed.exact_reference_match,
        action=parsed.action,
    )


def summarize_probe_records(
    prefixes: tuple[ValidationPrefix, ...],
    records: list[StrictProbeRecord],
    *,
    status: str = "completed",
) -> ProbeSummary:
    task_ids = list(dict.fromkeys(prefix.task_id for prefix in prefixes))
    environment_tasks: Counter[str] = Counter()
    for task_id in task_ids:
        environment = next(prefix.environment for prefix in prefixes if prefix.task_id == task_id)
        environment_tasks[environment] += 1
    environment_prefixes = Counter(prefix.environment for prefix in prefixes)
    failures = Counter(
        record.category for record in records if record.category != "exact_match"
    )
    exact_by_task: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        exact_by_task[record.task_id].append(record.exact_reference_match)
    return ProbeSummary.model_validate(
        {
            "status": status,
            "task_total": len(task_ids),
            "prefix_total": len(prefixes),
            "generation_completed": sum(record.generation_completed for record in records),
            "one_tool_call_detected": sum(
                record.one_tool_call_detected for record in records
            ),
            "json_object_valid": sum(record.json_object_valid for record in records),
            "known_allowed_tool": sum(
                record.known_allowed_tool for record in records
            ),
            "arguments_schema_valid": sum(
                record.arguments_schema_valid for record in records
            ),
            "exact_match_prefixes": sum(
                record.exact_reference_match for record in records
            ),
            "all_prefix_exact_tasks": sum(
                bool(values) and all(values) for values in exact_by_task.values()
            ),
            "environment_task_counts": dict(environment_tasks),
            "environment_prefix_counts": dict(environment_prefixes),
            "failure_categories": dict(sorted(failures.items())),
            "execution_calls": 0,
        }
    )


def render_probe_report(manifest: ProbeBundleManifest, summary: ProbeSummary) -> str:
    lines = [
        "# M7C-A validation next-action probe",
        "",
        f"- status: `{summary.status}`",
        f"- model: `{manifest.model_id}`",
        f"- revision: `{manifest.model_revision}`",
        f"- source Eval: `{manifest.source_eval_fingerprint}`",
        f"- seed: `{manifest.seed}`",
        f"- tasks/prefixes: `{summary.task_total}/{summary.prefix_total}`",
        f"- generation completed: `{summary.generation_completed}/{summary.prefix_total}`",
        f"- one tool call: `{summary.one_tool_call_detected}/{summary.prefix_total}`",
        f"- JSON object valid: `{summary.json_object_valid}/{summary.prefix_total}`",
        f"- known allowed tool: `{summary.known_allowed_tool}/{summary.prefix_total}`",
        f"- arguments valid: `{summary.arguments_schema_valid}/{summary.prefix_total}`",
        f"- exact reference prefixes: `{summary.exact_match_prefixes}/{summary.prefix_total}`",
        f"- all-prefix exact tasks: `{summary.all_prefix_exact_tasks}/{summary.task_total}`",
        "- predicted action executions: `0`",
        "",
        "## Failure categories",
        "",
    ]
    if summary.failure_categories:
        lines.extend(
            f"- `{name}`: `{count}`"
            for name, count in sorted(summary.failure_categories.items())
        )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            (
                "Each prefix uses the correct scripted-oracle history and asks only for the "
                "next action. Predictions were parsed and compared but never executed. This is "
                "not end-to-end task success, a test-set result, a performance benchmark, or a "
                "training result."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _manifest(
    *,
    status: str,
    source_commit: str,
    source_eval_fingerprint: str,
    spec: LocalModelSpec,
    selected_task_ids: list[str],
    prefix_total: int,
    artifacts: list[ProbeArtifact],
    capacity_evidence: CapacityEvidence | None,
) -> ProbeBundleManifest:
    payload = {
        "status": status,
        "source_commit": source_commit,
        "source_eval_fingerprint": source_eval_fingerprint,
        "model_spec_id": spec.spec_id,
        "backend_kind": spec.backend_kind,
        "model_id": spec.model_id,
        "model_revision": spec.revision,
        "chat_template_sha256": spec.chat_template_sha256,
        "generation_fingerprint": spec.generation_fingerprint,
        "seed": spec.generation.seed,
        "selected_task_ids": selected_task_ids,
        "prefix_total": prefix_total,
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "capacity_evidence": (
            capacity_evidence.model_dump(mode="json")
            if capacity_evidence is not None
            else None
        ),
    }
    draft = ProbeBundleManifest.model_validate(
        {**payload, "bundle_fingerprint": "0" * 64}
    )
    return draft.model_copy(
        update={
            "bundle_fingerprint": canonical_sha256(
                draft.model_dump(mode="json", exclude={"bundle_fingerprint"})
            )
        }
    )


def _write_bundle(
    output_dir: Path,
    *,
    prefixes: tuple[ValidationPrefix, ...],
    raw_records: list[RawProbeRecord],
    records: list[StrictProbeRecord],
    summary: ProbeSummary,
    source_commit: str,
    source_eval_fingerprint: str,
    spec: LocalModelSpec,
    selected_task_ids: list[str],
    capacity_evidence: CapacityEvidence | None = None,
) -> ProbeBundleManifest:
    (output_dir / "raw-generations.jsonl").write_text(
        _jsonl(raw_records), encoding="utf-8", newline="\n"
    )
    (output_dir / "records.jsonl").write_text(
        _jsonl(records), encoding="utf-8", newline="\n"
    )
    (output_dir / "summary.json").write_text(
        canonical_json_text(summary), encoding="utf-8", newline="\n"
    )
    placeholder = _manifest(
        status=summary.status,
        source_commit=source_commit,
        source_eval_fingerprint=source_eval_fingerprint,
        spec=spec,
        selected_task_ids=selected_task_ids,
        prefix_total=len(prefixes),
        artifacts=[],
        capacity_evidence=capacity_evidence,
    )
    (output_dir / "report.md").write_text(
        render_probe_report(placeholder, summary), encoding="utf-8", newline="\n"
    )
    artifacts = [
        ProbeArtifact(path=path, sha256=_sha256(output_dir / path))
        for path in PROBE_ARTIFACT_PATHS
    ]
    manifest = _manifest(
        status=summary.status,
        source_commit=source_commit,
        source_eval_fingerprint=source_eval_fingerprint,
        spec=spec,
        selected_task_ids=selected_task_ids,
        prefix_total=len(prefixes),
        artifacts=artifacts,
        capacity_evidence=capacity_evidence,
    )
    # Report rendering does not depend on the artifact list or bundle fingerprint.
    (output_dir / "probe-manifest.json").write_text(
        canonical_json_text(manifest), encoding="utf-8", newline="\n"
    )
    return manifest


def _raw_record(
    prefix: ValidationPrefix,
    spec: LocalModelSpec,
    generation: ModelGeneration,
) -> RawProbeRecord:
    return RawProbeRecord(
        prefix_id=prefix.prefix_id,
        task_id=prefix.task_id,
        environment=prefix.environment,
        step_index=prefix.step_index,
        prompt_fingerprint=prefix.prompt_fingerprint,
        reference_fingerprint=prefix.reference_fingerprint,
        model_spec_id=spec.spec_id,
        model_id=spec.model_id,
        model_revision=spec.revision,
        generation_fingerprint=spec.generation_fingerprint,
        seed=spec.generation.seed,
        generation=generation,
    )


def run_model_probe(
    source_eval: Path,
    output_dir: Path,
    *,
    backend_kind: str,
    seed: int,
    model_id: str | None = None,
    snapshot_dir: Path | None = None,
    selected_task_ids: list[str] | None = None,
    backend: ModelBackendProtocol | None = None,
) -> ModelProbeRunOutcome:
    """Generate independent prefixes; never pass a predicted action to a tool."""
    if output_dir.exists():
        raise ModelProbeRunError("output_directory_must_not_exist")
    contract = load_probe_contract()
    if seed != 17:
        raise ModelProbeRunError("probe_seed_must_be_17")
    if backend_kind == "fake":
        spec = select_model_spec("agent-learning-loop/fake-tool-model")
        default_tasks = [item.task_id for item in contract.validation_tasks]
    elif backend_kind == "qwen3":
        if model_id is None:
            raise ModelProbeRunError("qwen_model_id_required")
        spec = select_model_spec(model_id)
        default_tasks = list(contract.smoke_task_ids)
    else:
        raise ModelProbeRunError("unknown_probe_backend")
    if spec.generation.seed != seed:
        raise ModelProbeRunError("model_generation_seed_mismatch")
    tasks = selected_task_ids or default_tasks
    source_manifest = source_eval_manifest(source_eval)
    if source_manifest.source_commit != contract.public_source_commit:
        raise ModelProbeRunError("source_eval_public_commit")
    prefixes = build_validation_prefixes(source_eval, selected_task_ids=tasks)
    output_dir.mkdir(parents=True)
    owned_backend = backend is None
    try:
        if backend is None and backend_kind == "fake":
            responses = {
                prefix.prompt_fingerprint: exact_fake_generation(
                    prefix.reference_action.tool_name,
                    prefix.reference_action.arguments,
                )
                for prefix in prefixes
            }
            backend = FakeModelBackend(spec, responses)
        elif backend is None:
            if snapshot_dir is None:
                raise ModelProbeRunError("qwen_snapshot_dir_required")
            try:
                backend = Qwen3LocalBackend(spec, snapshot_dir)
            except ModelProbeCapacityError as exc:
                if model_id != "Qwen/Qwen3-1.7B":
                    raise ModelProbeRunError("required_0_6b_capacity_failure") from exc
                summary = summarize_probe_records(
                    prefixes, [], status="capacity_blocked"
                )
                manifest = _write_bundle(
                    output_dir,
                    prefixes=prefixes,
                    raw_records=[],
                    records=[],
                    summary=summary,
                    source_commit=source_manifest.source_commit,
                    source_eval_fingerprint=source_manifest.bundle_fingerprint,
                    spec=spec,
                    selected_task_ids=tasks,
                    capacity_evidence=exc.evidence,
                )
                return ModelProbeRunOutcome("capacity_blocked", manifest, summary)
        if backend.spec != spec:
            raise ModelProbeRunError("backend_model_spec_identity")
        raw_records: list[RawProbeRecord] = []
        records: list[StrictProbeRecord] = []
        try:
            for prefix in prefixes:
                generation = backend.generate(_backend_input(prefix))
                try:
                    require_completed_qwen_runtime(spec, generation)
                except ValueError as exc:
                    raise ModelProbeRunError("qwen_generation_runtime") from exc
                raw = _raw_record(prefix, spec, generation)
                raw_records.append(raw)
                records.append(derive_probe_record(raw, prefix))
        except ModelProbeCapacityError as exc:
            if model_id != "Qwen/Qwen3-1.7B":
                raise ModelProbeRunError("required_0_6b_capacity_failure") from exc
            if raw_records:
                raise ModelProbeRunError(
                    "partial_generation_capacity_failure"
                ) from exc
            raw_records = []
            records = []
            summary = summarize_probe_records(prefixes, [], status="capacity_blocked")
            manifest = _write_bundle(
                output_dir,
                prefixes=prefixes,
                raw_records=[],
                records=[],
                summary=summary,
                source_commit=source_manifest.source_commit,
                source_eval_fingerprint=source_manifest.bundle_fingerprint,
                spec=spec,
                selected_task_ids=tasks,
                capacity_evidence=exc.evidence,
            )
            return ModelProbeRunOutcome("capacity_blocked", manifest, summary)
        summary = summarize_probe_records(prefixes, records)
        manifest = _write_bundle(
            output_dir,
            prefixes=prefixes,
            raw_records=raw_records,
            records=records,
            summary=summary,
            source_commit=source_manifest.source_commit,
            source_eval_fingerprint=source_manifest.bundle_fingerprint,
            spec=spec,
            selected_task_ids=tasks,
        )
        return ModelProbeRunOutcome("completed", manifest, summary)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    finally:
        if backend is not None and owned_backend:
            backend.close()
