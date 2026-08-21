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


def source_arguments(output: Path, *, record_actions: str = "on") -> list[str]:
    return [
        "run-durable",
        "--task",
        "workspace.fix-config",
        "--mode",
        "safeguarded",
        "--failure-schedule",
        "workspace.lost-write-result.v1",
        "--checkpointing",
        "off",
        "--record-actions",
        record_actions,
        "--output-dir",
        str(output),
    ]


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_real_cli_source_then_replay_is_one_of_one_and_source_is_immutable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "replay"

    recorded = run_cli(*source_arguments(source))
    source_before = directory_bytes(source)
    replayed = run_cli(
        "replay-actions",
        "--source-run-dir",
        str(source),
        "--output-dir",
        str(output),
    )

    assert recorded.returncode == 0, recorded.stderr
    assert replayed.returncode == 0, replayed.stderr
    assert directory_bytes(source) == source_before
    action_lines = (source / "actions.jsonl").read_text(encoding="utf-8").splitlines()
    result = json.loads((output / "replay-result.json").read_text(encoding="utf-8"))
    assert len(action_lines) == 6
    assert result["actions_resolved"] == 2
    assert result["step_digests_matched"] == 2
    assert result["final_snapshot_match"] is True
    assert result["verifier_match"] is True
    assert result["usage"]["physical_write_executions"] == 1
    assert result["usage"]["duplicate_side_effects"] == 0
    assert result["action_replay_match_rate"] == 1.0
    assert "1/1 vertical-slice smoke" in replayed.stdout


def test_record_actions_default_off_and_interrupted_recording_is_exit_two(
    tmp_path: Path,
) -> None:
    default_source = tmp_path / "default"
    default = run_cli(*source_arguments(default_source, record_actions="off"))
    assert default.returncode == 0
    assert not (default_source / "actions.jsonl").exists()

    interrupted = tmp_path / "interrupted"
    invalid = run_cli(
        *source_arguments(interrupted),
        "--interruption-schedule",
        "workspace.post-write-boundary.v1",
    )
    assert invalid.returncode == 2
    assert not interrupted.exists()


def test_replay_cli_path_validation_is_exit_two_without_result(tmp_path: Path) -> None:
    source = tmp_path / "source"
    assert run_cli(*source_arguments(source)).returncode == 0

    replayed = run_cli(
        "replay-actions",
        "--source-run-dir",
        str(source),
        "--output-dir",
        str(source / "nested"),
    )

    assert replayed.returncode == 2
    assert not (source / "nested").exists()
