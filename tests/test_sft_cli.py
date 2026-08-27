from __future__ import annotations

from pathlib import Path

import pytest

from agent_learning_loop.cli import main
from agent_learning_loop.eval_runner import run_eval

SOURCE_COMMIT = "b" * 40


@pytest.fixture(scope="module")
def system_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sft-cli") / "eval"
    assert run_eval("system-correctness", SOURCE_COMMIT, root).exit_code == 0
    return root


def test_sft_cli_exports_and_validates_with_zero_exit(
    system_source: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "candidate"

    assert (
        main(
            [
                "export-sft-candidates",
                "--eval-bundle",
                str(system_source),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "validate-sft-candidates",
                "--bundle",
                str(output),
                "--eval-bundle",
                str(system_source),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "eligible=18" in captured.out
    assert '"valid":true' in captured.out


def test_sft_cli_maps_content_failure_to_one_and_setup_failure_to_two(
    system_source: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "candidate"
    assert (
        main(
            [
                "export-sft-candidates",
                "--eval-bundle",
                str(system_source),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    (output / "orphan.txt").write_text("orphan\n", encoding="utf-8", newline="\n")
    assert (
        main(
            [
                "validate-sft-candidates",
                "--bundle",
                str(output),
                "--eval-bundle",
                str(system_source),
            ]
        )
        == 1
    )
    missing_output = tmp_path / "missing-source-output"
    assert (
        main(
            [
                "export-sft-candidates",
                "--eval-bundle",
                str(tmp_path / "missing-eval"),
                "--output-dir",
                str(missing_output),
            ]
        )
        == 2
    )
    assert not missing_output.exists()
    incomplete_source = tmp_path / "train-only-eval"
    incomplete_output = tmp_path / "train-only-output"
    assert (
        run_eval(
            "system-correctness",
            SOURCE_COMMIT,
            incomplete_source,
            split="train",
        ).exit_code
        == 0
    )
    assert (
        main(
            [
                "export-sft-candidates",
                "--eval-bundle",
                str(incomplete_source),
                "--output-dir",
                str(incomplete_output),
            ]
        )
        == 1
    )
    assert not incomplete_output.exists()
    assert (
        main(
            [
                "validate-sft-candidates",
                "--bundle",
                str(tmp_path / "missing-candidate"),
                "--eval-bundle",
                str(system_source),
            ]
        )
        == 2
    )


@pytest.mark.parametrize("failure", ["export", "validate"])
def test_sft_cli_maps_infrastructure_failure_to_two_without_traceback(
    system_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    output = tmp_path / "candidate"
    if failure == "export":
        monkeypatch.setattr(
            "agent_learning_loop.sft_exporter.export_sft_candidates",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("controlled infra")),
        )
        exit_code = main(
            [
                "export-sft-candidates",
                "--eval-bundle",
                str(system_source),
                "--output-dir",
                str(output),
            ]
        )
    else:
        output.mkdir()
        monkeypatch.setattr(
            "agent_learning_loop.sft_validator.validate_sft_candidates",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("controlled infra")),
        )
        exit_code = main(
            [
                "validate-sft-candidates",
                "--bundle",
                str(output),
                "--eval-bundle",
                str(system_source),
            ]
        )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "controlled infra" in captured.err
    assert "Traceback" not in captured.err


def test_sft_cli_does_not_swallow_keyboard_interrupt(
    system_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "agent_learning_loop.sft_exporter.export_sft_candidates", interrupted
    )
    with pytest.raises(KeyboardInterrupt):
        main(
            [
                "export-sft-candidates",
                "--eval-bundle",
                str(system_source),
                "--output-dir",
                str(tmp_path / "candidate"),
            ]
        )
