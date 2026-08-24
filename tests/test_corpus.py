from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.cli import main
from agent_learning_loop.corpus import (
    CorpusResourceRoots,
    CorpusValidationError,
    WorkspaceCorpusManifest,
    validate_workspace_corpus,
)

EXPECTED_SPLITS = {
    "workspace.build-summary": "train",
    "workspace.merge-changelog": "train",
    "workspace.repair-service-map": "train",
    "workspace.create-owner-record": "train",
    "workspace.build-deploy-manifest": "train",
    "workspace.reconcile-inventory": "train",
    "workspace.update-status": "validation",
    "workspace.normalize-checklist": "validation",
    "workspace.fix-config": "test",
    "workspace.update-route": "test",
}


def valid_manifest_payload() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "manifest_id": "workspace.example.manifest.v1",
        "task_id": "workspace.example",
        "task_version": 1,
        "environment_kind": "workspace",
        "fixture_id": "workspace.example.v1",
        "fixture_fingerprint": "a" * 64,
        "catalog_id": "workspace.example.actions.v1",
        "catalog_fingerprint": "b" * 64,
        "split": "train",
        "seed": 11,
        "budgets": {
            "max_steps": 4,
            "max_tool_calls": 4,
            "timeout_seconds": 30.0,
        },
        "safety_constraints": ["Only mutate declared Workspace paths."],
        "verifier_id": "workspace-state-v1",
        "scenario_family": "workspace-example",
        "tags": ["workspace", "synthetic"],
        "provenance": {
            "source": "project-authored-synthetic",
            "license": "Apache-2.0",
        },
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "2"),
        (("task_version",), 2),
        (("budgets", "max_steps"), 0),
        (("budgets", "max_tool_calls"), -1),
        (("budgets", "timeout_seconds"), 0.0),
        (("safety_constraints",), []),
        (("tags",), []),
        (("provenance", "source"), "downloaded"),
        (("provenance", "license"), "unknown"),
    ],
)
def test_manifest_schema_rejects_invalid_governance_fields(
    path: tuple[str, ...], value: object
) -> None:
    payload = valid_manifest_payload()
    target: dict[str, Any] = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        WorkspaceCorpusManifest.model_validate(payload)


@pytest.mark.parametrize(
    "private_field",
    ["unexpected", "required_files", "expected", "arguments", "file_content"],
)
def test_manifest_schema_rejects_unknown_and_private_fields(private_field: str) -> None:
    payload = valid_manifest_payload()
    payload[private_field] = "must not enter corpus metadata"

    with pytest.raises(ValidationError):
        WorkspaceCorpusManifest.model_validate(payload)


def copy_packaged_corpus(tmp_path: Path) -> CorpusResourceRoots:
    package_root = Path(__file__).parents[1] / "src" / "agent_learning_loop"
    roots = CorpusResourceRoots(
        fixtures=tmp_path / "task_fixtures" / "workspace",
        catalogs=tmp_path / "action_catalogs",
        manifests=tmp_path / "task_manifests" / "workspace",
    )
    shutil.copytree(package_root / "task_fixtures" / "workspace", roots.fixtures)
    shutil.copytree(package_root / "action_catalogs", roots.catalogs)
    shutil.copytree(package_root / "task_manifests" / "workspace", roots.manifests)
    return roots


def read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def manifest_path(roots: CorpusResourceRoots, task_name: str) -> Path:
    return roots.manifests / f"{task_name.removeprefix('workspace.')}.json"


def test_packaged_workspace_corpus_is_exactly_the_fixed_ten_task_split() -> None:
    corpus = validate_workspace_corpus()

    assert [manifest.task_id for manifest in corpus.manifests] == sorted(EXPECTED_SPLITS)
    assert {manifest.task_id: manifest.split for manifest in corpus.manifests} == (
        EXPECTED_SPLITS
    )
    assert corpus.summary.total == 10
    assert corpus.summary.train == 6
    assert corpus.summary.validation == 2
    assert corpus.summary.test == 2
    assert len(corpus.fixtures) == len(corpus.catalogs) == 10


@pytest.mark.parametrize("bad_bytes", [b"{not-json}\n", b"\xff\xfe"])
def test_validator_rejects_bad_json_and_non_utf8(
    tmp_path: Path, bad_bytes: bytes
) -> None:
    roots = copy_packaged_corpus(tmp_path)
    manifest_path(roots, "workspace.build-summary").write_bytes(bad_bytes)

    with pytest.raises(CorpusValidationError, match="invalid_manifest"):
        validate_workspace_corpus(roots)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2"),
        ("unexpected", True),
        ("expected", {"required_files": {"private.txt": "private body"}}),
    ],
)
def test_validator_rejects_bad_or_private_manifest_schema(
    tmp_path: Path, field: str, value: object
) -> None:
    roots = copy_packaged_corpus(tmp_path)
    path = manifest_path(roots, "workspace.build-summary")
    payload = read_json(path)
    payload[field] = value
    write_json(path, payload)

    with pytest.raises(CorpusValidationError, match="invalid_manifest"):
        validate_workspace_corpus(roots)


@pytest.mark.parametrize(
    "identity_field",
    ["task_id", "fixture_id", "catalog_id", "manifest_id"],
)
def test_validator_rejects_duplicate_manifest_identities(
    tmp_path: Path, identity_field: str
) -> None:
    roots = copy_packaged_corpus(tmp_path)
    first = read_json(manifest_path(roots, "workspace.build-summary"))
    second_path = manifest_path(roots, "workspace.merge-changelog")
    second = read_json(second_path)
    second[identity_field] = first[identity_field]
    write_json(second_path, second)

    with pytest.raises(CorpusValidationError, match="duplicate_identity"):
        validate_workspace_corpus(roots)


