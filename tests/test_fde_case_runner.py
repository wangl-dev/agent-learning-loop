from __future__ import annotations

from pathlib import Path

import pytest

from agent_learning_loop.fde_case_runner import FdeCaseRunError, run_fde_case


def test_runner_calls_only_the_fixed_incident_eval_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fail_after_capture(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise RuntimeError("injected infrastructure failure")

    monkeypatch.setattr("agent_learning_loop.fde_case_runner.run_eval", fail_after_capture)
    output = tmp_path / "pilot"

    with pytest.raises(FdeCaseRunError, match="fde_case_execution_failed"):
        run_fde_case(
            "incident-copilot-v1",
            "d030e3219991d3fa52a5a3eca86c31239659745a",
            output,
        )

    assert calls == [
        (
            (
                "system-correctness",
                "d030e3219991d3fa52a5a3eca86c31239659745a",
                output / "evidence",
            ),
            {"environment": "incident"},
        )
    ]
    assert not output.exists()


def test_unknown_case_and_existing_output_fail_before_eval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def unexpected(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr("agent_learning_loop.fde_case_runner.run_eval", unexpected)
    with pytest.raises(FdeCaseRunError, match="unknown_fde_case"):
        run_fde_case("unknown", "d030e3219991d3fa52a5a3eca86c31239659745a", tmp_path / "x")

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FdeCaseRunError, match="output_directory_must_not_exist"):
        run_fde_case(
            "incident-copilot-v1",
            "d030e3219991d3fa52a5a3eca86c31239659745a",
            existing,
        )
    assert calls == 0
