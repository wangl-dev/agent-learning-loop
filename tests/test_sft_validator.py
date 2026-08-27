from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agent_learning_loop.canonical import canonical_json_bytes, canonical_sha256
from agent_learning_loop.eval_bundle import canonical_json_text, compute_bundle_fingerprint
from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_schemas import EvalBundleManifest
from agent_learning_loop.sft_exporter import SftExportError, export_sft_candidates
from agent_learning_loop.sft_validator import (
    SftCandidateValidationError,
    validate_sft_candidates,
)

SOURCE_COMMIT = "a" * 40


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def source_and_candidate(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("sft-validation")
    source = root / "eval"
    candidate = root / "candidate"
    assert run_eval("system-correctness", SOURCE_COMMIT, source).exit_code == 0
    export_sft_candidates(source, candidate)
    return source, candidate


def resign_candidate(root: Path, mutation: str) -> None:
    samples_path = root / "samples.jsonl"
    samples = [json.loads(line) for line in samples_path.read_text(encoding="utf-8").splitlines()]
    sample = samples[0]
    if mutation == "instruction":
        sample["task"]["instruction"] = "forged public instruction"
    elif mutation == "action_argument":
        sample["turns"][0]["arguments"]["transaction_id"] = "tx-forged"
    elif mutation == "result_payload":
        sample["turns"][1]["payload"]["transaction_state"] = "forged"
    elif mutation == "result_status":
        sample["turns"][1]["status"] = "error"
        sample["turns"][1]["error_category"] = "forged_error"
    elif mutation == "resource_identity":
        sample["resource"]["fixture_fingerprint"] = "0" * 64
    elif mutation == "provenance":
        sample["provenance"]["source"] = "forged-source"
    elif mutation == "run_id":
        sample["turns"][1]["payload"]["run_id"] = "forged-run"
    else:
        raise AssertionError(mutation)
    unsigned = {key: value for key, value in sample.items() if key != "sample_fingerprint"}
    sample["sample_fingerprint"] = canonical_sha256(unsigned)
    samples_path.write_bytes(
        b"".join(canonical_json_bytes(item) + b"\n" for item in samples)
    )

    manifest_path = root / "dataset-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample_fingerprints"] = [item["sample_fingerprint"] for item in samples]
    for artifact in manifest["artifacts"]:
        artifact["sha256"] = hashlib.sha256((root / artifact["path"]).read_bytes()).hexdigest()
    unsigned_manifest = {
        key: value for key, value in manifest.items() if key != "bundle_fingerprint"
    }
    manifest["bundle_fingerprint"] = canonical_sha256(unsigned_manifest)
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")


@pytest.mark.parametrize(
    "mutation",
    [
        "instruction",
        "action_argument",
        "result_payload",
        "result_status",
        "resource_identity",
        "provenance",
        "run_id",
    ],
)
def test_validator_rejects_joint_resigned_dataset_tamper(
    source_and_candidate: tuple[Path, Path],
    tmp_path: Path,
    mutation: str,
) -> None:
    source, candidate = source_and_candidate
    changed = tmp_path / mutation
    shutil.copytree(candidate, changed)
    resign_candidate(changed, mutation)

    with pytest.raises(SftCandidateValidationError):
        validate_sft_candidates(changed, source)


def test_validator_rejects_orphan_and_joint_rehashed_source_raw(
    source_and_candidate: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source, candidate = source_and_candidate
    orphan = tmp_path / "orphan"
    shutil.copytree(candidate, orphan)
    (orphan / "unregistered.txt").write_text("orphan\n", encoding="utf-8", newline="\n")
    with pytest.raises(SftCandidateValidationError):
        validate_sft_candidates(orphan, source)

    changed_source = tmp_path / "source"
    shutil.copytree(source, changed_source)
    event_path = changed_source / (
        "runs/system-correctness-v1/"
        "system.incident.acknowledge-auto-recovered-search/events.jsonl"
    )
    payloads = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    payloads[1]["payload"]["state"] = "stuck"
    event_path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in payloads),
        encoding="utf-8",
        newline="\n",
    )
    eval_manifest_path = changed_source / "eval-manifest.json"
    eval_manifest = EvalBundleManifest.model_validate_json(
        eval_manifest_path.read_text(encoding="utf-8")
    )
    changed_artifacts = [
        item.model_copy(
            update={
                "sha256": hashlib.sha256(
                    (changed_source / item.path).read_bytes()
                ).hexdigest()
            }
        )
        for item in eval_manifest.artifacts
    ]
    draft = eval_manifest.model_copy(
        update={"artifacts": changed_artifacts, "bundle_fingerprint": "0" * 64}
    )
    resigned = draft.model_copy(
        update={"bundle_fingerprint": compute_bundle_fingerprint(draft)}
    )
    eval_manifest_path.write_text(
        canonical_json_text(resigned), encoding="utf-8", newline="\n"
    )
    with pytest.raises(SftCandidateValidationError):
        validate_sft_candidates(candidate, changed_source)


def test_export_rejects_incomplete_system_selection(tmp_path: Path) -> None:
    source = tmp_path / "train-only-eval"
    output = tmp_path / "candidate"
    assert (
        run_eval(
            "system-correctness",
            SOURCE_COMMIT,
            source,
            split="train",
        ).exit_code
        == 0
    )

    with pytest.raises(SftExportError):
        export_sft_candidates(source, output)
    assert not output.exists()


def test_all_suite_source_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "all-eval"
    output = tmp_path / "candidate"
    assert run_eval("all", SOURCE_COMMIT, source).exit_code == 0

    outcome = export_sft_candidates(source, output)

    assert outcome.manifest.source_eval_selected_cells == 41
    assert validate_sft_candidates(output, source).eligible_samples == 18


def test_export_and_validation_execute_nothing_and_preserve_both_inputs(
    source_and_candidate: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, candidate = source_and_candidate
    output = tmp_path / "second-candidate"
    source_before = directory_bytes(source)
    candidate_before = directory_bytes(candidate)
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("execution boundary crossed")

    monkeypatch.setattr("agent_learning_loop.eval_runner.run_eval", forbidden)
    monkeypatch.setattr("agent_learning_loop.vertical_slice.execute_task", forbidden)
    monkeypatch.setattr("agent_learning_loop.incident_runner.run_incident_task", forbidden)
    monkeypatch.setattr("agent_learning_loop.dataops_runner.run_dataops_task", forbidden)
    monkeypatch.setattr("agent_learning_loop.workspace.WorkspaceEnvironment.__init__", forbidden)
    monkeypatch.setattr(
        "agent_learning_loop.incident_environment.IncidentEnvironment.__init__",
        forbidden,
    )
    monkeypatch.setattr(
        "agent_learning_loop.dataops_environment.DataOpsEnvironment.__init__",
        forbidden,
    )
    monkeypatch.setattr("agent_learning_loop.policy.ScriptedPolicy.__init__", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)

    export_sft_candidates(source, output)
    result = validate_sft_candidates(candidate, source)

    assert result.execution_calls == 0
    assert calls == 0
    assert source_before == directory_bytes(source)
    assert candidate_before == directory_bytes(candidate)
