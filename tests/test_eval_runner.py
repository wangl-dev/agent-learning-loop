from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_validator import validate_eval_bundle
from agent_learning_loop.runtime import execute_runtime_task as original_execute_runtime_task

SOURCE_COMMIT = "6" * 40


def read_records(run_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_real_system_30_and_reliability_7_suites_write_valid_bundles(
    tmp_path: Path,
) -> None:
    system_dir = tmp_path / "system"
    reliability_dir = tmp_path / "reliability"

    system = run_eval("system-correctness", SOURCE_COMMIT, system_dir)
    reliability = run_eval("runtime-reliability", SOURCE_COMMIT, reliability_dir)

    assert system.exit_code == 0
    assert system.summary.selected_total == 30
    assert system.summary.verifier_state_success.numerator == 30
    assert system.summary.runtime_completion_success == "N/A"
    assert reliability.exit_code == 0
    assert reliability.summary.selected_total == 7
    assert len(reliability.summary.pair_deltas) == 3
    lost_naive = next(
        record for record in read_records(reliability_dir) if record["cell_id"] == "lost.naive"
    )
    assert lost_naive["verifier_state_success"] is True
    assert lost_naive["runtime_completion_success"] is False
    assert lost_naive["cell_contract_passed"] is True
    assert validate_eval_bundle(system_dir).selected_cells == 30
    assert validate_eval_bundle(reliability_dir).selected_cells == 7
    public_text = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in system_dir.rglob("*")
        if path.is_file()
    ).lower()
    assert not list(system_dir.rglob("*.sqlite"))
    assert not list(system_dir.rglob("*.db"))
    assert '"private"' not in public_text
    assert "required_files" not in public_text
    assert "\\users\\" not in public_text


