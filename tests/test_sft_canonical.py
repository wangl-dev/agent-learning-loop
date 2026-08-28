from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.sft_exporter import export_sft_candidates
from agent_learning_loop.sft_schemas import SftCandidateManifest, SftQualityReport
from agent_learning_loop.sft_validator import validate_sft_candidates

SOURCE_COMMIT = "8a4016a9c154238cd7e5df5d1a3ed8fd194dd10d"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPOSITORY_ROOT / "datasets" / "sft-development-v1"
CANDIDATE_ROOT = DATASET_ROOT / "candidate"
EXPECTED_ARTIFACTS = {
    "dataset-manifest.json": (
        3_400,
        "6581d2be581df520a52be5a8436cde498485cb6ec92470d0602bc5aec7be7cd0",
    ),
    "quality-report.json": (
        1_198,
        "0d1436d3d7fd7252b4ee97c7029313feae0efc04bd07317f3aba9011211fe6d8",
    ),
    "report.md": (
        1_353,
        "fa71e6c4cad00def61b03b6ea3b03a9946bbc3201c18eace875b6c0c1646c4ef",
    ),
    "samples.jsonl": (
        55_016,
        "7ad352bd3e78b6347b1a2e61ce81fdfefa5fb859ab6f8f7368f82cb0df2f5d82",
    ),
}
EXPECTED_REVIEW_IDS = (
    "sft.dataops.correct-order-status.v1",
    "sft.dataops.sync-daily-summary.v1",
    "sft.incident.acknowledge-auto-recovered-search.v1",
    "sft.incident.rollback-checkout-canary.v1",
    "sft.workspace.build-deploy-manifest.v1",
    "sft.workspace.repair-service-map.v1",
)


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_samples() -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in (CANDIDATE_ROOT / "samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


@pytest.fixture(scope="module")
def fresh_source_and_candidate(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("m7b-canonical")
    source = root / "source-eval"
    candidate = root / "candidate"
    assert run_eval("system-correctness", SOURCE_COMMIT, source).exit_code == 0
    export_sft_candidates(source, candidate)
    return source, candidate


def test_canonical_candidate_is_exact_and_source_bound(
    fresh_source_and_candidate: tuple[Path, Path],
) -> None:
    source, fresh = fresh_source_and_candidate
    committed_before = directory_bytes(CANDIDATE_ROOT)
    source_before = directory_bytes(source)

    assert set(committed_before) == set(EXPECTED_ARTIFACTS)
    assert sum(len(payload) for payload in committed_before.values()) == 60_967
    assert all(b"\r" not in payload for payload in committed_before.values())
    for path, (length, sha256) in EXPECTED_ARTIFACTS.items():
        payload = committed_before[path]
        assert len(payload) == length
        assert hashlib.sha256(payload).hexdigest() == sha256

    manifest = SftCandidateManifest.model_validate_json(
        committed_before["dataset-manifest.json"]
    )
    quality = SftQualityReport.model_validate_json(
        committed_before["quality-report.json"]
    )
    assert manifest.source_commit == SOURCE_COMMIT
    assert manifest.source_eval_bundle_fingerprint == (
        "84936d6aff0e5932791bf4a976448e65ec845c787a85c4c73d0b79651830fe9c"
    )
    assert manifest.source_eval_manifest_sha256 == (
        "98c6b2c4590013b9cccf6e7314b27889bc72fac1f122080992244ad643128db3"
    )
    assert manifest.bundle_fingerprint == (
        "0ef96672a2137a38a564640033ce90361eddf935a374e2d22197cb6b5180e06f"
    )
    assert manifest.sample_count == 18
    assert manifest.environment_counts == {
        "workspace": 6,
        "incident": 6,
        "dataops": 6,
    }
    assert manifest.held_out_excluded == {"validation": 6, "test": 6}
    assert quality.model_generated_samples == 0
    assert quality.preference_pairs == 0

    result = validate_sft_candidates(CANDIDATE_ROOT, source)
    assert result.valid
    assert result.eligible_samples == 18
    assert (result.workspace_samples, result.incident_samples, result.dataops_samples) == (
        6,
        6,
        6,
    )
    assert result.held_out_excluded == 12
    assert result.files == 4
    assert result.execution_calls == 0
    assert result.source_bytes_unchanged
    assert result.dataset_bytes_unchanged
    assert directory_bytes(fresh) == committed_before
    assert directory_bytes(source) == source_before
    assert directory_bytes(CANDIDATE_ROOT) == committed_before

    all_paths = list(CANDIDATE_ROOT.rglob("*"))
    assert not any(path.is_symlink() for path in all_paths)
    assert not any(path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"} for path in all_paths)
    assert not any("journal" in path.name.casefold() for path in all_paths)


def test_data_card_and_completed_review_match_canonical() -> None:
    data_card = (DATASET_ROOT / "DATA_CARD.md").read_text(encoding="utf-8")
    review = (DATASET_ROOT / "HUMAN_REVIEW.md").read_text(encoding="utf-8")
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    decision = (
        REPOSITORY_ROOT
        / "docs"
        / "decisions"
        / "0014-m7b-canonical-candidate-and-human-gate.md"
    ).read_text(encoding="utf-8")
    manifest = SftCandidateManifest.model_validate_json(
        (CANDIDATE_ROOT / "dataset-manifest.json").read_bytes()
    )
    samples = load_samples()

    for fact in (
        SOURCE_COMMIT,
        manifest.source_eval_bundle_fingerprint,
        manifest.source_eval_manifest_sha256,
        manifest.bundle_fingerprint,
        "60,967",
        "18 train",
        "Workspace 6, Incident 6, DataOps 6",
        "validation 6 and test 6",
        "model-generated samples 0",
        "preference pairs 0",
        "project-authored-synthetic",
        "Apache-2.0",
        "6 of 6 pre-registered samples passed",
        "6 of 18 candidate samples",
        "remaining 12 candidate samples were not individually human-reviewed",
    ):
        assert fact in data_card
    assert "pending human review" not in data_card
    for sample in samples:
        assert sample["sample_id"] in data_card
        assert sample["sample_fingerprint"] in data_card
    for link, target in (
        ("[`candidate/`](candidate/)", CANDIDATE_ROOT),
        ("[HUMAN_REVIEW.md](HUMAN_REVIEW.md)", DATASET_ROOT / "HUMAN_REVIEW.md"),
    ):
        assert link in data_card
        assert target.exists()
    for boundary in (
        "not a model benchmark",
        "training result",
        "training-ready claims",
        "DPO",
        "real customer data",
        "security or compliance",
        "certification",
        "only 18 scripted-oracle demonstrations",
        "does not make the candidate training-ready",
        "does not establish model improvement",
        "does not make the data risk-free",
    ):
        assert boundary in data_card
    assert attributes.count("datasets/sft-development-v1/candidate/** -text") == 1
    assert "datasets/** -text" not in attributes
    generated_report = (CANDIDATE_ROOT / "report.md").read_text(encoding="utf-8")
    assert "not a tracked dataset" in generated_report
    for explanation in (
        "generation-time boundary",
        "M7A exporter",
        "versions the exact same bytes",
        "stage=development_candidate",
        "exporter_commit=null",
        "not training-ready",
    ):
        assert explanation in data_card
    data_card_link = "[data card](datasets/sft-development-v1/DATA_CARD.md)"
    report_link = "[generated candidate report](datasets/sft-development-v1/candidate/report.md)"
    assert readme.index(data_card_link) < readme.index(report_link)
    assert "M7A generation-time boundary" in readme
    assert "versions the same generated bytes" in readme
    assert "not training-ready" in readme
    assert "guided human review is complete" in readme
    assert "6/6 pre-registered samples passed" in readme
    assert "6/18 candidate samples" in readme
    assert "remaining 12 were not individually reviewed" in readme
    assert "pending human review" not in readme
    for explanation in (
        "not a tracked dataset",
        "generation-time boundary",
        "versions the exact generated bytes",
        "stage=development_candidate",
        "exporter_commit=null",
        "not training-ready",
    ):
        assert explanation in decision

    assert review.count("Status: completed") == 1
    assert review.count("Review date: 2026-08-28") == 1
    assert review.count(
        "Method: The user completed a guided review in the commander conversation using each "
        "sample's complete task, constraints, public scope, and ordered turns summary."
    ) == 1
    assert "pending human review" not in review
    assert "[x]" not in review.casefold()
    assert "[✓]" not in review
    assert "AI approved" not in review
    assert "human approved" not in review
    assert "Reviewer:" not in review
    assert "Signature:" not in review

    by_environment: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        by_environment.setdefault(cast(str, sample["environment"]), []).append(sample)
    selected: list[dict[str, Any]] = []
    for environment in sorted(by_environment):
        ordered = sorted(
            by_environment[environment],
            key=lambda item: (
                cast(str, item["sample_id"]).casefold(),
                cast(str, item["sample_id"]),
            ),
        )
        selected.extend((ordered[0], ordered[-1]))
    assert tuple(item["sample_id"] for item in selected) == EXPECTED_REVIEW_IDS

    blocks = re.findall(r"```json\n(.*?)\n```", review, flags=re.DOTALL)
    projections = [cast(dict[str, Any], json.loads(block)) for block in blocks]
    assert len(projections) == 6
    expected_projections = [
        {
            "sample_id": sample["sample_id"],
            "sample_fingerprint": sample["sample_fingerprint"],
            "source_cell_id": sample["source_cell_id"],
            "instruction": sample["task"]["instruction"],
            "allowed_tools": sample["task"]["allowed_tools"],
            "constraints": sample["task"]["constraints"],
            "public_scope": sample["task"]["public_scope"],
            "turns": sample["turns"],
        }
        for sample in selected
    ]
    assert projections == expected_projections
    assert review.count("Review result: passed") == 6
    assert "## Issues\n\nnone reported" in review
    assert "## Final conclusion\n\n6/6 passed" in review
    for boundary in (
        "only the six pre-registered samples",
        "other 12 candidate samples were individually human-reviewed",
        "18-sample candidate is training-ready",
        "model improvement",
        "risk-free",
    ):
        assert boundary in review
