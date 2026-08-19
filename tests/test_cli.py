from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_module_version_command_reports_project_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_learning_loop", "--version"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "agent-learning-loop 0.1.0.dev0"
    assert result.stderr == ""


def test_workspace_command_runs_three_tasks_and_writes_machine_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "runs"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_learning_loop",
            "run-workspace",
            "--task",
            "all",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.count("passed") == 3
    assert len(list(output_dir.glob("*/events.jsonl"))) == 3
    result_files = list(output_dir.glob("*/result.json"))
    assert len(result_files) == 3
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["outcome"] == "passed"
        for path in result_files
    )


def external_fixture(*, setup_path: str = "app.conf", expected: str) -> dict[str, object]:
    return {
        "task": {
            "schema_version": "1",
            "task_id": "workspace.fix-config",
            "environment_kind": "workspace",
            "instruction": "Set mode to production and keep the port unchanged.",
            "allowed_tools": ["read_text", "write_text"],
            "fixture_id": "workspace.cli-test.v1",
            "provenance": "Project-authored CLI test fixture.",
        },
        "private": {
            "setup": {"files": {setup_path: "mode=debug\nport=8080\n"}},
            "expected": {
                "required_files": {"app.conf": expected},
                "unchanged_files": [],
                "allowed_mutations": ["app.conf"],
                "forbidden_paths": [],
            },
        },
    }


def run_external_fixture(
    tmp_path: Path,
    data: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(data), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_learning_loop",
            "run-workspace",
            "--task-file",
            str(fixture_path),
            "--output-dir",
            str(tmp_path / "runs"),
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def test_workspace_command_returns_nonzero_for_invalid_schema(tmp_path: Path) -> None:
    data = external_fixture(expected="mode=production\nport=8080\n")
    data["task"]["unknown"] = True  # type: ignore[index]

    result = run_external_fixture(tmp_path, data)

    assert result.returncode != 0
    assert "validation" in result.stderr.lower()


def test_workspace_command_returns_nonzero_for_path_escape(tmp_path: Path) -> None:
    data = external_fixture(
        setup_path="../outside.txt",
        expected="mode=production\nport=8080\n",
    )

    result = run_external_fixture(tmp_path, data)

    assert result.returncode != 0
    assert not (tmp_path / "outside.txt").exists()


def test_workspace_command_returns_nonzero_when_verifier_fails(tmp_path: Path) -> None:
    data = external_fixture(expected="impossible expected state\n")

    result = run_external_fixture(tmp_path, data)

    assert result.returncode != 0
    stored = json.loads(next((tmp_path / "runs").glob("*/result.json")).read_text(encoding="utf-8"))
    assert stored["outcome"] == "failed"
