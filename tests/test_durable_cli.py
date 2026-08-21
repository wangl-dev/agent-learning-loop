from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_learning_loop", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def durable_arguments(output: Path, checkpointing: str) -> list[str]:
    return [
        "run-durable",
        "--task",
        "workspace.fix-config",
        "--mode",
        "safeguarded",
        "--failure-schedule",
        "workspace.lost-write-result.v1",
        "--interruption-schedule",
        "workspace.post-write-boundary.v1",
        "--checkpointing",
        checkpointing,
        "--output-dir",
        str(output),
    ]


def test_two_real_cli_processes_interrupt_then_resume_and_validate(tmp_path: Path) -> None:
    output = tmp_path / "on"

    interrupted = run_cli(*durable_arguments(output, "on"))
    prefix = (output / "events.jsonl").read_bytes()
    resumed = run_cli("resume-runtime", "--run-dir", str(output))
    validated = run_cli("validate-trajectory", "--run-dir", str(output))

    assert interrupted.returncode == 6
    assert not (output / "result.json").exists() or resumed.returncode == 0
    assert resumed.returncode == 0, resumed.stderr
    assert validated.returncode == 0, validated.stderr
    assert (output / "events.jsonl").read_bytes().startswith(prefix)
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["schema_version"] == "3"
    assert result["terminal_state"] == "SUCCEEDED"
    assert result["resumed"] is True
    assert result["usage"]["physical_write_executions"] == 1
    assert result["usage"]["duplicate_side_effects"] == 0
    assert "valid_completed" in validated.stdout


def test_checkpoint_off_resume_is_exit_two_and_does_not_change_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "off"
    interrupted = run_cli(*durable_arguments(output, "off"))
    journal = (output / "events.jsonl").read_bytes()
    workspace = (output / "workspace" / "app.conf").read_bytes()

    resumed = run_cli("resume-runtime", "--run-dir", str(output))
    validated = run_cli("validate-trajectory", "--run-dir", str(output))

    assert interrupted.returncode == 6
    assert resumed.returncode == 2
    assert validated.returncode == 0
    assert "valid_partial" in validated.stdout
    assert (output / "events.jsonl").read_bytes() == journal
    assert (output / "workspace" / "app.conf").read_bytes() == workspace
    assert not (output / "result.json").exists()


def test_uninterrupted_reference_finishes_without_an_interruption_schedule(
    tmp_path: Path,
) -> None:
    output = tmp_path / "reference"
    result = run_cli(
        "run-durable",
        "--task",
        "workspace.fix-config",
        "--mode",
        "safeguarded",
        "--failure-schedule",
        "workspace.lost-write-result.v1",
        "--checkpointing",
        "off",
        "--output-dir",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert payload["terminal_state"] == "SUCCEEDED"
    assert payload["resumed"] is False
    assert payload["segment_count"] == 1
