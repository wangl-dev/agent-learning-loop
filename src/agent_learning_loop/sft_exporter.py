"""Read-only M7A exporter for deterministic scripted-oracle SFT candidates."""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, JsonValue, ValidationError

from agent_learning_loop.canonical import canonical_json_bytes, canonical_sha256
from agent_learning_loop.corpus import WorkspaceCorpusManifest, validate_workspace_corpus
from agent_learning_loop.dataops_corpus import (
    DataOpsCorpusManifest,
    validate_dataops_corpus,
)
from agent_learning_loop.dataops_schemas import DataOpsTask
from agent_learning_loop.eval_schemas import EvalBundleManifest, SystemEvalCell
from agent_learning_loop.eval_suites import load_eval_suites
from agent_learning_loop.eval_validator import validate_eval_bundle
from agent_learning_loop.incident_corpus import (
    IncidentCorpusManifest,
    validate_incident_corpus,
)
from agent_learning_loop.incident_schemas import IncidentTask
from agent_learning_loop.schemas import Task
from agent_learning_loop.sft_normalizers import (
    normalize_dataops_trajectory,
    normalize_incident_trajectory,
    normalize_workspace_trajectory,
)
from agent_learning_loop.sft_schemas import (
    SFT_ARTIFACT_PATHS,
    SFT_CONTRACT_VERSION,
    SFT_DATASET_ID,
    SftArtifact,
    SftCandidateManifest,
    SftProvenance,
    SftQualityReport,
    SftResourceIdentity,
    SftSample,
    SftSampleQuality,
    SftTableScope,
    SftTaskContext,
    SftTurn,
    scan_sft_sensitive_text,
)

CorpusManifest = WorkspaceCorpusManifest | IncidentCorpusManifest | DataOpsCorpusManifest

_PROHIBITED_SAMPLE_KEYS = {
    "private",
    "setup",
    "expected",
    "verifier",
    "checks",
    "detail",
    "score",
    "audit",
    "run_id",
    "action_ref",
    "before_digest",
    "after_digest",
    "primary_key_digest",
    "database_digest",
}
class SftExportError(ValueError):
    """The source, output boundary, or fixed M7A contract is invalid."""


class SftExportInfrastructureError(RuntimeError):
    """Filesystem or caller setup prevented an export attempt."""


@dataclass(frozen=True)
class SftCandidateArtifacts:
    files: dict[str, bytes]
    manifest: SftCandidateManifest
    quality: SftQualityReport
    samples: tuple[SftSample, ...]


@dataclass(frozen=True)
class SftExportOutcome:
    manifest: SftCandidateManifest
    quality: SftQualityReport


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _directory_bytes(root: Path) -> dict[str, bytes]:
    try:
        resolved = root.resolve(strict=True)
        result: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise SftExportError("sft_source_contains_symlink")
            path.resolve(strict=True).relative_to(resolved)
            if path.is_file():
                result[path.relative_to(root).as_posix()] = path.read_bytes()
        return result
    except SftExportError:
        raise
    except OSError as exc:
        raise SftExportInfrastructureError("unreadable_sft_source_directory") from exc
    except ValueError as exc:
        raise SftExportError("invalid_sft_source_directory") from exc


def _manifest_by_task(
    manifests: tuple[CorpusManifest, ...],
) -> dict[str, CorpusManifest]:
    return {manifest.task_id: manifest for manifest in manifests}


def _assert_cell_resource(
    cell: SystemEvalCell,
    manifest: CorpusManifest,
) -> None:
    if (
        cell.task_id != manifest.task_id
        or cell.environment != manifest.environment_kind
        or cell.split != manifest.split
        or cell.seed != manifest.seed
        or cell.resource_id != manifest.manifest_id
        or cell.resource_fingerprint
        != canonical_sha256(manifest.model_dump(mode="json"))
        or cell.fixture_id != manifest.fixture_id
        or cell.fixture_fingerprint != manifest.fixture_fingerprint
        or cell.catalog_id != manifest.catalog_id
        or cell.catalog_fingerprint != manifest.catalog_fingerprint
        or cell.tags != manifest.tags
        or cell.provenance.source != manifest.provenance.source
        or cell.provenance.license != manifest.provenance.license
    ):
        raise SftExportError("sft_cell_resource_identity")