def test_validator_rejects_split_count_and_fixed_mapping_drift(tmp_path: Path) -> None:
    roots = copy_packaged_corpus(tmp_path)
    path = manifest_path(roots, "workspace.build-summary")
    payload = read_json(path)
    payload["split"] = "validation"
    write_json(path, payload)

    with pytest.raises(CorpusValidationError, match="split_contract"):
        validate_workspace_corpus(roots)


def test_validator_rejects_scenario_family_crossing_splits(tmp_path: Path) -> None:
    roots = copy_packaged_corpus(tmp_path)
    train = read_json(manifest_path(roots, "workspace.build-summary"))
    test_path = manifest_path(roots, "workspace.fix-config")
    test_payload = read_json(test_path)
    test_payload["scenario_family"] = train["scenario_family"]
    write_json(test_path, test_payload)

    with pytest.raises(CorpusValidationError, match="scenario_split"):
        validate_workspace_corpus(roots)


def test_validator_rejects_missing_reference_and_orphan_resource(tmp_path: Path) -> None:
    roots = copy_packaged_corpus(tmp_path)
    path = manifest_path(roots, "workspace.build-summary")
    payload = read_json(path)
    payload["fixture_id"] = "workspace.missing.v1"
    write_json(path, payload)

    with pytest.raises(CorpusValidationError, match="resource_reference"):
        validate_workspace_corpus(roots)


def test_validator_rejects_fixture_task_identity_mismatch(tmp_path: Path) -> None:
    roots = copy_packaged_corpus(tmp_path)
    fixture_path = roots.fixtures / "build-summary.json"
    fixture = read_json(fixture_path)
    fixture["task"]["task_id"] = "workspace.wrong-task"
    write_json(fixture_path, fixture)
    manifest_file = manifest_path(roots, "workspace.build-summary")
    manifest = read_json(manifest_file)
    manifest["fixture_fingerprint"] = canonical_sha256(fixture)
    write_json(manifest_file, manifest)

    with pytest.raises(CorpusValidationError, match="identity_mismatch"):
        validate_workspace_corpus(roots)


def test_validator_rejects_an_extra_orphan_resource(tmp_path: Path) -> None:
    roots = copy_packaged_corpus(tmp_path)
    source = roots.fixtures / "build-summary.json"
    shutil.copy2(source, roots.fixtures / "orphan.json")

    with pytest.raises(CorpusValidationError, match="resource_count"):
        validate_workspace_corpus(roots)


@pytest.mark.parametrize("resource_kind", ["fixture", "catalog"])
def test_validator_recomputes_resource_fingerprints(
    tmp_path: Path, resource_kind: str
) -> None:
    roots = copy_packaged_corpus(tmp_path)
    if resource_kind == "fixture":
        path = roots.fixtures / "build-summary.json"
        payload = read_json(path)
        payload["task"]["instruction"] += " Changed."
    else:
        path = roots.catalogs / "build-summary.json"
        payload = read_json(path)
        payload["actions"][0]["action"]["arguments"]["path"] = "input/other.txt"
    write_json(path, payload)

    with pytest.raises(CorpusValidationError, match="fingerprint_mismatch"):
        validate_workspace_corpus(roots)


def test_validator_rejects_catalog_tool_outside_task_allowlist(tmp_path: Path) -> None:
    roots = copy_packaged_corpus(tmp_path)
    fixture_path = roots.fixtures / "build-summary.json"
    fixture = read_json(fixture_path)
    fixture["task"]["allowed_tools"] = ["write_text"]
    write_json(fixture_path, fixture)
    manifest_file = manifest_path(roots, "workspace.build-summary")
    manifest = read_json(manifest_file)
    manifest["fixture_fingerprint"] = canonical_sha256(fixture)
    write_json(manifest_file, manifest)

    with pytest.raises(CorpusValidationError, match="tool_allowlist"):
        validate_workspace_corpus(roots)


def test_validator_failure_is_read_only_and_has_no_execution_side_effects(
    tmp_path: Path,
) -> None:
    roots = copy_packaged_corpus(tmp_path)
    path = manifest_path(roots, "workspace.build-summary")
    payload = read_json(path)
    payload["fixture_fingerprint"] = "0" * 64
    write_json(path, payload)
    before = {
        item.relative_to(tmp_path): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    with pytest.raises(CorpusValidationError, match="fingerprint_mismatch"):
        validate_workspace_corpus(roots)

    after = {
        item.relative_to(tmp_path): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    assert after == before
    assert not (tmp_path / "workspace").exists()
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "events.jsonl").exists()


def test_validate_corpus_cli_prints_only_the_machine_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["validate-corpus"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "schema_version": "1",
        "environment": "workspace",
        "total": 10,
        "train": 6,
        "validation": 2,
        "test": 2,
    }
    assert "required_files" not in captured.out
    assert "expected" not in captured.out


def test_validate_corpus_cli_returns_stable_exit_two_without_private_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_marker = "private-file-body-must-not-leak"

    def reject() -> None:
        raise CorpusValidationError("fingerprint_mismatch") from ValueError(private_marker)

    monkeypatch.setattr("agent_learning_loop.corpus.validate_workspace_corpus", reject)
    exit_code = main(["validate-corpus"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "validate-corpus validation error: fingerprint_mismatch\n"
    assert private_marker not in captured.err
