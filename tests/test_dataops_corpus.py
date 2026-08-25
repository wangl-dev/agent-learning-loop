from __future__ import annotations

import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.cli import main
from agent_learning_loop.corpus import CorpusValidationError
from agent_learning_loop.dataops_catalog import DataOpsActionCatalog, dataops_catalog_fingerprint
from agent_learning_loop.dataops_corpus import (
    FIXED_DATAOPS_SPLITS,
    DataOpsCorpusManifest,
    DataOpsResourceRoots,
    packaged_dataops_roots,
    validate_all_corpora,
    validate_dataops_corpus,
)
from agent_learning_loop.dataops_runner import run_all_dataops_tasks
from agent_learning_loop.dataops_schemas import DataOpsTaskFixture


def copied_roots(tmp_path: Path) -> DataOpsResourceRoots:
    source = packaged_dataops_roots()
    target = DataOpsResourceRoots(
        fixtures=tmp_path / "fixtures",
        catalogs=tmp_path / "catalogs",
        manifests=tmp_path / "manifests",
    )
    shutil.copytree(source.fixtures, target.fixtures)
    shutil.copytree(source.catalogs, target.catalogs)
    shutil.copytree(source.manifests, target.manifests)
    return target


def rewrite_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def refresh_fixture_fingerprint(roots: DataOpsResourceRoots, stem: str) -> None:
    fixture_path = roots.fixtures / f"{stem}.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    manifest_path = roots.manifests / f"{stem}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture_id"] = fixture["task"]["fixture_id"]
    manifest["fixture_fingerprint"] = canonical_sha256(fixture)
    rewrite_json(manifest_path, manifest)


def refresh_catalog_fingerprint(roots: DataOpsResourceRoots, stem: str) -> None:
    catalog_path = roots.catalogs / f"{stem}.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    model = DataOpsActionCatalog.model_validate(catalog)
    manifest_path = roots.manifests / f"{stem}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["catalog_fingerprint"] = dataops_catalog_fingerprint(model)
    rewrite_json(manifest_path, manifest)


def test_packaged_dataops_corpus_is_fixed_ten_task_six_two_two() -> None:
    corpus = validate_dataops_corpus()

    assert {item.task.task_id for item in corpus.fixtures} == set(FIXED_DATAOPS_SPLITS)
    assert corpus.summary.model_dump() == {
        "schema_version": "1",
        "environment": "dataops",
        "total": 10,
        "train": 6,
        "validation": 2,
        "test": 2,
    }
    rollback = {
        item.task.task_id
        for item in corpus.fixtures
        if item.private.expected.terminal_state == "rolled_back"
    }
    assert {
        "dataops.rollback-ambiguous-customer-match",
        "dataops.rollback-unique-key-conflict",
        "dataops.detect-stale-version-precondition",
    } <= rollback


def test_all_corpus_gate_is_read_only_thirty_eighteen_six_six() -> None:
    summary = validate_all_corpora()

    assert summary.model_dump() == {
        "schema_version": "1",
        "environment": "all",
        "total": 30,
        "train": 18,
        "validation": 6,
        "test": 6,
        "environments": {
            name: {"total": 10, "train": 6, "validation": 2, "test": 2}
            for name in ("workspace", "incident", "dataops")
        },
    }


