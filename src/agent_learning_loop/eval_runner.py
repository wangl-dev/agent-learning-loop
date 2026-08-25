"""Execute fixed M5A suites and emit one deterministic evidence bundle."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from agent_learning_loop.corpus import CorpusSplit
from agent_learning_loop.dataops_corpus import validate_dataops_corpus
from agent_learning_loop.dataops_runner import run_dataops_task
from agent_learning_loop.eval_aggregation import aggregate_eval_records
from agent_learning_loop.eval_bundle import (
    canonical_json_text,
    compute_bundle_fingerprint,
    records_jsonl_text,
    render_eval_report,
    sha256_file,
)
from agent_learning_loop.eval_clock import DeterministicEvalClock
from agent_learning_loop.eval_records import expected_eval_run_id, normalize_result
from agent_learning_loop.eval_recovery import execute_recovery_suite
from agent_learning_loop.eval_schemas import (
    EnvironmentName,
    EvalArtifact,
    EvalBundleManifest,
    EvalSummary,
    RecoveryEvalCell,
    ReliabilityEvalCell,
    SuiteSelector,
    SystemEvalCell,
)
from agent_learning_loop.eval_suites import load_eval_suites, select_eval_cells
from agent_learning_loop.failure_schedules import load_failure_schedule
from agent_learning_loop.incident_corpus import validate_incident_corpus
from agent_learning_loop.incident_runner import run_incident_task
from agent_learning_loop.runtime import execute_runtime_task
from agent_learning_loop.tasks import load_task
from agent_learning_loop.vertical_slice import execute_task


class EvalRunError(ValueError):
    """Arguments, identity, or execution prevented a trustworthy bundle."""


@dataclass(frozen=True)
class EvalRunOutcome:
    exit_code: int
    manifest: EvalBundleManifest
    summary: EvalSummary


def _run_id(cell_id: str) -> str:
    return expected_eval_run_id(cell_id)


def _primary_path(cell: SystemEvalCell | ReliabilityEvalCell) -> str:
    return f"runs/{cell.suite_id}/{cell.cell_id}/result.json"


def run_eval(
    suite: SuiteSelector,
    source_commit: str,
    output_dir: Path,
    *,
    environment: EnvironmentName | None = None,
    split: CorpusSplit | None = None,
    tag: str | None = None,
    pair: str | None = None,
) -> EvalRunOutcome:
    """Run a pre-registered selection; exit 1 means valid evidence with oracle drift."""
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise EvalRunError("source_commit_must_be_lowercase_40_hex")
    if output_dir.exists():
        raise EvalRunError("output_directory_must_not_exist")
    try:
        suites = load_eval_suites()
        selection = select_eval_cells(
            suites,
            suite,
            environment=environment,
            split=split,
            tag=tag,
            pair=pair,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvalRunError("invalid_eval_selection") from exc

    output_dir.mkdir(parents=True)
    raw_paths: dict[str, str] = {}
    try:
        workspace_cells = [
            cell
            for cell in selection.cells
            if isinstance(cell, SystemEvalCell) and cell.environment == "workspace"
        ]
        incident_cells = [
            cell
            for cell in selection.cells
            if isinstance(cell, SystemEvalCell) and cell.environment == "incident"
        ]
        dataops_cells = [
            cell
            for cell in selection.cells
            if isinstance(cell, SystemEvalCell) and cell.environment == "dataops"
        ]
        reliability_cells = [
            cell for cell in selection.cells if isinstance(cell, ReliabilityEvalCell)
        ]
        recovery_cells = tuple(
            cell for cell in selection.cells if isinstance(cell, RecoveryEvalCell)
        )

        for workspace_cell in workspace_cells:
            target = output_dir / "runs" / workspace_cell.suite_id / workspace_cell.cell_id
            execute_task(
                load_task(workspace_cell.task_id),
                target,
                run_id=_run_id(workspace_cell.cell_id),
            )
            raw_paths[workspace_cell.cell_id] = _primary_path(workspace_cell)
        if incident_cells:
            incident_corpus = validate_incident_corpus()
            for incident_cell in incident_cells:
                target = output_dir / "runs" / incident_cell.suite_id / incident_cell.cell_id
                run_incident_task(
                    incident_corpus,
                    incident_cell.task_id,
                    target,
                    run_id=_run_id(incident_cell.cell_id),
                )
                raw_paths[incident_cell.cell_id] = _primary_path(incident_cell)
        if dataops_cells:
            dataops_corpus = validate_dataops_corpus()
            for dataops_cell in dataops_cells:
                target = output_dir / "runs" / dataops_cell.suite_id / dataops_cell.cell_id
                run_dataops_task(
                    dataops_corpus,
                    dataops_cell.task_id,
                    target,
                    run_id=_run_id(dataops_cell.cell_id),
                )
                raw_paths[dataops_cell.cell_id] = _primary_path(dataops_cell)
        for reliability_cell in reliability_cells:
            target = (
                output_dir
                / "runs"
                / reliability_cell.suite_id
                / reliability_cell.cell_id
            )
            execute_runtime_task(
                load_task(reliability_cell.task_id),
                target,
                run_id=_run_id(reliability_cell.cell_id),
                config=reliability_cell.runtime_config,
                schedule=load_failure_schedule(reliability_cell.schedule_id),
                clock=DeterministicEvalClock(),
            )
            raw_paths[reliability_cell.cell_id] = _primary_path(reliability_cell)
        if recovery_cells:
            raw_paths.update(execute_recovery_suite(output_dir, recovery_cells))

        records = [
            normalize_result(
                output_dir,
                cell,
                source_commit,
                raw_paths[cell.cell_id],
            )
            for cell in selection.cells
        ]
        selected_suite_ids = {cell.suite_id for cell in selection.cells}
        comparisons = [
            comparison
            for suite_id in selected_suite_ids
            for comparison in suites[suite_id].comparisons
        ]
        summary = aggregate_eval_records(records, comparisons)
        artifacts = [
            EvalArtifact(
                path=path.relative_to(output_dir).as_posix(),
                sha256=sha256_file(path),
            )
            for path in sorted((output_dir / "runs").rglob("*"))
            if path.is_file()
        ]
        draft = EvalBundleManifest(
            source_commit=source_commit,
            selection=selection.spec,
            suite_fingerprints={
                suite_id: suites[suite_id].manifest_fingerprint
                for suite_id in sorted(selected_suite_ids)
            },
            artifacts=artifacts,
            bundle_fingerprint="0" * 64,
        )
        manifest = draft.model_copy(
            update={"bundle_fingerprint": compute_bundle_fingerprint(draft)}
        )
        (output_dir / "records.jsonl").write_text(
            records_jsonl_text(records), encoding="utf-8", newline="\n"
        )
        (output_dir / "summary.json").write_text(
            canonical_json_text(summary), encoding="utf-8", newline="\n"
        )
        (output_dir / "eval-manifest.json").write_text(
            canonical_json_text(manifest), encoding="utf-8", newline="\n"
        )
        (output_dir / "report.md").write_text(
            render_eval_report(manifest, summary), encoding="utf-8", newline="\n"
        )

        from agent_learning_loop.eval_validator import validate_eval_bundle

        validate_eval_bundle(output_dir)
        return EvalRunOutcome(
            exit_code=1 if summary.oracle_failure_cell_ids else 0,
            manifest=manifest,
            summary=summary,
        )
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