def test_real_recovery_replay_suite_uses_second_process_and_fixed_diagnostics(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "recovery"

    result = run_eval("recovery-replay", SOURCE_COMMIT, run_dir)
    records = read_records(run_dir)

    assert result.exit_code == 0
    assert len(records) == 4
    assert {record["diagnostic"] for record in records} == {
        "checkpoint_off",
        "checkpoint_on",
        "reference",
        "action_replay",
    }
    on = json.loads(
        (
            run_dir
            / "runs/recovery-replay-v1/recovery.checkpoint-on/diagnostic.json"
        ).read_text(encoding="utf-8")
    )
    replay = json.loads(
        (
            run_dir
            / "runs/recovery-replay-v1/recovery.action-replay/diagnostic.json"
        ).read_text(encoding="utf-8")
    )
    assert on["second_process_used"] is True
    assert on["reference_match"] is True
    assert replay["vertical_slice_matches"] == replay["vertical_slice_total"] == 1
    assert replay["policy_calls"] == 0
    assert {record["physical_executions"] for record in records} == {2}
    assert {record["physical_write_executions"] for record in records} == {1}


def test_real_all_has_exact_usage_and_complete_markdown_tables(tmp_path: Path) -> None:
    run_dir = tmp_path / "all"

    result = run_eval("all", SOURCE_COMMIT, run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert result.summary.selected_total == 41
    physical_executions = result.summary.physical_executions
    physical_writes = result.summary.physical_writes
    assert physical_executions != "N/A"
    assert physical_writes != "N/A"
    assert physical_executions.model_dump() == {
        "numerator": 22,
        "denominator": 11,
        "rate": 2.0,
    }
    assert physical_writes.model_dump() == {
        "numerator": 10,
        "denominator": 11,
        "rate": 10 / 11,
    }
    assert "## System correctness by environment" in report
    assert "| workspace | 10/10 |" in report
    assert "| incident | 10/10 |" in report
    assert "| dataops | 10/10 |" in report
    assert "## System correctness by split" in report
    assert "| train | 18/18 |" in report
    assert "| validation | 6/6 |" in report
    assert "| test | 6/6 |" in report
    assert "physical executions: `22/11 (2)`" in report
    assert "physical writes: `10/11" in report
    assert "verifier Δ" in report
    assert "physical execution Δ" in report
    assert "retry Δ" in report
    assert "source commit was explicitly supplied by the caller" in report


def test_valid_subset_and_complete_pair_keep_their_real_denominator(tmp_path: Path) -> None:
    subset = run_eval(
        "system-correctness",
        SOURCE_COMMIT,
        tmp_path / "subset",
        environment="workspace",
        split="train",
    )
    pair = run_eval(
        "runtime-reliability",
        SOURCE_COMMIT,
        tmp_path / "pair",
        pair="lost-result-idempotency",
    )

    assert subset.manifest.selection.candidate_total == 30
    assert subset.manifest.selection.selected_total == 6
    assert pair.manifest.selection.candidate_total == 7
    assert pair.manifest.selection.cell_ids == ["lost.retry", "lost.idempotent"]
    assert len(pair.summary.pair_deltas) == 1


def test_expected_naive_failures_are_valid_but_oracle_drift_is_exit_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def inject_one_raw_oracle_deviation(*args: Any, **kwargs: Any) -> object:
        result = original_execute_runtime_task(*args, **kwargs)
        run_directory = Path(args[1])
        if run_directory.name != "transient.naive":
            return result

        result_path = run_directory / "result.json"
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        result_payload["usage"]["steps"] += 1
        result_path.write_text(json.dumps(result_payload), encoding="utf-8")

        events_path = run_directory / "events.jsonl"
        event_payloads = [
            json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
        ]
        assert event_payloads[-1]["event_kind"] == "run_finished"
        event_payloads[-1]["step_index"] += 1
        events_path.write_text(
            "".join(
                json.dumps(payload, separators=(",", ":")) + "\n"
                for payload in event_payloads
            ),
            encoding="utf-8",
        )
        return result

    honest = run_eval("runtime-reliability", SOURCE_COMMIT, tmp_path / "honest")
    monkeypatch.setattr(
        "agent_learning_loop.eval_runner.execute_runtime_task",
        inject_one_raw_oracle_deviation,
    )
    drift = run_eval("runtime-reliability", SOURCE_COMMIT, tmp_path / "drift")

    assert honest.exit_code == 0
    honest_transient = next(
        record
        for record in read_records(tmp_path / "honest")
        if record["cell_id"] == "transient.naive"
    )
    assert honest_transient["terminal"] == "FAILED"
    assert honest_transient["runtime_completion_success"] is False
    assert honest_transient["cell_contract_passed"] is True
    assert drift.exit_code == 1
    assert drift.summary.oracle_failure_cell_ids == ["transient.naive"]
    assert [item.model_dump() for item in drift.summary.oracle_failures] == [
        {
            "cell_id": "transient.naive",
            "error_category": "tool_transient",
            "raw_result_path": (
                "runs/runtime-reliability-v1/transient.naive/result.json"
            ),
        }
    ]
    drift_dir = tmp_path / "drift"
    assert {
        "eval-manifest.json",
        "records.jsonl",
        "summary.json",
        "report.md",
    } <= {path.name for path in drift_dir.iterdir() if path.is_file()}
    drift_record = next(
        record
        for record in read_records(drift_dir)
        if record["cell_id"] == "transient.naive"
    )
    assert drift_record["steps"] == 2
    assert drift_record["cell_contract_passed"] is False
    raw_result = drift_dir / str(drift_record["raw_result_path"])
    assert raw_result.is_file()

    monkeypatch.undo()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from agent_learning_loop.eval_validator import validate_eval_bundle; "
                "print(validate_eval_bundle(Path(sys.argv[1])).selected_cells)"
            ),
            str(drift_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "7"


def test_infrastructure_failure_cleans_partial_output_and_existing_dir_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = tmp_path / "partial"

    def fail_execution(*args: object, **kwargs: object) -> None:
        raise OSError("controlled infrastructure failure")

    monkeypatch.setattr(
        "agent_learning_loop.eval_runner.execute_runtime_task", fail_execution
    )
    with pytest.raises(OSError, match="controlled infrastructure"):
        run_eval("runtime-reliability", SOURCE_COMMIT, partial)
    assert not partial.exists()

    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must_not_exist"):
        run_eval("runtime-reliability", SOURCE_COMMIT, existing)
    assert marker.read_text(encoding="utf-8") == "keep\n"
