from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.corpus import validate_workspace_corpus
from agent_learning_loop.dataops_corpus import validate_dataops_corpus
from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.incident_corpus import validate_incident_corpus
from agent_learning_loop.sft_exporter import export_sft_candidates
from agent_learning_loop.sft_normalizers import (
    SftNormalizationError,
    normalize_dataops_trajectory,
    normalize_incident_trajectory,
    normalize_workspace_trajectory,
)
from agent_learning_loop.sft_schemas import SftCandidateManifest, SftSample

SOURCE_COMMIT = "9" * 40


@pytest.fixture(scope="module")
def exported_candidate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("sft-contracts")
    source = root / "eval"
    candidate = root / "candidate"
    assert run_eval("system-correctness", SOURCE_COMMIT, source).exit_code == 0
    export_sft_candidates(source, candidate)
    return source, candidate


def load_samples(candidate: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (candidate / "samples.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def resign_sample(payload: dict[str, object]) -> dict[str, object]:
    unsigned = {key: value for key, value in payload.items() if key != "sample_fingerprint"}
    return {**unsigned, "sample_fingerprint": canonical_sha256(unsigned)}


def test_sample_and_manifest_are_strict_and_self_consistent(
    exported_candidate: tuple[Path, Path],
) -> None:
    _, candidate = exported_candidate
    manifest_payload = json.loads(
        (candidate / "dataset-manifest.json").read_text(encoding="utf-8")
    )
    sample_payload = load_samples(candidate)[0]

    assert SftCandidateManifest.model_validate(manifest_payload).sample_count == 18
    assert SftSample.model_validate(sample_payload).split == "train"

    with pytest.raises(ValidationError):
        SftSample.model_validate({**sample_payload, "unknown": True})
    changed_split = dict(sample_payload)
    changed_split["split"] = "validation"
    with pytest.raises(ValidationError):
        SftSample.model_validate(resign_sample(changed_split))
    changed_fingerprint = dict(sample_payload)
    changed_fingerprint["sample_fingerprint"] = "0" * 64
    with pytest.raises(ValidationError):
        SftSample.model_validate(changed_fingerprint)
    changed_run_id = json.loads(json.dumps(sample_payload))
    changed_run_id["turns"][1]["payload"]["run_id"] = "forged-run"
    with pytest.raises(ValidationError):
        SftSample.model_validate(resign_sample(changed_run_id))
    changed_counts = dict(manifest_payload)
    changed_counts["environment_counts"] = {
        "workspace": 7,
        "incident": 5,
        "dataops": 6,
    }
    changed_counts["bundle_fingerprint"] = canonical_sha256(
        {
            key: value
            for key, value in changed_counts.items()
            if key != "bundle_fingerprint"
        }
    )
    with pytest.raises(ValidationError):
        SftCandidateManifest.model_validate(changed_counts)


@pytest.mark.parametrize(
    ("location", "embedded_path"),
    [
        ("instruction", r"Open C:\Users\wangl\private.txt and continue."),
        ("instruction", "Open /home/wangl/private.txt and continue."),
        ("action", r"Load C:\Users\wangl\private.txt before continuing."),
        ("action", r"Load \\fileserver\private\input.txt before continuing."),
        ("result", "Loaded /home/wangl/private.txt before continuing."),
    ],
)
def test_sample_rejects_embedded_machine_paths_after_resigning(
    exported_candidate: tuple[Path, Path],
    location: str,
    embedded_path: str,
) -> None:
    _, candidate = exported_candidate
    payload = json.loads(json.dumps(load_samples(candidate)[0]))
    if location == "instruction":
        payload["task"]["instruction"] = embedded_path
    elif location == "action":
        payload["turns"][0]["arguments"]["path_probe"] = embedded_path
    else:
        payload["turns"][1]["payload"]["path_probe"] = embedded_path

    with pytest.raises(ValidationError):
        SftSample.model_validate(resign_sample(payload))


def test_sample_accepts_embedded_project_relative_paths(
    exported_candidate: tuple[Path, Path],
) -> None:
    _, candidate = exported_candidate
    payload = json.loads(json.dumps(load_samples(candidate)[0]))
    payload["task"]["instruction"] = (
        "Open input/title.txt and save output/summary.txt."
    )
    payload["turns"][0]["arguments"]["path_probe"] = "input/title.txt"
    payload["turns"][1]["payload"]["path_probe"] = "output/summary.txt"

    validated = SftSample.model_validate(resign_sample(payload))

    assert validated.task.instruction.endswith("output/summary.txt.")


def test_honest_normalizers_preserve_exact_public_action_result_pairs(
    exported_candidate: tuple[Path, Path],
) -> None:
    source, _ = exported_candidate
    workspace = validate_workspace_corpus()
    incident = validate_incident_corpus()
    dataops = validate_dataops_corpus()

    workspace_task_id = "workspace.build-summary"
    workspace_fixture = next(
        item for item in workspace.fixtures if item.task.task_id == workspace_task_id
    )
    workspace_catalog = next(
        item for item in workspace.catalogs if item.task_id == workspace_task_id
    )
    workspace_turns = normalize_workspace_trajectory(
        source
        / "runs/system-correctness-v1/system.workspace.build-summary/events.jsonl",
        workspace_fixture.task,
        workspace_catalog,
    )

    incident_task_id = "incident.enable-catalog-cache-fallback"
    incident_fixture = next(
        item for item in incident.fixtures if item.task.task_id == incident_task_id
    )
    incident_catalog = next(
        item for item in incident.catalogs if item.task_id == incident_task_id
    )
    incident_turns = normalize_incident_trajectory(
        source
        / (
            "runs/system-correctness-v1/"
            "system.incident.enable-catalog-cache-fallback/events.jsonl"
        ),
        incident_fixture.task,
        incident_catalog,
    )

    dataops_task_id = "dataops.correct-order-status"
    dataops_fixture = next(
        item for item in dataops.fixtures if item.task.task_id == dataops_task_id
    )
    dataops_catalog = next(
        item for item in dataops.catalogs if item.task_id == dataops_task_id
    )
    dataops_turns = normalize_dataops_trajectory(
        source
        / "runs/system-correctness-v1/system.dataops.correct-order-status/events.jsonl",
        dataops_fixture.task,
        dataops_catalog,
    )

    assert len(workspace_turns) == len(workspace_catalog.actions) * 2
    assert len(incident_turns) == len(incident_catalog.actions) * 2
    assert len(dataops_turns) == len(dataops_catalog.actions) * 2
    approval_result = incident_turns[1]
    approved_action = incident_turns[2]
    assert approval_result.role == "tool_result"
    assert approved_action.role == "assistant_action"
    assert approval_result.payload["approval_id"] == approved_action.arguments["approval_id"]


@pytest.mark.parametrize("environment", ["workspace", "incident", "dataops"])
def test_normalizers_reject_reordered_or_unknown_raw_fields(
    exported_candidate: tuple[Path, Path],
    tmp_path: Path,
    environment: str,
) -> None:
    source, _ = exported_candidate
    if environment == "workspace":
        workspace_corpus = validate_workspace_corpus()
        task_id = "workspace.build-summary"
        workspace_fixture = next(
            item for item in workspace_corpus.fixtures if item.task.task_id == task_id
        )
        workspace_catalog = next(
            item for item in workspace_corpus.catalogs if item.task_id == task_id
        )
        original = source / (
            "runs/system-correctness-v1/system.workspace.build-summary/events.jsonl"
        )
        payloads = [json.loads(line) for line in original.read_text(encoding="utf-8").splitlines()]
        payloads[1], payloads[2] = payloads[2], payloads[1]
        target = tmp_path / "workspace-events.jsonl"
        target.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in payloads),
            encoding="utf-8",
            newline="\n",
        )
        with pytest.raises(SftNormalizationError):
            normalize_workspace_trajectory(
                target, workspace_fixture.task, workspace_catalog
            )
        return
    elif environment == "incident":
        incident_corpus = validate_incident_corpus()
        task_id = "incident.acknowledge-auto-recovered-search"
        incident_fixture = next(
            item for item in incident_corpus.fixtures if item.task.task_id == task_id
        )
        incident_catalog = next(
            item for item in incident_corpus.catalogs if item.task_id == task_id
        )
        original = source / (
            "runs/system-correctness-v1/"
            "system.incident.acknowledge-auto-recovered-search/events.jsonl"
        )
        payloads = [json.loads(line) for line in original.read_text(encoding="utf-8").splitlines()]
        payloads[1]["payload"]["unknown"] = True
        target = tmp_path / "incident-events.jsonl"
        target.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in payloads),
            encoding="utf-8",
            newline="\n",
        )
        with pytest.raises(SftNormalizationError):
            normalize_incident_trajectory(
                target, incident_fixture.task, incident_catalog
            )
        return
    else:
        dataops_corpus = validate_dataops_corpus()
        task_id = "dataops.correct-order-status"
        dataops_fixture = next(
            item for item in dataops_corpus.fixtures if item.task.task_id == task_id
        )
        dataops_catalog = next(
            item for item in dataops_corpus.catalogs if item.task_id == task_id
        )
        original = source / (
            "runs/system-correctness-v1/system.dataops.correct-order-status/events.jsonl"
        )
        payloads = [json.loads(line) for line in original.read_text(encoding="utf-8").splitlines()]
        payloads[0]["arguments"]["unknown"] = True
        target = tmp_path / "dataops-events.jsonl"
        target.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in payloads),
            encoding="utf-8",
            newline="\n",
        )
        with pytest.raises(SftNormalizationError):
            normalize_dataops_trajectory(
                target, dataops_fixture.task, dataops_catalog
            )