@pytest.mark.parametrize("resource", ["fixture", "catalog", "manifest"])
def test_validator_rejects_missing_resource(resource: str, tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    directory = getattr(roots, f"{resource}s")
    next(directory.glob("*.json")).unlink()

    with pytest.raises(CorpusValidationError, match="resource_count"):
        validate_dataops_corpus(roots)


def test_validator_recomputes_fixture_and_catalog_fingerprints(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    fixture_path = roots.fixtures / "correct-order-status.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    payload["task"]["instruction"] = "silently replaced"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="fingerprint_mismatch"):
        validate_dataops_corpus(roots)


def test_validator_rejects_duplicate_identity_and_fixed_split_drift(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    first = roots.manifests / "correct-order-status.json"
    duplicate_target = roots.manifests / "sync-daily-summary.json"
    duplicate_target.write_bytes(first.read_bytes())

    with pytest.raises(CorpusValidationError, match="duplicate_identity"):
        validate_dataops_corpus(roots)

    roots = copied_roots(tmp_path / "split")
    manifest_path = roots.manifests / "correct-order-status.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["split"] = "test"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="split_contract"):
        validate_dataops_corpus(roots)


def test_validator_rejects_family_cross_split_and_bad_provenance(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    train_path = roots.manifests / "correct-order-status.json"
    test_path = roots.manifests / "preserve-neighbor-tenant.json"
    train = json.loads(train_path.read_text(encoding="utf-8"))
    test = json.loads(test_path.read_text(encoding="utf-8"))
    test["scenario_family"] = train["scenario_family"]
    test_path.write_text(json.dumps(test), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="duplicate_scenario_family"):
        validate_dataops_corpus(roots)

    roots = copied_roots(tmp_path / "provenance")
    manifest_path = roots.manifests / "correct-order-status.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["provenance"]["source"] = "real-customer-export"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CorpusValidationError, match="invalid_resource"):
        validate_dataops_corpus(roots)


@pytest.mark.parametrize("field", ["seed", "scenario_family"])
def test_validator_rejects_same_split_duplicate_seed_or_family(
    field: str, tmp_path: Path
) -> None:
    roots = copied_roots(tmp_path)
    first_path = roots.manifests / "correct-order-status.json"
    second_path = roots.manifests / "sync-daily-summary.json"
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    second[field] = first[field]
    rewrite_json(second_path, second)

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_rejects_foreign_action_ref_after_fingerprint_refresh(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    path = roots.catalogs / "correct-order-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["actions"][0]["action_ref"] = "dataops.other-task.step-1.v1"
    rewrite_json(path, payload)
    refresh_catalog_fingerprint(roots, "correct-order-status")

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_rejects_fixture_alias_after_fingerprint_refresh(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    path = roots.fixtures / "correct-order-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["task"]["fixture_id"] = "dataops.fixture-alias.v1"
    rewrite_json(path, payload)
    refresh_fixture_fingerprint(roots, "correct-order-status")

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_rejects_resource_filename_alias(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    (roots.fixtures / "correct-order-status.json").rename(
        roots.fixtures / "renamed-correct-order-status.json"
    )

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_rejects_out_of_scope_catalog_action_after_fingerprint_refresh(
    tmp_path: Path,
) -> None:
    roots = copied_roots(tmp_path)
    path = roots.catalogs / "correct-order-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["actions"][1]["action"]["arguments"]["table"] = "shadow_records"
    rewrite_json(path, payload)
    refresh_catalog_fingerprint(roots, "correct-order-status")

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_rejects_catalog_transaction_alias_after_fingerprint_refresh(
    tmp_path: Path,
) -> None:
    roots = copied_roots(tmp_path)
    path = roots.catalogs / "correct-order-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["actions"][1]["action"]["arguments"]["transaction_id"] = "tx-alias"
    rewrite_json(path, payload)
    refresh_catalog_fingerprint(roots, "correct-order-status")

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_rejects_operation_map_alias_after_fingerprint_refresh(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    path = roots.fixtures / "correct-order-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload["private"]["expected"]
    expected["exact_attempted_by_operation"] = {"op-alias": 1}
    expected["exact_committed_by_operation"] = {"op-alias": 1}
    rewrite_json(path, payload)
    refresh_fixture_fingerprint(roots, "correct-order-status")

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_validator_requires_all_eight_dataops_tools_after_fingerprint_refresh(
    tmp_path: Path,
) -> None:
    roots = copied_roots(tmp_path)
    path = roots.fixtures / "correct-order-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["task"]["allowed_tools"].remove("insert_row")
    rewrite_json(path, payload)
    refresh_fixture_fingerprint(roots, "correct-order-status")

    with pytest.raises(CorpusValidationError, match="tool_contract"):
        validate_dataops_corpus(roots)


@pytest.mark.parametrize("rows_kind", ["initial", "expected"])
def test_atomic_fixture_rejects_dangling_parent_reference(rows_kind: str) -> None:
    selected = next(
        item
        for item in validate_dataops_corpus().fixtures
        if item.task.task_id == "dataops.atomic-parent-child-migration"
    )
    payload = selected.model_dump(mode="json")
    if rows_kind == "initial":
        children = next(
            table for table in payload["private"]["tables"] if table["name"] == "children"
        )
        children["rows"][0]["parent_id"] = 999
    else:
        payload["private"]["expected"]["tables"]["children"][0]["parent_id"] = 999

    with pytest.raises(ValidationError):
        DataOpsTaskFixture.model_validate(payload)


def test_validator_rejects_rehashed_dangling_initial_foreign_key(tmp_path: Path) -> None:
    roots = copied_roots(tmp_path)
    path = roots.fixtures / "atomic-parent-child-migration.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    children = next(
        table for table in payload["private"]["tables"] if table["name"] == "children"
    )
    children["rows"][0]["parent_id"] = 999
    rewrite_json(path, payload)
    refresh_fixture_fingerprint(roots, "atomic-parent-child-migration")

    with pytest.raises(CorpusValidationError):
        validate_dataops_corpus(roots)


def test_legal_atomic_parent_child_fixture_and_corpus_are_accepted() -> None:
    corpus = validate_dataops_corpus()
    selected = next(
        item
        for item in corpus.fixtures
        if item.task.task_id == "dataops.atomic-parent-child-migration"
    )

    assert DataOpsTaskFixture.model_validate(selected.model_dump(mode="json")) == selected
    assert corpus.summary.total == 10


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_initial_type",
        "null_not_null",
        "duplicate_initial_unique",
        "missing_expected_column",
        "wrong_expected_type",
        "null_expected_not_null",
        "duplicate_expected_unique",
        "unknown_protected_filter",
        "wrong_protected_type",
        "empty_protected_filter",
    ],
)
def test_fixture_model_rejects_schema_and_protected_filter_aliases(mutation: str) -> None:
    selected = next(
        item
        for item in validate_dataops_corpus().fixtures
        if item.task.task_id == "dataops.correct-order-status"
    )
    fixture = selected.model_dump(mode="json")
    table = fixture["private"]["tables"][0]
    expected = fixture["private"]["expected"]
    if mutation == "wrong_initial_type":
        table["rows"][0]["id"] = "1"
    elif mutation == "null_not_null":
        table["rows"][0]["tenant_id"] = None
    elif mutation == "duplicate_initial_unique":
        table["rows"][1]["code"] = table["rows"][0]["code"]
    elif mutation == "missing_expected_column":
        expected["tables"][table["name"]][0].pop("value")
    elif mutation == "wrong_expected_type":
        expected["tables"][table["name"]][0]["id"] = "1"
    elif mutation == "null_expected_not_null":
        expected["tables"][table["name"]][0]["tenant_id"] = None
    elif mutation == "duplicate_expected_unique":
        expected["tables"][table["name"]][1]["code"] = expected["tables"][table["name"]][0][
            "code"
        ]
    elif mutation == "unknown_protected_filter":
        expected["protected_rows"][0]["where"] = {"unknown_column": 2}
    elif mutation == "wrong_protected_type":
        expected["protected_rows"][0]["where"] = {"id": "2"}
    else:
        expected["protected_rows"][0]["where"] = {"id": 999}

    with pytest.raises(ValidationError):
        DataOpsTaskFixture.model_validate(fixture)


def test_manifest_rejects_private_identity_and_provenance_fields() -> None:
    manifest = validate_dataops_corpus().manifests[0].model_dump(mode="json")
    manifest["customer_email"] = "private@example.invalid"
    with pytest.raises(ValidationError):
        DataOpsCorpusManifest.model_validate(manifest)

    fixture = validate_dataops_corpus().fixtures[0].model_dump(mode="json")
    fixture["task"]["private_expected"] = {"rows": []}
    with pytest.raises(ValidationError):
        DataOpsTaskFixture.model_validate(fixture)

    fixture = validate_dataops_corpus().fixtures[0].model_dump(mode="json")
    fixture["private"]["tables"][0]["rows"][0]["tenant_id"] = "person@example.invalid"
    with pytest.raises(ValidationError):
        DataOpsTaskFixture.model_validate(fixture)


def test_scripted_ten_tasks_pass_without_database_or_private_output(tmp_path: Path) -> None:
    output = tmp_path / "runs"
    results = run_all_dataops_tasks(output)

    assert len(results) == 10
    assert all(result.outcome == "passed" for result in results)
    assert not [
        path
        for path in output.rglob("*")
        if path.is_file()
        and (
            path.suffix in {".sqlite", ".db"}
            or path.name.endswith("-journal")
            or path.name.endswith("-wal")
        )
    ]
    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file()
    ).lower()
    assert '"private"' not in public_text
    assert '"expected"' not in public_text
    assert "raw sql" not in public_text
    assert "task.sqlite" not in public_text
    assert str(tmp_path).lower() not in public_text


def test_cli_exposes_dataops_and_all_without_changing_default_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate-corpus"]) == 0
    assert json.loads(capsys.readouterr().out)["environment"] == "workspace"
    assert main(["validate-corpus", "--environment", "dataops"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 10
    assert main(["validate-corpus", "--environment", "all"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 30
    assert main(["run-dataops", "--task", "all", "--output-dir", str(tmp_path / "cli")]) == 0
    assert capsys.readouterr().out.count("passed") == 10


def test_three_environment_package_data_counts_and_database_absence_are_exact() -> None:
    root = cast(Path, files("agent_learning_loop"))

    for environment in ("workspace", "incident", "dataops"):
        assert len(list((root / "task_fixtures" / environment).glob("*.json"))) == 10
        catalog_root = root / "action_catalogs"
        if environment != "workspace":
            catalog_root = catalog_root / environment
        assert len(list(catalog_root.glob("*.json"))) == 10
        assert len(list((root / "task_manifests" / environment).glob("*.json"))) == 10
    assert len(list((root / "failure_schedules").glob("*.json"))) == 3
    assert len(list((root / "interruption_schedules").glob("*.json"))) == 1
    assert not [
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.suffix in {".sqlite", ".db"}
            or path.name.endswith("-journal")
            or path.name.endswith("-wal")
        )
    ]
