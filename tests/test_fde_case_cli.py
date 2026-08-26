from __future__ import annotations

from pathlib import Path

import pytest

from agent_learning_loop.cli import build_parser, main


def test_cli_exposes_only_fixed_fde_case_inputs() -> None:
    parser = build_parser()
    run = parser.parse_args(
        [
            "run-fde-case",
            "--case",
            "incident-copilot-v1",
            "--source-commit",
            "d030e3219991d3fa52a5a3eca86c31239659745a",
            "--output-dir",
            "pilot",
        ]
    )
    validate = parser.parse_args(["validate-fde-case", "--run-dir", "pilot"])

    assert run.command == "run-fde-case"
    assert vars(run) == {
        "command": "run-fde-case",
        "case": "incident-copilot-v1",
        "source_commit": "d030e3219991d3fa52a5a3eca86c31239659745a",
        "output_dir": run.output_dir,
    }
    assert validate.command == "validate-fde-case"
    assert vars(validate) == {
        "command": "validate-fde-case",
        "run_dir": validate.run_dir,
    }


def test_cli_returns_exit_2_for_invalid_input_and_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invalid = tmp_path / "invalid"
    assert (
        main(
            [
                "run-fde-case",
                "--case",
                "incident-copilot-v1",
                "--source-commit",
                "not-a-commit",
                "--output-dir",
                str(invalid),
            ]
        )
        == 2
    )
    assert not invalid.exists()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected infrastructure failure")

    monkeypatch.setattr("agent_learning_loop.fde_case_runner.run_eval", fail)
    infrastructure = tmp_path / "infrastructure"
    assert (
        main(
            [
                "run-fde-case",
                "--case",
                "incident-copilot-v1",
                "--source-commit",
                "d030e3219991d3fa52a5a3eca86c31239659745a",
                "--output-dir",
                str(infrastructure),
            ]
        )
        == 2
    )
    assert not infrastructure.exists()
    assert main(["validate-fde-case", "--run-dir", str(tmp_path / "missing")]) == 2
