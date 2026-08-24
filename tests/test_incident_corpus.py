from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.cli import main
from agent_learning_loop.corpus import CorpusValidationError
from agent_learning_loop.incident_corpus import (
    FIXED_INCIDENT_SPLITS,
    IncidentResourceRoots,
    validate_incident_corpus,
)
from agent_learning_loop.incident_runner import run_all_incident_tasks


def copy_incident_corpus(tmp_path: Path) -> IncidentResourceRoots:
    package = Path(__file__).parents[1] / "src" / "agent_learning_loop"
    roots = IncidentResourceRoots(
        fixtures=tmp_path / "fixtures",
        catalogs=tmp_path / "catalogs",
        manifests=tmp_path / "manifests",
    )
    shutil.copytree(package / "task_fixtures" / "incident", roots.fixtures)
    shutil.copytree(package / "action_catalogs" / "incident", roots.catalogs)
    shutil.copytree(package / "task_manifests" / "incident", roots.manifests)
    return roots


def read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def test_packaged_incident_corpus_has_the_fixed_ten_task_split() -> None:
    corpus = validate_incident_corpus()

    assert [item.task.task_id for item in corpus.fixtures] == sorted(FIXED_INCIDENT_SPLITS)
    assert {item.task_id: item.split for item in corpus.manifests} == FIXED_INCIDENT_SPLITS
    assert corpus.summary.model_dump() == {
        "schema_version": "1",
        "environment": "incident",
        "total": 10,
        "train": 6,
        "validation": 2,
        "test": 2,
    }


def test_scripted_incident_runner_writes_auditable_outputs(tmp_path: Path) -> None:
    results = run_all_incident_tasks(tmp_path / "incident-runs")

    assert len(results) == 10
    assert all(result.outcome == "passed" for result in results)
    duplicate = tmp_path / "incident-runs" / "incident_dedupe-notification-restart"
    audit = [json.loads(line) for line in (duplicate / "audit.jsonl").read_text().splitlines()]
    assert [item["sequence"] for item in audit] == list(range(len(audit)))
    assert sum(item["operation_id"] == "notifier-restart-1" for item in audit) == 2
    public_artifacts = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "incident-runs").rglob("*.json*")
    )
    assert '"expected"' not in public_artifacts
    assert '"max_restart_counts"' not in public_artifacts
    assert '"exact_restart_counts"' not in public_artifacts
    assert '"exact_feature_flag_mutations"' not in public_artifacts


def test_incident_corpus_cli_is_separate_from_workspace_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["validate-corpus", "--environment", "incident"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "1",
        "environment": "incident",
        "total": 10,
        "train": 6,
        "validation": 2,
        "test": 2,
    }


@pytest.mark.parametrize("resource", ["fixtures", "catalogs", "manifests"])
def test_incident_corpus_rejects_missing_resources(tmp_path: Path, resource: str) -> None:
    roots = copy_incident_corpus(tmp_path)
    selected = cast(Path, getattr(roots, resource))
    first = sorted(selected.glob("*.json"))[0]
    first.unlink()

    with pytest.raises(CorpusValidationError, match="resource_count"):
        validate_incident_corpus(roots)


@pytest.mark.parametrize("resource", ["fixtures", "catalogs", "manifests"])
def test_incident_corpus_rejects_orphan_resources(tmp_path: Path, resource: str) -> None:
    roots = copy_incident_corpus(tmp_path)
    selected = cast(Path, getattr(roots, resource))
    shutil.copy2(sorted(selected.glob("*.json"))[0], selected / "orphan.json")

    with pytest.raises(CorpusValidationError, match="resource_count"):
        validate_incident_corpus(roots)


@pytest.mark.parametrize(
    ("field", "value", "category"),
    [
        ("fixture_fingerprint", "0" * 64, "fingerprint_mismatch"),
        ("split", "validation", "split_contract"),
        ("unexpected_private", {"expected": "hidden"}, "invalid_resource"),
        ("provenance", {"source": "downloaded", "license": "unknown"}, "invalid_resource"),
    ],
)
def test_incident_corpus_rejects_manifest_identity_and_privacy_drift(
    tmp_path: Path, field: str, value: object, category: str
) -> None:
    roots = copy_incident_corpus(tmp_path)
    path = roots.manifests / "rollback-checkout-canary.json"
    payload = read_json(path)
    payload[field] = value
    write_json(path, payload)

    with pytest.raises(CorpusValidationError, match=category):
        validate_incident_corpus(roots)


