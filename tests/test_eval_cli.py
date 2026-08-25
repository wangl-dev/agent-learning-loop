from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_learning_loop.cli import main

SOURCE_COMMIT = "7" * 40


def test_eval_cli_runs_filtered_suite_then_validates_read_only(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "eval"

    assert (
        main(
            [
                "run-eval",
                "--suite",
                "system-correctness",
                "--source-commit",
                SOURCE_COMMIT,
                "--environment",
                "workspace",
                "--split",
                "train",
                "--output-dir",
                str(run_dir),
            ]
        )
        == 0
    )
    assert main(["validate-eval", "--run-dir", str(run_dir)]) == 0
    manifest = json.loads(
        (run_dir / "eval-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_commit"] == SOURCE_COMMIT
    assert manifest["selection"]["selected_total"] == 6


def test_eval_cli_maps_invalid_identity_and_bundle_to_exit_two(tmp_path: Path) -> None:
    assert (
        main(
            [
                "run-eval",
                "--suite",
                "runtime-reliability",
                "--source-commit",
                "NOT-A-COMMIT",
                "--output-dir",
                str(tmp_path / "bad"),
            ]
        )
        == 2
    )
    assert main(["validate-eval", "--run-dir", str(tmp_path / "missing")]) == 2


@pytest.mark.parametrize("failure", ["timeout", "runtime"])
def test_eval_cli_maps_infrastructure_failures_to_exit_two_and_cleans_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    run_dir = tmp_path / failure

    def fail_recovery(*args: object, **kwargs: object) -> None:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["python", "-m", "resume"], timeout=30)
        raise RuntimeError("controlled output collision")

    monkeypatch.setattr(
        "agent_learning_loop.eval_runner.execute_recovery_suite", fail_recovery
    )

    assert (
        main(
            [
                "run-eval",
                "--suite",
                "recovery-replay",
                "--source-commit",
                SOURCE_COMMIT,
                "--output-dir",
                str(run_dir),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "run-eval validation error:" in captured.err
    assert "Traceback" not in captured.err
    assert not run_dir.exists()
