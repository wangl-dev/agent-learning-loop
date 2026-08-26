from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from agent_learning_loop.eval_bundle import canonical_json_text, compute_bundle_fingerprint
from agent_learning_loop.eval_schemas import EvalArtifact, EvalBundleManifest
from agent_learning_loop.fde_case_manifest import load_fde_case, render_case_report
from agent_learning_loop.fde_case_runner import run_fde_case
from agent_learning_loop.fde_case_schemas import FdeCaseAcceptance, FdeCaseRunManifest
from agent_learning_loop.fde_case_validator import (
    FdeCaseValidationError,
    _unauthorized_execution_ids,
    compute_pilot_fingerprint,
    evidence_artifacts,
    evidence_inventory_digest,
    validate_fde_case,
)
from agent_learning_loop.incident_schemas import IncidentAuditRecord

SOURCE_COMMIT = "d030e3219991d3fa52a5a3eca86c31239659745a"


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def honest_pilot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("m6a-honest") / "pilot"
    outcome = run_fde_case("incident-copilot-v1", SOURCE_COMMIT, root)
    assert outcome.exit_code == 0
    return root


def _resign_nested(root: Path) -> None:
    manifest_path = root / "evidence" / "eval-manifest.json"
    manifest = EvalBundleManifest.model_validate_json(manifest_path.read_bytes())
    artifacts = [
        item.model_copy(
            update={
                "sha256": hashlib.sha256((root / "evidence" / item.path).read_bytes()).hexdigest()
            }
        )
        for item in manifest.artifacts
    ]
    draft = manifest.model_copy(update={"artifacts": artifacts, "bundle_fingerprint": "0" * 64})
    changed = draft.model_copy(update={"bundle_fingerprint": compute_bundle_fingerprint(draft)})
    manifest_path.write_text(canonical_json_text(changed), encoding="utf-8", newline="\n")


def _add_and_resign_nested_artifact(root: Path, relative_path: str) -> None:
    manifest_path = root / "evidence" / "eval-manifest.json"
    manifest = EvalBundleManifest.model_validate_json(manifest_path.read_bytes())
    artifacts = [
        *manifest.artifacts,
        EvalArtifact(
            path=relative_path,
            sha256=hashlib.sha256((root / "evidence" / relative_path).read_bytes()).hexdigest(),
        ),
    ]
    artifacts.sort(key=lambda item: (item.path.casefold(), item.path))
    draft = manifest.model_copy(
        update={"artifacts": artifacts, "bundle_fingerprint": "0" * 64}
    )
    changed = draft.model_copy(
        update={"bundle_fingerprint": compute_bundle_fingerprint(draft)}
    )
    manifest_path.write_text(canonical_json_text(changed), encoding="utf-8", newline="\n")


def _resign_outer(root: Path) -> None:
    manifest_path = root / "case-manifest.json"
    manifest = FdeCaseRunManifest.model_validate_json(manifest_path.read_bytes())
    nested = EvalBundleManifest.model_validate_json(
        (root / "evidence" / "eval-manifest.json").read_bytes()
    )
    artifacts = evidence_artifacts(root / "evidence")
    draft = manifest.model_copy(
        update={
            "nested_eval_fingerprint": nested.bundle_fingerprint,
            "evidence_artifacts": artifacts,
            "evidence_inventory_digest": evidence_inventory_digest(artifacts),
            "acceptance_sha256": hashlib.sha256(
                (root / "acceptance.json").read_bytes()
            ).hexdigest(),
            "report_sha256": hashlib.sha256((root / "report.md").read_bytes()).hexdigest(),
            "pilot_fingerprint": "0" * 64,
        }
    )
    changed = draft.model_copy(update={"pilot_fingerprint": compute_pilot_fingerprint(draft)})
    manifest_path.write_text(canonical_json_text(changed), encoding="utf-8", newline="\n")


def _regenerate_outer_report_for_nested_fingerprint(root: Path) -> None:
    manifest = FdeCaseRunManifest.model_validate_json(
        (root / "case-manifest.json").read_bytes()
    )
    nested = EvalBundleManifest.model_validate_json(
        (root / "evidence" / "eval-manifest.json").read_bytes()
    )
    acceptance = FdeCaseAcceptance.model_validate_json(
        (root / "acceptance.json").read_bytes()
    )
    definition = load_fde_case(manifest.case_id)
    (root / "report.md").write_text(
        render_case_report(
            definition,
            manifest.source_commit,
            nested.bundle_fingerprint,
            acceptance,
        ),
        encoding="utf-8",
        newline="\n",
    )