def test_incident_corpus_rejects_cross_split_scenario_family(tmp_path: Path) -> None:
    roots = copy_incident_corpus(tmp_path)
    train = read_json(roots.manifests / "rollback-checkout-canary.json")
    test_path = roots.manifests / "escalate-ambiguous-api-errors.json"
    test_manifest = read_json(test_path)
    test_manifest["scenario_family"] = train["scenario_family"]
    write_json(test_path, test_manifest)

    with pytest.raises(CorpusValidationError, match="scenario_split"):
        validate_incident_corpus(roots)


def test_incident_corpus_rejects_private_expected_in_public_task(tmp_path: Path) -> None:
    roots = copy_incident_corpus(tmp_path)
    fixture_path = roots.fixtures / "restart-stuck-order-worker.json"
    fixture = read_json(fixture_path)
    fixture["task"]["expected"] = {"service_states": {"order-worker": "healthy"}}
    write_json(fixture_path, fixture)
    manifest_path = roots.manifests / "restart-stuck-order-worker.json"
    manifest = read_json(manifest_path)
    manifest["fixture_fingerprint"] = canonical_sha256(fixture)
    write_json(manifest_path, manifest)

    with pytest.raises(CorpusValidationError, match="invalid_resource"):
        validate_incident_corpus(roots)


@pytest.mark.parametrize("bad_bytes", [b"{not-json}\n", b"\xff\xfe"])
def test_incident_corpus_rejects_bad_json_and_utf8(tmp_path: Path, bad_bytes: bytes) -> None:
    roots = copy_incident_corpus(tmp_path)
    (roots.fixtures / "restart-stuck-order-worker.json").write_bytes(bad_bytes)

    with pytest.raises(CorpusValidationError, match="invalid_resource"):
        validate_incident_corpus(roots)


def test_incident_corpus_rejects_fixture_task_identity_mismatch(tmp_path: Path) -> None:
    roots = copy_incident_corpus(tmp_path)
    fixture_path = roots.fixtures / "restart-stuck-order-worker.json"
    fixture = read_json(fixture_path)
    fixture["task"]["task_id"] = "incident.other-worker"
    write_json(fixture_path, fixture)
    manifest_path = roots.manifests / "restart-stuck-order-worker.json"
    manifest = read_json(manifest_path)
    manifest["fixture_fingerprint"] = canonical_sha256(fixture)
    write_json(manifest_path, manifest)

    with pytest.raises(CorpusValidationError, match="identity_mismatch"):
        validate_incident_corpus(roots)


def test_incident_corpus_rejects_catalog_tool_outside_task_allowlist(
    tmp_path: Path,
) -> None:
    roots = copy_incident_corpus(tmp_path)
    fixture_path = roots.fixtures / "restart-stuck-order-worker.json"
    fixture = read_json(fixture_path)
    fixture["task"]["allowed_tools"] = ["acknowledge_incident"]
    write_json(fixture_path, fixture)
    manifest_path = roots.manifests / "restart-stuck-order-worker.json"
    manifest = read_json(manifest_path)
    manifest["fixture_fingerprint"] = canonical_sha256(fixture)
    write_json(manifest_path, manifest)

    with pytest.raises(CorpusValidationError, match="tool_allowlist"):
        validate_incident_corpus(roots)


def test_incident_corpus_failure_is_read_only(tmp_path: Path) -> None:
    roots = copy_incident_corpus(tmp_path)
    manifest_path = roots.manifests / "rollback-checkout-canary.json"
    manifest = read_json(manifest_path)
    manifest["fixture_fingerprint"] = "0" * 64
    write_json(manifest_path, manifest)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(CorpusValidationError, match="fingerprint_mismatch"):
        validate_incident_corpus(roots)

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "audit.jsonl").exists()
