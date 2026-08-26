"""Execute the registered M6A pilot as one filtered Incident Eval run."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from agent_learning_loop.eval_bundle import canonical_json_text, sha256_file
from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.fde_case_manifest import (
    FdeCaseManifestError,
    build_case_acceptance,
    load_fde_case,
    render_case_report,
)
from agent_learning_loop.fde_case_schemas import FdeCaseAcceptance, FdeCaseRunManifest
from agent_learning_loop.fde_case_validator import (
    FdeCaseValidationError,
    compute_pilot_fingerprint,
    derive_case_evidence,
    evidence_artifacts,
    evidence_inventory_digest,
    validate_fde_case,
)


class FdeCaseRunError(ValueError):
    """Arguments, execution, or validation prevented a trustworthy pilot bundle."""


@dataclass(frozen=True)
class FdeCaseRunOutcome:
    exit_code: int
    manifest: FdeCaseRunManifest
    acceptance: FdeCaseAcceptance


def run_fde_case(case_id: str, source_commit: str, output_dir: Path) -> FdeCaseRunOutcome:
    """Run exactly one registered case; exit 1 is valid evidence with acceptance drift."""
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise FdeCaseRunError("source_commit_must_be_lowercase_40_hex")
    try:
        definition = load_fde_case(case_id)
    except FdeCaseManifestError as exc:
        raise FdeCaseRunError(str(exc)) from exc
    if output_dir.exists():
        raise FdeCaseRunError("output_directory_must_not_exist")

    evidence_dir = output_dir / "evidence"
    try:
        output_dir.mkdir(parents=True)
        eval_outcome = run_eval(
            "system-correctness",
            source_commit,
            evidence_dir,
            environment="incident",
        )
        evidence = derive_case_evidence(
            definition,
            evidence_dir,
            source_commit=source_commit,
        )
        acceptance = build_case_acceptance(definition, evidence)
        acceptance_path = output_dir / "acceptance.json"
        acceptance_path.write_text(
            canonical_json_text(acceptance), encoding="utf-8", newline="\n"
        )
        report_path = output_dir / "report.md"
        report_path.write_text(
            render_case_report(
                definition,
                source_commit,
                eval_outcome.manifest.bundle_fingerprint,
                acceptance,
            ),
            encoding="utf-8",
            newline="\n",
        )
        artifacts = evidence_artifacts(evidence_dir)
        manifest = FdeCaseRunManifest(
            source_commit=source_commit,
            definition_fingerprint=definition.definition_fingerprint,
            selection=definition.selection,
            cell_ids=[cell.cell_id for cell in definition.cells],
            nested_eval_fingerprint=eval_outcome.manifest.bundle_fingerprint,
            evidence_artifacts=artifacts,
            evidence_inventory_digest=evidence_inventory_digest(artifacts),
            acceptance_sha256=sha256_file(acceptance_path),
            report_sha256=sha256_file(report_path),
            pilot_fingerprint="0" * 64,
        )
        manifest = manifest.model_copy(
            update={"pilot_fingerprint": compute_pilot_fingerprint(manifest)}
        )
        (output_dir / "case-manifest.json").write_text(
            canonical_json_text(manifest), encoding="utf-8", newline="\n"
        )
        validation = validate_fde_case(output_dir)
        if validation.overall != acceptance.overall:
            raise FdeCaseValidationError("runner_validator_acceptance_mismatch")
        return FdeCaseRunOutcome(
            exit_code=0 if acceptance.overall == "accepted" else 1,
            manifest=manifest,
            acceptance=acceptance,
        )
    except Exception as exc:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if isinstance(exc, FdeCaseRunError):
            raise
        raise FdeCaseRunError("fde_case_execution_failed") from exc