def test_two_fresh_honest_runs_have_identical_bytes(
    honest_pilot: Path, tmp_path: Path
) -> None:
    second = tmp_path / "second"
    outcome = run_fde_case("incident-copilot-v1", SOURCE_COMMIT, second)

    assert outcome.exit_code == 0
    assert {path.name for path in honest_pilot.iterdir()} == {
        "case-manifest.json",
        "acceptance.json",
        "report.md",
        "evidence",
    }
    honest_bytes = directory_bytes(honest_pilot)
    assert len(honest_bytes) == 37
    assert honest_bytes == directory_bytes(second)
    assert validate_fde_case(second).overall == "accepted"
    case = load_fde_case("incident-copilot-v1")
    expected_raw_paths = sorted(
        (
            f"runs/{case.selection.suite_id}/{cell.cell_id}/{name}"
            for cell in case.cells
            for name in ("result.json", "events.jsonl", "audit.jsonl")
        ),
        key=lambda path: (path.casefold(), path),
    )
    nested_manifest = EvalBundleManifest.model_validate_json(
        (honest_pilot / "evidence/eval-manifest.json").read_bytes()
    )
    outer_manifest = FdeCaseRunManifest.model_validate_json(
        (honest_pilot / "case-manifest.json").read_bytes()
    )
    assert [artifact.path for artifact in nested_manifest.artifacts] == expected_raw_paths
    assert [artifact.path for artifact in outer_manifest.evidence_artifacts] == sorted(
        [
            "eval-manifest.json",
            "records.jsonl",
            "summary.json",
            "report.md",
            *expected_raw_paths,
        ],
        key=lambda path: (path.casefold(), path),
    )
    records = [
        json.loads(line)
        for line in (honest_pilot / "evidence/records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {record["incident_terminal"] for record in records} == {
        "acknowledged",
        "escalated",
    }
    assert [record["split"] for record in records].count("train") == 6
    assert [record["split"] for record in records].count("validation") == 2
    assert [record["split"] for record in records].count("test") == 2


@pytest.mark.parametrize(
    "orphan_path",
    [
        "runs/unregistered-orphan.txt",
        (
            "runs/system-correctness-v1/"
            "system.incident.restart-stuck-order-worker/unregistered-orphan.txt"
        ),
    ],
)
def test_jointly_resigned_nested_orphan_is_rejected(
    honest_pilot: Path, tmp_path: Path, orphan_path: str
) -> None:
    root = tmp_path / hashlib.sha256(orphan_path.encode()).hexdigest()[:12]
    shutil.copytree(honest_pilot, root)
    target = root / "evidence" / orphan_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unregistered evidence\n", encoding="utf-8", newline="\n")
    _add_and_resign_nested_artifact(root, orphan_path)
    _regenerate_outer_report_for_nested_fingerprint(root)
    _resign_outer(root)

    with pytest.raises(FdeCaseValidationError):
        validate_fde_case(root)


def test_validator_is_byte_preserving_and_does_not_execute(
    honest_pilot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("read-only validator attempted execution")

    monkeypatch.setattr("agent_learning_loop.eval_runner.run_eval", blocked)
    monkeypatch.setattr("agent_learning_loop.incident_runner.run_incident_task", blocked)
    monkeypatch.setattr("agent_learning_loop.incident_environment.IncidentEnvironment", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(socket, "socket", blocked)
    before = directory_bytes(honest_pilot)

    result = validate_fde_case(honest_pilot)

    assert result.overall == "accepted"
    assert result.execution_calls == 0
    assert directory_bytes(honest_pilot) == before


@pytest.mark.parametrize("field", ["numerator", "denominator", "passed_ids", "failed_ids"])
def test_jointly_resigned_acceptance_tamper_is_rejected(
    honest_pilot: Path, tmp_path: Path, field: str
) -> None:
    root = tmp_path / field
    shutil.copytree(honest_pilot, root)
    path = root / "acceptance.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    metric = payload["registered_contracts"]
    if field == "numerator":
        metric[field] = 9
    elif field == "denominator":
        metric[field] = 11
    elif field == "passed_ids":
        metric[field] = metric[field][:-1]
    else:
        metric[field] = [metric["passed_ids"][0]]
    path.write_text(canonical_json_text(payload), encoding="utf-8", newline="\n")
    _resign_outer(root)

    with pytest.raises(FdeCaseValidationError):
        validate_fde_case(root)


def test_jointly_resigned_na_and_report_tamper_are_rejected(
    honest_pilot: Path, tmp_path: Path
) -> None:
    na_root = tmp_path / "na"
    shutil.copytree(honest_pilot, na_root)
    acceptance_path = na_root / "acceptance.json"
    payload = json.loads(acceptance_path.read_text(encoding="utf-8"))
    payload["roi"] = "measured"
    acceptance_path.write_text(
        canonical_json_text(payload), encoding="utf-8", newline="\n"
    )
    _resign_outer(na_root)
    with pytest.raises(FdeCaseValidationError):
        validate_fde_case(na_root)

    report_root = tmp_path / "report"
    shutil.copytree(honest_pilot, report_root)
    report_path = report_root / "report.md"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace("10/10", "9/10", 1),
        encoding="utf-8",
        newline="\n",
    )
    _resign_outer(report_root)
    with pytest.raises(FdeCaseValidationError):
        validate_fde_case(report_root)


def test_resigned_nested_record_and_audit_tamper_are_rejected(
    honest_pilot: Path, tmp_path: Path
) -> None:
    split_root = tmp_path / "split"
    shutil.copytree(honest_pilot, split_root)
    records_path = split_root / "evidence" / "records.jsonl"
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    rows[3]["split"] = "train"
    records_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    _resign_outer(split_root)
    with pytest.raises(FdeCaseValidationError, match="invalid_nested_eval_bundle"):
        validate_fde_case(split_root)

    audit_root = tmp_path / "audit"
    shutil.copytree(honest_pilot, audit_root)
    audit_path = (
        audit_root
        / "evidence/runs/system-correctness-v1/"
        "system.incident.restart-stuck-order-worker/audit.jsonl"
    )
    audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    execution = next(
        row
        for row in audit
        if row["decision"] == "executed"
        and row["tool_name"] == "restart_simulated_service"
    )
    execution["approval_id"] = None
    audit_path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in audit),
        encoding="utf-8",
        newline="\n",
    )
    _resign_nested(audit_root)
    _resign_outer(audit_root)
    with pytest.raises(FdeCaseValidationError, match="invalid_nested_eval_bundle"):
        validate_fde_case(audit_root)