def _scan_value(value: JsonValue, findings: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _PROHIBITED_SAMPLE_KEYS:
                findings.append(f"prohibited_key:{key}")
            _scan_value(item, findings)
    elif isinstance(value, list):
        for item in value:
            _scan_value(item, findings)
    elif isinstance(value, str):
        findings.extend(scan_sft_sensitive_text(value))


def _field_minimization_findings(sample: SftSample) -> Counter[str]:
    findings: list[str] = []
    payload = sample.model_dump(mode="json")
    _scan_value(payload, findings)
    serialized = canonical_json_bytes(payload)
    if b"\r" in serialized:
        findings.append("carriage_return")
    return Counter(findings)


def _sample_payload(
    *,
    cell: SystemEvalCell,
    manifest: CorpusManifest,
    source_manifest: EvalBundleManifest,
    source_artifacts: dict[str, str],
    task_context: SftTaskContext,
    turns: list[SftTurn],
) -> dict[str, JsonValue]:
    run_root = f"runs/system-correctness-v1/{cell.cell_id}"
    events_path = f"{run_root}/events.jsonl"
    result_path = f"{run_root}/result.json"
    for path in (events_path, result_path):
        if path not in source_artifacts:
            raise SftExportError("sft_source_artifact_missing")
    quality = SftSampleQuality()
    provenance = SftProvenance()
    resource = SftResourceIdentity(
        manifest_id=manifest.manifest_id,
        manifest_fingerprint=cell.resource_fingerprint,
        fixture_id=manifest.fixture_id,
        fixture_fingerprint=manifest.fixture_fingerprint,
        catalog_id=manifest.catalog_id,
        catalog_fingerprint=manifest.catalog_fingerprint,
    )
    return {
        "schema_version": "1",
        "contract_version": SFT_CONTRACT_VERSION,
        "sample_id": f"sft.{cell.task_id}.v1",
        "task_id": cell.task_id,
        "environment": cell.environment,
        "split": "train",
        "scenario_family": manifest.scenario_family,
        "seed": cell.seed,
        "tags": list(cell.tags),
        "generation_mode": "scripted_oracle",
        "source_commit": source_manifest.source_commit,
        "source_suite_id": "system-correctness-v1",
        "source_cell_id": cell.cell_id,
        "source_artifacts": [
            {"role": "events", "path": events_path, "sha256": source_artifacts[events_path]},
            {"role": "result", "path": result_path, "sha256": source_artifacts[result_path]},
        ],
        "resource": resource.model_dump(mode="json"),
        "provenance": provenance.model_dump(mode="json"),
        "task": task_context.model_dump(mode="json"),
        "turns": [turn.model_dump(mode="json") for turn in turns],
        "quality": quality.model_dump(mode="json"),
    }


def _render_report(
    source_manifest: EvalBundleManifest,
    quality: SftQualityReport,
) -> str:
    report = "\n".join(
        [
            "# M7A scripted-oracle SFT development candidate",
            "",
            f"- Stage: `{quality.stage}`",
            f"- Source trajectory commit: `{source_manifest.source_commit}`",
            f"- Source Eval bundle fingerprint: `{source_manifest.bundle_fingerprint}`",
            "- Eligible samples: `18` (`workspace=6`, `incident=6`, `dataops=6`)",
            "- Held-out excluded: `12` (`validation=6`, `test=6`)",
            "- Generation mode: `scripted_oracle`; model-generated samples: `0`",
            "- Preference or DPO pairs: `0`",
            "- Provenance/license: `project-authored-synthetic` / `Apache-2.0`",
            "",
            "## Quality and leakage gates",
            "",
            "- Duplicate sample/task/family/fingerprint findings: `0/0/0/0`",
            "- Train-to-held-out task/family overlap: `0/0`",
            "- Leakage, machine-path, secret-like, CR, non-UTF-8, symlink findings: `0`",
            "- Environment, Policy, tool, runner, subprocess, socket, and network calls: `0`",
            "- Source Eval bytes changed by export: `false`",
            "",
            "## Boundary",
            "",
            "This bundle is a deterministic development candidate, not a tracked dataset or a "
            "training result. It contains public task context plus raw action/tool observations. "
            "Fixture-only setup and answer state, verifier output, audit records, run identifiers, "
            "held-out content, reliability/recovery cells, and preference labels are excluded.",
            "SHA-256 binds reviewed bytes and identities; it is not a signature against an actor "
            "who can rewrite every related artifact.",
            "",
        ]
    )
    if "\r" in report or scan_sft_sensitive_text(report):
        raise SftExportError("sft_report_leakage_or_path")
    return report


def _canonical_json_file(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json")
    return canonical_json_bytes(payload) + b"\n"


def build_sft_candidate_artifacts(eval_bundle: Path) -> SftCandidateArtifacts:
    """Regenerate all four expected bytes from a normal, complete source Eval."""
    try:
        source_before = _directory_bytes(eval_bundle)
        validation = validate_eval_bundle(eval_bundle)
        source_manifest_bytes = source_before.get("eval-manifest.json")
        if source_manifest_bytes is None:
            raise SftExportError("sft_source_manifest_missing")
        source_manifest = EvalBundleManifest.model_validate_json(source_manifest_bytes)

        suites = load_eval_suites()
        suite = suites["system-correctness-v1"]
        system_cells = tuple(
            cell for cell in suite.cells if isinstance(cell, SystemEvalCell)
        )
        if len(system_cells) != 30:
            raise SftExportError("sft_system_cell_contract")
        system_ids = {cell.cell_id for cell in system_cells}
        if (
            validation.selected_cells != source_manifest.selection.selected_total
            or not system_ids <= set(source_manifest.selection.cell_ids)
            or source_manifest.selection.suite not in {"system-correctness", "all"}
        ):
            raise SftExportError("sft_source_missing_complete_system_suite")

        workspace = validate_workspace_corpus()
        incident = validate_incident_corpus()
        dataops = validate_dataops_corpus()
        all_manifests: tuple[CorpusManifest, ...] = (
            *workspace.manifests,
            *incident.manifests,
            *dataops.manifests,
        )
        by_task = _manifest_by_task(all_manifests)
        task_ids = [manifest.task_id for manifest in all_manifests]
        families = [manifest.scenario_family for manifest in all_manifests]
        if len(task_ids) != 30 or len(task_ids) != len(set(task_ids)):
            raise SftExportError("sft_global_task_identity")
        if len(families) != 30 or len(families) != len(set(families)):
            raise SftExportError("sft_global_family_identity")
        split_counts = Counter(manifest.split for manifest in all_manifests)
        if split_counts != {"train": 18, "validation": 6, "test": 6}:
            raise SftExportError("sft_global_split_contract")
        train_tasks = {
            manifest.task_id for manifest in all_manifests if manifest.split == "train"
        }
        held_out_tasks = {
            manifest.task_id for manifest in all_manifests if manifest.split != "train"
        }
        train_families = {
            manifest.scenario_family
            for manifest in all_manifests
            if manifest.split == "train"
        }
        held_out_families = {
            manifest.scenario_family
            for manifest in all_manifests
            if manifest.split != "train"
        }
        if train_tasks & held_out_tasks or train_families & held_out_families:
            raise SftExportError("sft_train_held_out_overlap")

        workspace_fixtures = {item.task.task_id: item for item in workspace.fixtures}
        workspace_catalogs = {item.task_id: item for item in workspace.catalogs}
        incident_fixtures = {item.task.task_id: item for item in incident.fixtures}
        incident_catalogs = {item.task_id: item for item in incident.catalogs}
        dataops_fixtures = {item.task.task_id: item for item in dataops.fixtures}
        dataops_catalogs = {item.task_id: item for item in dataops.catalogs}
        source_artifacts = {
            artifact.path: artifact.sha256 for artifact in source_manifest.artifacts
        }

        samples: list[SftSample] = []
        leakage_findings: Counter[str] = Counter()
        for cell in system_cells:
            if cell.split != "train":
                continue
            manifest = by_task[cell.task_id]
            _assert_cell_resource(cell, manifest)
            events_path = (
                eval_bundle
                / "runs"
                / "system-correctness-v1"
                / cell.cell_id
                / "events.jsonl"
            )
            if cell.environment == "workspace":
                fixture = workspace_fixtures[cell.task_id]
                catalog = workspace_catalogs[cell.task_id]
                task: Task = fixture.task
                context = SftTaskContext(
                    instruction=task.instruction,
                    allowed_tools=list(task.allowed_tools),
                    public_scope=[],
                    constraints=list(manifest.safety_constraints),
                )
                turns = normalize_workspace_trajectory(events_path, task, catalog)
            elif cell.environment == "incident":
                incident_fixture = incident_fixtures[cell.task_id]
                incident_catalog = incident_catalogs[cell.task_id]
                incident_task: IncidentTask = incident_fixture.task
                context = SftTaskContext(
                    instruction=incident_task.instruction,
                    allowed_tools=list(incident_task.allowed_tools),
                    public_scope=[],
                    constraints=list(manifest.safety_constraints),
                )
                turns = normalize_incident_trajectory(
                    events_path, incident_task, incident_catalog
                )
            else:
                dataops_fixture = dataops_fixtures[cell.task_id]
                dataops_catalog = dataops_catalogs[cell.task_id]
                dataops_task: DataOpsTask = dataops_fixture.task
                scope = [
                    SftTableScope.model_validate(item.model_dump(mode="json"))
                    for item in dataops_task.scope
                ]
                constraints = list(
                    dict.fromkeys(
                        [*manifest.safety_constraints, *dataops_task.public_constraints]
                    )
                )
                context = SftTaskContext(
                    instruction=dataops_task.instruction,
                    allowed_tools=list(dataops_task.allowed_tools),
                    public_scope=scope,
                    constraints=constraints,
                )
                turns = normalize_dataops_trajectory(
                    events_path, dataops_task, dataops_catalog
                )
            base_payload = _sample_payload(
                cell=cell,
                manifest=manifest,
                source_manifest=source_manifest,
                source_artifacts=source_artifacts,
                task_context=context,
                turns=list(turns),
            )
            sample = SftSample.model_validate(
                {
                    **base_payload,
                    "sample_fingerprint": canonical_sha256(base_payload),
                }
            )
            sample_findings = _field_minimization_findings(sample)
            leakage_findings.update(sample_findings)
            if sample_findings:
                raise SftExportError("sft_sample_leakage_or_path")
            samples.append(sample)

        if len(samples) != 18 or {sample.task_id for sample in samples} != train_tasks:
            raise SftExportError("sft_eligible_identity_contract")
        environment_counts = Counter(sample.environment for sample in samples)
        if environment_counts != {"workspace": 6, "incident": 6, "dataops": 6}:
            raise SftExportError("sft_eligible_environment_contract")
        if len({sample.sample_id for sample in samples}) != 18:
            raise SftExportError("sft_duplicate_sample_id")
        if len({sample.scenario_family for sample in samples}) != 18:
            raise SftExportError("sft_duplicate_sample_family")
        if len({sample.sample_fingerprint for sample in samples}) != 18:
            raise SftExportError("sft_duplicate_sample_fingerprint")
        if leakage_findings:
            raise SftExportError("sft_sample_leakage_or_path")

        quality = SftQualityReport.model_validate(
            {
                "leakage_findings": sum(leakage_findings.values()),
                "machine_path_findings": leakage_findings["machine_path"],
                "secret_like_findings": leakage_findings["secret_like"],
                "environment_counts": dict(environment_counts),
                "held_out_excluded": {"validation": 6, "test": 6},
                "quality_gates": {
                    "complete_train_identity": True,
                    "environment_balance": True,
                    "held_out_exclusion": True,
                    "raw_action_result_binding": True,
                    "public_field_minimization": True,
                    "provenance_and_license": True,
                    "unique_identity": True,
                    "path_and_encoding_safety": not leakage_findings,
                    "zero_execution": True,
                },
                "provenance": SftProvenance().model_dump(mode="json"),
                "limitation": (
                    "Eighteen synthetic scripted demonstrations define a development data "
                    "contract; they are not model outputs, training results, or a population "
                    "benchmark."
                ),
            },
        )
        samples_bytes = b"".join(
            canonical_json_bytes(sample.model_dump(mode="json")) + b"\n"
            for sample in samples
        )
        quality_bytes = _canonical_json_file(quality)
        report_bytes = _render_report(source_manifest, quality).encode("utf-8")
        artifact_bytes = {
            "samples.jsonl": samples_bytes,
            "quality-report.json": quality_bytes,
            "report.md": report_bytes,
        }
        environment_count_payload: dict[str, JsonValue] = {
            "workspace": 6,
            "incident": 6,
            "dataops": 6,
        }
        manifest_payload: dict[str, JsonValue] = {
            "schema_version": "1",
            "dataset_id": SFT_DATASET_ID,
            "contract_version": SFT_CONTRACT_VERSION,
            "stage": "development_candidate",
            "exporter_commit": None,
            "source_eval_manifest_sha256": _sha256(source_manifest_bytes),
            "source_eval_bundle_fingerprint": source_manifest.bundle_fingerprint,
            "source_eval_selected_cells": source_manifest.selection.selected_total,
            "system_suite_fingerprint": suite.manifest_fingerprint,
            "source_commit": source_manifest.source_commit,
            "sample_count": 18,
            "environment_counts": environment_count_payload,
            "held_out_excluded": {"validation": 6, "test": 6},
            "generation_mode": "scripted_oracle",
            "model_generated_samples": 0,
            "preference_pairs": 0,
            "provenance": SftProvenance().model_dump(mode="json"),
            "sample_ids": [sample.sample_id for sample in samples],
            "sample_fingerprints": [sample.sample_fingerprint for sample in samples],
            "artifacts": [
                SftArtifact(path=path, sha256=_sha256(artifact_bytes[path])).model_dump(
                    mode="json"
                )
                for path in ("samples.jsonl", "quality-report.json", "report.md")
            ],
            "limitation": (
                "Development candidate only; no tracked dataset, model training, DPO pair, or "
                "model-quality claim is produced by M7A."
            ),
        }
        candidate_manifest = SftCandidateManifest.model_validate(
            {
                **manifest_payload,
                "bundle_fingerprint": canonical_sha256(manifest_payload),
            }
        )
        files = {
            "dataset-manifest.json": _canonical_json_file(candidate_manifest),
            **artifact_bytes,
        }
        if tuple(files) != SFT_ARTIFACT_PATHS or any(b"\r" in data for data in files.values()):
            raise SftExportError("sft_output_inventory_or_line_ending")
        if source_before != _directory_bytes(eval_bundle):
            raise SftExportError("sft_export_changed_source_bytes")
        return SftCandidateArtifacts(
            files=files,
            manifest=candidate_manifest,
            quality=quality,
            samples=tuple(samples),
        )
    except SftExportError:
        raise
    except SftExportInfrastructureError:
        raise
    except (KeyError, UnicodeError, ValidationError, ValueError, TypeError) as exc:
        raise SftExportError("invalid_sft_export_source") from exc


def export_sft_candidates(eval_bundle: Path, output_dir: Path) -> SftExportOutcome:
    """Write a new four-file development bundle without executing source tasks."""
    if output_dir.exists():
        raise SftExportInfrastructureError("sft_output_directory_must_not_exist")
    created_output = False
    try:
        source_root = eval_bundle.resolve(strict=True)
        output_root = output_dir.resolve(strict=False)
        if output_root == source_root or output_root.is_relative_to(source_root):
            raise SftExportInfrastructureError("sft_output_overlaps_source")
        artifacts = build_sft_candidate_artifacts(eval_bundle)
        output_dir.mkdir(parents=True)
        created_output = True
        for relative_path in SFT_ARTIFACT_PATHS:
            (output_dir / relative_path).write_bytes(artifacts.files[relative_path])
        from agent_learning_loop.sft_validator import validate_sft_candidates

        validate_sft_candidates(output_dir, eval_bundle)
        return SftExportOutcome(manifest=artifacts.manifest, quality=artifacts.quality)
    except Exception:
        if created_output and output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise
