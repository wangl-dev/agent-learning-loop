from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_schemas import SystemEvalCell
from agent_learning_loop.eval_suites import load_eval_suites
from agent_learning_loop.sft_exporter import export_sft_candidates
from agent_learning_loop.sft_schemas import SftCandidateManifest, SftSample

SOURCE_COMMIT = "8" * 40


@pytest.fixture(scope="module")
def system_eval_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sft-source") / "eval"
    assert run_eval("system-correctness", SOURCE_COMMIT, root).exit_code == 0
    return root


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_is_train_only_deterministic_and_field_minimized(
    system_eval_bundle: Path,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    export_sft_candidates(system_eval_bundle, first)
    export_sft_candidates(system_eval_bundle, second)

    assert directory_bytes(first) == directory_bytes(second)
    assert set(directory_bytes(first)) == {
        "dataset-manifest.json",
        "samples.jsonl",
        "quality-report.json",
        "report.md",
    }
    manifest = SftCandidateManifest.model_validate_json(
        (first / "dataset-manifest.json").read_text(encoding="utf-8")
    )
    samples = [
        SftSample.model_validate_json(line)
        for line in (first / "samples.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert manifest.stage == "development_candidate"
    assert manifest.sample_count == 18
    assert manifest.held_out_excluded == {"validation": 6, "test": 6}
    assert Counter(sample.environment for sample in samples) == {
        "workspace": 6,
        "incident": 6,
        "dataops": 6,
    }
    assert {sample.split for sample in samples} == {"train"}
    assert all(sample.generation_mode == "scripted_oracle" for sample in samples)
    assert all(
        [artifact.role for artifact in sample.source_artifacts] == ["events", "result"]
        and len(sample.turns) % 2 == 0
        for sample in samples
    )

    serialized = (first / "samples.jsonl").read_text(encoding="utf-8")
    for forbidden in (
        '"private"',
        '"expected"',
        '"verifier"',
        '"audit"',
        '"run_id"',
        '"action_ref"',
        '"before_digest"',
        '"after_digest"',
    ):
        assert forbidden not in serialized
    assert "validation" not in serialized
    assert "test" not in serialized
    held_out_ids = {
        cell.task_id
        for cell in load_eval_suites()["system-correctness-v1"].cells
        if isinstance(cell, SystemEvalCell) and cell.split != "train"
    }
    assert len(held_out_ids) == 12
    assert all(task_id not in serialized for task_id in held_out_ids)
    quality = json.loads(
        (first / "quality-report.json").read_text(encoding="utf-8")
    )
    assert quality["model_generated_samples"] == 0
    assert quality["preference_pairs"] == 0
    assert quality["execution_calls"] == 0