def test_extra_or_traversing_paths_are_rejected(
    honest_pilot: Path, tmp_path: Path
) -> None:
    extra_root = tmp_path / "extra"
    shutil.copytree(honest_pilot, extra_root)
    (extra_root / "orphan.txt").write_text("orphan\n", encoding="utf-8", newline="\n")
    with pytest.raises(FdeCaseValidationError, match="fde_bundle_top_level_shape"):
        validate_fde_case(extra_root)

    traversal_root = tmp_path / "traversal"
    shutil.copytree(honest_pilot, traversal_root)
    manifest_path = traversal_root / "case-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["evidence_artifacts"][0]["path"] = "../outside"
    payload["pilot_fingerprint"] = compute_pilot_fingerprint(payload)
    manifest_path.write_text(canonical_json_text(payload), encoding="utf-8", newline="\n")
    with pytest.raises(FdeCaseValidationError):
        validate_fde_case(traversal_root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_id", None),
        ("approval_id", "foreign-approval"),
        ("target", "foreign-target"),
        ("action_fingerprint", "0" * 64),
        ("operation_id", None),
    ],
)
def test_high_impact_projection_flags_unapproved_identity_changes(
    honest_pilot: Path, field: str, value: object
) -> None:
    audit_path = (
        honest_pilot
        / "evidence/runs/system-correctness-v1/"
        "system.incident.restart-stuck-order-worker/audit.jsonl"
    )
    audit = [
        IncidentAuditRecord.model_validate_json(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    index = next(
        index
        for index, record in enumerate(audit)
        if record.decision == "executed"
        and record.tool_name == "restart_simulated_service"
    )
    audit[index] = audit[index].model_copy(update={field: value})

    assert _unauthorized_execution_ids(
        "system.incident.restart-stuck-order-worker",
        "incident.restart-stuck-order-worker",
        audit,
    )


def test_high_impact_projection_flags_denied_and_duplicate_physical_execution(
    honest_pilot: Path,
) -> None:
    denied_path = (
        honest_pilot
        / "evidence/runs/system-correctness-v1/"
        "system.incident.escalate-denied-payment-restart/audit.jsonl"
    )
    denied_audit = [
        IncidentAuditRecord.model_validate_json(line)
        for line in denied_path.read_text(encoding="utf-8").splitlines()
    ]
    denied = next(
        record
        for record in denied_audit
        if record.category == "approval" and record.decision == "denied"
    )
    denied_audit.append(
        denied.model_copy(
            update={
                "sequence": len(denied_audit),
                "category": "execution",
                "decision": "executed",
                "operation_id": "forged-denied-operation",
                "physical_mutation": True,
            }
        )
    )
    assert _unauthorized_execution_ids(
        "system.incident.escalate-denied-payment-restart",
        "incident.escalate-denied-payment-restart",
        denied_audit,
    )

    restart_path = (
        honest_pilot
        / "evidence/runs/system-correctness-v1/"
        "system.incident.restart-stuck-order-worker/audit.jsonl"
    )
    restart_audit = [
        IncidentAuditRecord.model_validate_json(line)
        for line in restart_path.read_text(encoding="utf-8").splitlines()
    ]
    execution = next(
        record
        for record in restart_audit
        if record.decision == "executed"
        and record.tool_name == "restart_simulated_service"
    )
    restart_audit.append(
        execution.model_copy(
            update={"sequence": len(restart_audit), "physical_mutation": True}
        )
    )
    assert len(
        _unauthorized_execution_ids(
            "system.incident.restart-stuck-order-worker",
            "incident.restart-stuck-order-worker",
            restart_audit,
        )
    ) == 2
