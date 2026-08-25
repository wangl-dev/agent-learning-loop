"""Zero-execution, read-only validator for deterministic M5A Eval bundles."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from agent_learning_loop.eval_aggregation import aggregate_eval_records
from agent_learning_loop.eval_bundle import (
    canonical_json_text,
    compute_bundle_fingerprint,
    records_jsonl_text,
    render_eval_report,
    sha256_file,
)
from agent_learning_loop.eval_records import normalize_result
from agent_learning_loop.eval_recovery import validate_recovery_evidence
from agent_learning_loop.eval_schemas import (
    EvalBundleManifest,
    EvalSummary,
    EvalValidationResult,
    NormalizedEvalRecord,
    RecoveryEvalCell,
)
from agent_learning_loop.eval_suites import load_eval_suites, select_eval_cells


class EvalBundleValidationError(ValueError):
    """The bundle is malformed, incomplete, inconsistent, or outside M5A scope."""


def _directory_bytes(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    resolved = root.resolve(strict=True)
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise EvalBundleValidationError("bundle_contains_symlink")
        path.resolve(strict=True).relative_to(resolved)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _raw_path(cell: object) -> str:
    suite_id = cell.suite_id  # type: ignore[attr-defined]
    cell_id = cell.cell_id  # type: ignore[attr-defined]
    name = "diagnostic.json" if isinstance(cell, RecoveryEvalCell) else "result.json"
    return f"runs/{suite_id}/{cell_id}/{name}"


def validate_eval_bundle(run_dir: Path) -> EvalValidationResult:
    """Validate from packaged identities and persisted bytes without executing a cell."""
    try:
        if run_dir.is_symlink():
            raise EvalBundleValidationError("bundle_root_is_symlink")
        if not run_dir.is_dir():
            raise EvalBundleValidationError("bundle_root_missing")
        before = _directory_bytes(run_dir)
        required_top = {
            "eval-manifest.json",
            "records.jsonl",
            "summary.json",
            "report.md",
        }
        actual_top = {path.name for path in run_dir.iterdir() if path.is_file()}
        actual_dirs = {path.name for path in run_dir.iterdir() if path.is_dir()}
        if actual_top != required_top or actual_dirs != {"runs"}:
            raise EvalBundleValidationError("bundle_top_level_shape")

        manifest = EvalBundleManifest.model_validate_json(
            (run_dir / "eval-manifest.json").read_text(encoding="utf-8")
        )
        if (
            (run_dir / "eval-manifest.json").read_text(encoding="utf-8")
            != canonical_json_text(manifest)
            or manifest.bundle_fingerprint != compute_bundle_fingerprint(manifest)
        ):
            raise EvalBundleValidationError("manifest_canonical_or_fingerprint")

        suites = load_eval_suites()
        selected = select_eval_cells(
            suites,
            manifest.selection.suite,
            environment=manifest.selection.environment,
            split=manifest.selection.split,
            tag=manifest.selection.tag,
            pair=manifest.selection.pair,
        )
        if selected.spec != manifest.selection:
            raise EvalBundleValidationError("selection_identity")
        selected_suite_ids = {cell.suite_id for cell in selected.cells}
        expected_fingerprints = {
            suite_id: suites[suite_id].manifest_fingerprint
            for suite_id in sorted(selected_suite_ids)
        }
        if manifest.suite_fingerprints != expected_fingerprints:
            raise EvalBundleValidationError("suite_fingerprint_identity")

        actual_artifact_paths = [
            path
            for path in sorted((run_dir / "runs").rglob("*"))
            if path.is_file()
        ]
        actual_artifacts = {
            path.relative_to(run_dir).as_posix(): sha256_file(path)
            for path in actual_artifact_paths
        }
        declared_artifacts = {item.path: item.sha256 for item in manifest.artifacts}
        if actual_artifacts != declared_artifacts:
            raise EvalBundleValidationError("raw_artifact_inventory_or_hash")

        recovery_cells = tuple(
            cell for cell in selected.cells if isinstance(cell, RecoveryEvalCell)
        )
        if recovery_cells:
            validate_recovery_evidence(run_dir, recovery_cells)

        record_lines = (run_dir / "records.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if not record_lines:
            raise EvalBundleValidationError("records_empty")
        saved_records = [
            NormalizedEvalRecord.model_validate_json(line) for line in record_lines
        ]
        if [record.cell_id for record in saved_records] != selected.spec.cell_ids:
            raise EvalBundleValidationError("record_cell_identity_or_order")
        regenerated = [
            normalize_result(
                run_dir,
                cell,
                manifest.source_commit,
                _raw_path(cell),
            )
            for cell in selected.cells
        ]
        if (
            saved_records != regenerated
            or (run_dir / "records.jsonl").read_text(encoding="utf-8")
            != records_jsonl_text(regenerated)
        ):
            raise EvalBundleValidationError("records_not_raw_derived")

        comparisons = [
            comparison
            for suite_id in selected_suite_ids
            for comparison in suites[suite_id].comparisons
        ]
        regenerated_summary = aggregate_eval_records(regenerated, comparisons)
        saved_summary = EvalSummary.model_validate_json(
            (run_dir / "summary.json").read_text(encoding="utf-8")
        )
        if (
            saved_summary != regenerated_summary
            or (run_dir / "summary.json").read_text(encoding="utf-8")
            != canonical_json_text(regenerated_summary)
        ):
            raise EvalBundleValidationError("summary_not_record_derived")
        if (run_dir / "report.md").read_text(encoding="utf-8") != render_eval_report(
            manifest, regenerated_summary
        ):
            raise EvalBundleValidationError("report_not_summary_derived")

        after = _directory_bytes(run_dir)
        if before != after:
            raise EvalBundleValidationError("validator_changed_source_bytes")
        return EvalValidationResult(
            source_commit=manifest.source_commit,
            selected_cells=manifest.selection.selected_total,
        )
    except EvalBundleValidationError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise EvalBundleValidationError("invalid_eval_bundle") from exc
