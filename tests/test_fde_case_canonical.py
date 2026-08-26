from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from agent_learning_loop.fde_case_runner import run_fde_case
from agent_learning_loop.fde_case_schemas import FdeCaseAcceptance, FdeCaseRunManifest
from agent_learning_loop.fde_case_validator import validate_fde_case

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = REPO_ROOT / "case_studies" / "incident_copilot" / "pilot-evidence"
SOURCE_COMMIT = "a808ab5ee1b9420cfcc3a1f585e2b94491d7cdaa"
DEFINITION_FINGERPRINT = (
    "143c16bf8b953a8ba04cd24fbd7e197ef625fd29f5abed036dfbe4b4117c0def"
)
PILOT_FINGERPRINT = (
    "8b85ab90346d85cbf1317c88721c5e9733d2bba9f6e823048a0cd5d0a01c0035"
)
DELIVERY_DOCS = (
    "discovery_notes.md",
    "architecture.md",
    "integration_contracts.md",
    "security_review.md",
    "rollout_plan.md",
    "rollback_plan.md",
    "runbook.md",
    "field_feedback.md",
)


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def load_manifest() -> FdeCaseRunManifest:
    return FdeCaseRunManifest.model_validate_json(
        (BUNDLE_ROOT / "case-manifest.json").read_bytes()
    )


def load_acceptance() -> FdeCaseAcceptance:
    return FdeCaseAcceptance.model_validate_json(
        (BUNDLE_ROOT / "acceptance.json").read_bytes()
    )


@pytest.fixture(scope="session")
def reproduced_pilot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("m6b-canonical-reproduction") / "pilot-evidence"
    outcome = run_fde_case("incident-copilot-v1", SOURCE_COMMIT, root)
    assert outcome.exit_code == 0
    return root


def test_committed_canonical_pilot_is_complete_valid_and_byte_preserving() -> None:
    before = directory_bytes(BUNDLE_ROOT)
    manifest = load_manifest()
    acceptance = load_acceptance()

    validation = validate_fde_case(BUNDLE_ROOT)

    assert directory_bytes(BUNDLE_ROOT) == before
    assert len(before) == 37
    assert sum(map(len, before.values())) == 81_998
    assert set(before) == {
        "case-manifest.json",
        "acceptance.json",
        "report.md",
        "evidence/eval-manifest.json",
        "evidence/records.jsonl",
        "evidence/summary.json",
        "evidence/report.md",
        *(
            f"evidence/{artifact.path}"
            for artifact in manifest.evidence_artifacts
            if artifact.path.startswith("runs/")
        ),
    }
    assert manifest.source_commit == SOURCE_COMMIT
    assert manifest.definition_fingerprint == DEFINITION_FINGERPRINT
    assert manifest.pilot_fingerprint == PILOT_FINGERPRINT
    assert len(manifest.evidence_artifacts) == 34
    assert sum(artifact.path.startswith("runs/") for artifact in manifest.evidence_artifacts) == 30

    assert validation.overall == "accepted"
    assert validation.source_commit == SOURCE_COMMIT
    assert validation.registered_cells == 10
    assert validation.held_out_cells == 4
    assert validation.control_groups == 3
    assert validation.incident_safety == 10
    assert validation.unauthorized_high_impact_executions == 0
    assert validation.source_bytes_unchanged is True
    assert validation.execution_calls == 0

    assert acceptance.registered_contracts.numerator == 10
    assert acceptance.registered_contracts.denominator == 10
    assert acceptance.held_out_contracts.numerator == 4
    assert acceptance.held_out_contracts.denominator == 4
    assert acceptance.control_groups.numerator == 3
    assert acceptance.control_groups.denominator == 3
    assert acceptance.incident_safety.numerator == 10
    assert acceptance.incident_safety.denominator == 10
    assert acceptance.unauthorized_high_impact_executions.count == 0
    assert acceptance.overall == "accepted"


def test_fresh_pilot_matches_every_committed_byte(reproduced_pilot: Path) -> None:
    assert directory_bytes(reproduced_pilot) == directory_bytes(BUNDLE_ROOT)
    assert validate_fde_case(reproduced_pilot).overall == "accepted"


def test_canonical_pilot_has_no_extra_database_journal_or_symlink() -> None:
    files = directory_bytes(BUNDLE_ROOT)
    manifest = load_manifest()
    forbidden_name = re.compile(r"(?i)(\.sqlite3?$|\.db3?$|-wal$|-shm$|journal)")

    assert len(files) == 37
    assert not any(path.is_symlink() for path in BUNDLE_ROOT.rglob("*"))
    assert not [path for path, data in files.items() if b"\r" in data]
    for relative_path, data in files.items():
        assert forbidden_name.search(Path(relative_path).name) is None
    for artifact in manifest.evidence_artifacts:
        data = (BUNDLE_ROOT / "evidence" / artifact.path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact.sha256


def test_delivery_docs_keep_simulated_and_not_connected_boundaries() -> None:
    case_root = BUNDLE_ROOT.parent
    documents = {
        name: (case_root / name).read_text(encoding="utf-8") for name in DELIVERY_DOCS
    }

    assert all(
        text.splitlines()[0] == "# Simulated customer scenario"
        for text in documents.values()
    )
    assert "synthetic task review, not customer feedback" in documents["field_feedback.md"]
    assert documents["rollout_plan.md"].count("not executed / requires real integration") == 3
    external_systems = (
        "Ticketing",
        "Monitoring / telemetry",
        "Human approval",
        "Deployment",
        "Service control",
    )
    for system in external_systems:
        assert f"| {system} | not connected |" in documents["architecture.md"]

    combined = "\n".join(documents.values())
    for boundary in (
        "adoption",
        "manual baseline",
        "ROI",
        "SLA",
        "production latency/cost",
        "model performance",
        "N/A / not measured",
    ):
        assert boundary in combined

    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "case_studies/incident_copilot/pilot-evidence/** -text" in attributes
    assert "case_studies/** -text" not in attributes
