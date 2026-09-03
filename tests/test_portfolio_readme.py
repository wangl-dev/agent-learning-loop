from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TOUR = ROOT / "docs" / "technical-tour.md"


def _relative_targets(text: str, source: Path) -> list[Path]:
    targets: list[Path] = []
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if "://" in target or target.startswith("#"):
            continue
        target = target.split("#", 1)[0]
        if target:
            targets.append((source.parent / target).resolve())
    return targets


def test_portfolio_entry_preserves_truthful_evidence_links() -> None:
    readme = README.read_text(encoding="utf-8")
    tour = TOUR.read_text(encoding="utf-8")
    summary = json.loads(
        (ROOT / "reports" / "v0.1" / "eval-bundle" / "summary.json").read_text(
            encoding="utf-8"
        )
    )
    acceptance_path = (
        ROOT
        / "case_studies"
        / "incident_copilot"
        / "pilot-evidence"
        / "acceptance.json"
    )
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (ROOT / "reports" / "v0.1" / "eval-bundle" / "records.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    for required in ("563 passed", "30/30", "1/10", "4/10", "0/3", "not training-ready"):
        assert required in readme
    assert "30 seconds" in readme and "10 minutes" in readme and "30 minutes" in readme
    assert "lost.naive" in tour
    assert "reference action leakage" in tour
    system_slices = [
        slice for slice in summary["system_slices"] if slice["dimension"] == "environment"
    ]
    assert sum(slice["selected"] for slice in system_slices) == 30
    assert sum(slice["verifier_passed"] for slice in system_slices) == 30
    assert acceptance["registered_contracts"]["numerator"] == 10
    assert acceptance["unauthorized_high_impact_executions"]["count"] == 0

    pair = next(
        item
        for item in summary["pair_deltas"]
        if item["comparison_id"] == "lost-result-idempotency"
    )
    arms = {
        record["arm"]: record
        for record in records
        if record["pair_id"] == pair["comparison_id"]
    }
    baseline = arms["baseline"]
    mechanism = arms["mechanism"]
    metrics = (
        ("physical_executions", "physical executions"),
        ("physical_write_executions", "physical writes"),
        ("duplicate_side_effects", "duplicate side effects"),
        ("idempotency_hits", "idempotency hits"),
    )
    for key, label in metrics:
        assert f"{label} `{baseline[key]} → {mechanism[key]}`" in readme
    deltas = (
        ("physical_execution_delta", "physical_executions"),
        ("physical_write_delta", "physical_write_executions"),
        ("duplicate_side_effect_delta", "duplicate_side_effects"),
        ("idempotency_hit_delta", "idempotency_hits"),
    )
    for delta_key, record_key in deltas:
        assert pair[delta_key] == mechanism[record_key] - baseline[record_key]

    for source_name in (
        "failure_schedules.py",
        "runtime.py",
        "durable_runtime.py",
        "incident_runner.py",
        "dataops_runner.py",
        "eval_runner.py",
        "eval_validator.py",
        "sft_exporter.py",
        "sft_validator.py",
        "fde_case_runner.py",
        "fde_case_validator.py",
        "model_probe_runner.py",
        "model_probe_parser.py",
        "model_probe_validator.py",
    ):
        assert f"../src/agent_learning_loop/{source_name}" in tour

    for text, source in ((readme, README), (tour, TOUR)):
        assert all(target.exists() for target in _relative_targets(text, source))
        assert re.search(r"(?i)[a-z]:\\users\\|/home/|/users/", text) is None
        assert "real customer outcome" not in text.casefold()
        assert "demonstrated training benefit" not in text.casefold()


def test_portfolio_entry_removes_superseded_boundary_language() -> None:
    readme = README.read_text(encoding="utf-8").casefold()
    assert "no model adapter" not in readme
    assert "fde remains out of scope" not in readme
    assert "wait for v0.2" not in readme
