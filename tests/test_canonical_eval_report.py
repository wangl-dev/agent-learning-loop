from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Literal

import pytest

from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_schemas import (
    EvalBundleManifest,
    EvalSummary,
    ExactRatio,
    NormalizedEvalRecord,
)
from agent_learning_loop.eval_validator import validate_eval_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = REPO_ROOT / "reports" / "v0.1" / "eval-bundle"
SOURCE_COMMIT = "a00da937e299c99031f7f4711da5dd3eeef50e22"
BUNDLE_FINGERPRINT = "aefc0385680f827bbf45887a1ef335cb93f2826e16539e570f2f56c3028a8856"
SUITE_FINGERPRINTS = {
    "system-correctness-v1": "624dfb19c2b9575056dd9d24a92e3dcb4852617eb538ee3541fb28cae933488e",
    "runtime-reliability-v1": "a8c5e2389ce1bbe31ae7895ecbfe211be3460aee40563c4c13efb0523d89ac2e",
    "recovery-replay-v1": "4fb499de8c42ac2d78aaa962c6e6fda2419e7df5fda45c64287d5b78d23b9a97",
}


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_manifest() -> EvalBundleManifest:
    return EvalBundleManifest.model_validate_json(
        (BUNDLE_ROOT / "eval-manifest.json").read_text(encoding="utf-8")
    )


def load_summary() -> EvalSummary:
    return EvalSummary.model_validate_json(
        (BUNDLE_ROOT / "summary.json").read_text(encoding="utf-8")
    )


