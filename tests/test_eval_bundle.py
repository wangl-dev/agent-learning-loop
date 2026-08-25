from __future__ import annotations

from pathlib import Path

from agent_learning_loop.eval_bundle import render_eval_report
from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_schemas import (
    EvalArtifact,
    EvalBundleManifest,
    EvalSelectionSpec,
    EvalSummary,
    ExactRatio,
    OracleFailureSummary,
)


def test_markdown_renderer_is_deterministic_and_uses_honest_labels(
) -> None:
    summary = EvalSummary(
        suite_ids=["runtime-reliability-v1"],
        selected_total=4,
        verifier_state_success=ExactRatio(numerator=3, denominator=4, rate=0.75),
        runtime_completion_success=ExactRatio(numerator=2, denominator=4, rate=0.5),
        duplicate_side_effects=ExactRatio(numerator=1, denominator=4, rate=0.25),
        physical_executions=ExactRatio(numerator=5, denominator=4, rate=1.25),
        physical_writes=ExactRatio(numerator=3, denominator=4, rate=0.75),
        retries=ExactRatio(numerator=1, denominator=4, rate=0.25),
        idempotency_hits=ExactRatio(numerator=1, denominator=4, rate=0.25),
        system_slices=[],
        reliability_cells=[],
        pair_deltas=[],
        diagnostics=[],
        oracle_failure_cell_ids=["one.failed"],
        oracle_failures=[
            OracleFailureSummary(
                cell_id="one.failed",
                error_category="expected_failure",
                raw_result_path="runs/one/result.json",
            )
        ],
    )
    manifest = EvalBundleManifest(
        source_commit="6" * 40,
        selection=EvalSelectionSpec(
            suite="runtime-reliability",
            candidate_total=7,
            selected_total=4,
            cell_ids=["one", "two", "three", "four"],
        ),
        suite_fingerprints={"runtime-reliability-v1": "a" * 64},
        artifacts=[EvalArtifact(path="runs/one/result.json", sha256="b" * 64)],
        bundle_fingerprint="c" * 64,
    )
    first = render_eval_report(manifest, summary)
    second = render_eval_report(manifest, summary)

    assert first == second
    assert "verifier state success" in first
    assert "Runtime completion" in first
    assert "model: N/A" in first
    assert "token cost: N/A" in first
    assert "observed/non-comparable" in first
    assert "Agent success rate" not in first


def test_two_reliability_runs_have_identical_deterministic_bundle_bytes(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    assert run_eval("runtime-reliability", "8" * 40, first).exit_code == 0
    assert run_eval("runtime-reliability", "8" * 40, second).exit_code == 0

    def files(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    assert files(first) == files(second)