def load_records() -> list[NormalizedEvalRecord]:
    return [
        NormalizedEvalRecord.model_validate_json(line)
        for line in (BUNDLE_ROOT / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def require_ratio(value: ExactRatio | Literal["N/A"]) -> ExactRatio:
    assert isinstance(value, ExactRatio)
    return value


@pytest.fixture(scope="session")
def reproduced_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("canonical-reproduction") / "eval-bundle"
    outcome = run_eval("all", SOURCE_COMMIT, root)
    assert outcome.exit_code == 0
    validation = validate_eval_bundle(root)
    assert validation.selected_cells == 41
    assert validation.execution_calls == 0
    assert validation.source_bytes_unchanged is True
    return root


def test_committed_canonical_bundle_is_complete_and_valid() -> None:
    before = directory_bytes(BUNDLE_ROOT)
    validation = validate_eval_bundle(BUNDLE_ROOT)
    after = directory_bytes(BUNDLE_ROOT)
    manifest = load_manifest()

    assert before == after
    assert validation.source_commit == SOURCE_COMMIT
    assert validation.selected_cells == 41
    assert validation.execution_calls == 0
    assert validation.source_bytes_unchanged is True
    assert manifest.source_commit == SOURCE_COMMIT
    assert manifest.package_version == "0.1.0.dev0"
    assert manifest.proposal_version == "1.10"
    assert manifest.selection.suite == "all"
    assert manifest.selection.candidate_total == 41
    assert manifest.selection.selected_total == 41
    assert manifest.bundle_fingerprint == BUNDLE_FINGERPRINT
    assert manifest.suite_fingerprints == SUITE_FINGERPRINTS

    expected_inventory = {
        "eval-manifest.json",
        "records.jsonl",
        "summary.json",
        "report.md",
        *(artifact.path for artifact in manifest.artifacts),
    }
    assert set(before) == expected_inventory
    assert len(manifest.artifacts) == 163
    assert len(before) == 167
    assert sum(map(len, before.values())) == 421_449
    assert not any(path.is_symlink() for path in BUNDLE_ROOT.rglob("*"))
    for artifact in manifest.artifacts:
        assert hashlib.sha256(before[artifact.path]).hexdigest() == artifact.sha256


def test_canonical_summary_matches_hand_calculated_records() -> None:
    records = load_records()
    summary = load_summary()
    system = [record for record in records if record.kind == "system"]
    reliability = [record for record in records if record.kind == "reliability"]
    recovery = [record for record in records if record.kind == "recovery"]

    assert len(records) == 41
    assert len({record.cell_id for record in records}) == 41
    assert all(record.cell_contract_passed for record in records)
    assert (len(system), len(reliability), len(recovery)) == (30, 7, 4)
    assert Counter(record.environment for record in system) == {
        "workspace": 10,
        "incident": 10,
        "dataops": 10,
    }
    assert Counter(record.split for record in system) == {
        "train": 18,
        "validation": 6,
        "test": 6,
    }
    assert all(record.verifier_state_success is True for record in system)

    boolean_state = [
        record.verifier_state_success
        for record in records
        if isinstance(record.verifier_state_success, bool)
    ]
    boolean_completion = [
        record.runtime_completion_success
        for record in records
        if isinstance(record.runtime_completion_success, bool)
    ]
    assert (sum(boolean_state), len(boolean_state)) == (38, 40)
    assert (sum(boolean_completion), len(boolean_completion)) == (6, 10)
    assert summary.verifier_state_success == ExactRatio(numerator=38, denominator=40, rate=0.95)
    assert require_ratio(summary.runtime_completion_success) == ExactRatio(
        numerator=6, denominator=10, rate=0.6
    )

    effect_records = reliability + recovery
    assert sum(record.physical_executions or 0 for record in effect_records) == 22
    assert sum(record.physical_write_executions or 0 for record in effect_records) == 10
    assert sum(record.duplicate_side_effects or 0 for record in effect_records) == 1
    assert sum(record.retries or 0 for record in reliability) == 4
    assert sum(record.idempotency_hits or 0 for record in reliability) == 1
    assert require_ratio(summary.physical_executions) == ExactRatio(
        numerator=22, denominator=11, rate=2.0
    )
    assert require_ratio(summary.physical_writes) == ExactRatio(
        numerator=10, denominator=11, rate=10 / 11
    )

    assert [item.model_dump() for item in summary.pair_deltas] == [
        {
            "comparison_id": "transient-retry",
            "baseline_cell_id": "transient.naive",
            "mechanism_cell_id": "transient.retry",
            "completion_delta": 1,
            "verifier_delta": 1,
            "duplicate_side_effect_delta": 0,
            "physical_execution_delta": 3,
            "physical_write_delta": 1,
            "retry_delta": 1,
            "idempotency_hit_delta": 0,
        },
        {
            "comparison_id": "timeout-retry",
            "baseline_cell_id": "timeout.naive",
            "mechanism_cell_id": "timeout.retry",
            "completion_delta": 1,
            "verifier_delta": 1,
            "duplicate_side_effect_delta": 0,
            "physical_execution_delta": 2,
            "physical_write_delta": 1,
            "retry_delta": 1,
            "idempotency_hit_delta": 0,
        },
        {
            "comparison_id": "lost-result-idempotency",
            "baseline_cell_id": "lost.retry",
            "mechanism_cell_id": "lost.idempotent",
            "completion_delta": 0,
            "verifier_delta": 0,
            "duplicate_side_effect_delta": -1,
            "physical_execution_delta": -1,
            "physical_write_delta": -1,
            "retry_delta": 0,
            "idempotency_hit_delta": 1,
        },
    ]
    assert [(item.cell_id, item.passed, item.detail) for item in summary.diagnostics] == [
        ("recovery.checkpoint-off", True, "fixed recovery diagnostic"),
        ("recovery.checkpoint-on", True, "fixed recovery diagnostic"),
        ("recovery.reference", True, "fixed recovery diagnostic"),
        ("recovery.action-replay", True, "1/1 vertical-slice diagnostic"),
    ]
    assert summary.oracle_failure_cell_ids == []
    assert summary.model == "N/A"
    assert summary.token_cost == "N/A"
    assert summary.latency == "observed/non-comparable"


def test_fresh_all_run_matches_every_committed_bundle_byte(
    reproduced_bundle: Path,
) -> None:
    assert directory_bytes(reproduced_bundle) == directory_bytes(BUNDLE_ROOT)


def test_readme_numbers_are_derived_from_canonical_models() -> None:
    manifest = load_manifest()
    summary = load_summary()
    root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    report_readme = (REPO_ROOT / "reports" / "v0.1" / "README.md").read_text(
        encoding="utf-8"
    )
    adr = (REPO_ROOT / "docs" / "decisions" / "0010-m5b-canonical-evidence.md").read_text(
        encoding="utf-8"
    )

    fragments = {
        manifest.source_commit,
        manifest.bundle_fingerprint,
        f"{manifest.selection.selected_total}/{manifest.selection.candidate_total}",
        "30/30",
        (
            f"{summary.verifier_state_success.numerator}/"
            f"{summary.verifier_state_success.denominator}"
        ),
        (
            f"{require_ratio(summary.runtime_completion_success).numerator}/"
            f"{require_ratio(summary.runtime_completion_success).denominator}"
        ),
    }
    for text in (root_readme, report_readme):
        assert all(fragment in text for fragment in fragments)
    assert "reports/v0.1/FAILURE_ANALYSIS.md" in root_readme
    assert "1/1 vertical-slice" in root_readme
    assert SOURCE_COMMIT in adr
    assert "published M5A.1 evaluator-plus-portability commit" in adr


def test_canonical_bundle_has_no_private_database_or_machine_artifact() -> None:
    files = directory_bytes(BUNDLE_ROOT)
    total_bytes = sum(map(len, files.values()))
    forbidden_name = re.compile(r"(?i)(\.sqlite3?$|\.db3?$|-wal$|-shm$|journal)")
    forbidden_text = re.compile(
        r"(?i)([A-Z]:\\Users\\|/home/|/Users/|AppData|Temp\\|Traceback \(most recent call last\)|"
        r"BEGIN (?:RSA|OPENSSH|EC|DSA|PRIVATE) KEY|gh[pousr]_[A-Za-z0-9]|github_pat_|"
        r"sk-[A-Za-z0-9]|\"(?:private|required)\"\s*:)"
    )

    assert total_bytes < 2 * 1024 * 1024
    for relative_path, data in files.items():
        assert len(data) < 1024 * 1024
        assert forbidden_name.search(Path(relative_path).name) is None
        text = data.decode("utf-8")
        assert forbidden_text.search(text) is None

    report_readme = (REPO_ROOT / "reports" / "v0.1" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "project-authored Apache-2.0 synthetic tasks" in report_readme
