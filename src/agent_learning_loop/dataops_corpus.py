"""Fail-closed corpus validation for packaged DataOps v1 resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, ValidationError

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.corpus import CorpusBudgets, CorpusProvenance, CorpusValidationError
from agent_learning_loop.dataops_catalog import DataOpsActionCatalog, dataops_catalog_fingerprint
from agent_learning_loop.dataops_schemas import DATAOPS_TOOL_NAMES, DataOpsTaskFixture
from agent_learning_loop.schemas import StrictModel

DataOpsSplit = Literal["train", "validation", "test"]
FIXED_DATAOPS_SPLITS: dict[str, DataOpsSplit] = {
    "dataops.correct-order-status": "train",
    "dataops.sync-daily-summary": "train",
    "dataops.insert-missing-product-mapping": "train",
    "dataops.normalize-legacy-regions": "train",
    "dataops.rollback-ambiguous-customer-match": "train",
    "dataops.reject-transactionless-update": "train",
    "dataops.atomic-parent-child-migration": "validation",
    "dataops.rollback-unique-key-conflict": "validation",
    "dataops.detect-stale-version-precondition": "test",
    "dataops.preserve-neighbor-tenant": "test",
}


class DataOpsCorpusManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    manifest_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.manifest\.v1$")
    task_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_version: Literal[1]
    environment_kind: Literal["dataops"]
    fixture_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.v1$")
    fixture_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.actions\.v1$")
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: DataOpsSplit
    seed: int = Field(ge=0)
    budgets: CorpusBudgets
    safety_constraints: list[str] = Field(min_length=1)
    verifier_id: Literal["dataops-state-v1"]
    scenario_family: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    provenance: CorpusProvenance


class DataOpsCorpusSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    environment: Literal["dataops"] = "dataops"
    total: int
    train: int
    validation: int
    test: int


class AllCorpusSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    environment: Literal["all"] = "all"
    total: Literal[30] = 30
    train: Literal[18] = 18
    validation: Literal[6] = 6
    test: Literal[6] = 6
    environments: dict[str, dict[str, int]]


@dataclass(frozen=True)
class DataOpsCorpus:
    fixtures: tuple[DataOpsTaskFixture, ...]
    catalogs: tuple[DataOpsActionCatalog, ...]
    manifests: tuple[DataOpsCorpusManifest, ...]
    summary: DataOpsCorpusSummary


@dataclass(frozen=True)
class DataOpsResourceRoots:
    fixtures: Path
    catalogs: Path
    manifests: Path


def packaged_dataops_roots() -> DataOpsResourceRoots:
    root = cast(Path, files("agent_learning_loop"))
    return DataOpsResourceRoots(
        fixtures=root / "task_fixtures" / "dataops",
        catalogs=root / "action_catalogs" / "dataops",
        manifests=root / "task_manifests" / "dataops",
    )


def _validate_action_contract(
    fixture: DataOpsTaskFixture,
    catalog: DataOpsActionCatalog,
) -> None:
    scope_by_table = {scope.table: scope for scope in fixture.task.scope}
    table_by_name = {table.name: table for table in fixture.private.tables}
    begin_actions = [
        entry for entry in catalog.actions if entry.action.tool_name == "begin_transaction"
    ]
    terminal_actions = [
        entry
        for entry in catalog.actions
        if entry.action.tool_name in {"commit_transaction", "rollback_transaction"}
    ]
    if len(begin_actions) != 1 or len(terminal_actions) != 1:
        raise CorpusValidationError("transaction_contract")
    if terminal_actions[0] is not catalog.actions[-1]:
        raise CorpusValidationError("transaction_contract")
    transaction_id = begin_actions[0].action.arguments.get("transaction_id")
    if not isinstance(transaction_id, str) or any(
        entry.action.arguments.get("transaction_id") != transaction_id
        for entry in catalog.actions
        if "transaction_id" in entry.action.arguments
    ):
        raise CorpusValidationError("transaction_contract")
    expected_terminal_tool = (
        "commit_transaction"
        if fixture.private.expected.terminal_state == "committed"
        else "rollback_transaction"
    )
    if terminal_actions[0].action.tool_name != expected_terminal_tool:
        raise CorpusValidationError("transaction_contract")

    operation_ids: list[str] = []
    for entry in catalog.actions:
        action = entry.action
        arguments = action.arguments
        table_name = arguments.get("table")
        if table_name is not None:
            if not isinstance(table_name, str) or table_name not in scope_by_table:
                raise CorpusValidationError("action_scope")
            scope = scope_by_table[table_name]
            table = table_by_name[table_name]
            if action.tool_name == "describe_table":
                continue
            if action.tool_name == "query_rows":
                columns = arguments.get("columns")
                where = arguments.get("where")
                if (
                    not isinstance(columns, list)
                    or not set(columns) <= set(scope.readable_columns)
                    or not isinstance(where, dict)
                    or not set(where) <= set(scope.predicate_columns)
                ):
                    raise CorpusValidationError("action_scope")
            elif action.tool_name == "update_rows":
                where = arguments.get("where")
                values = arguments.get("values")
                if (
                    not isinstance(where, dict)
                    or not set(where) <= set(scope.predicate_columns)
                    or not isinstance(values, dict)
                    or not set(values) <= set(scope.mutable_columns)
                ):
                    raise CorpusValidationError("action_scope")
            elif action.tool_name == "insert_row":
                row = arguments.get("row")
                expected_columns = {column.name for column in table.columns}
                if (
                    not scope.allow_insert
                    or not isinstance(row, dict)
                    or set(row) != expected_columns
                ):
                    raise CorpusValidationError("action_scope")
        if action.tool_name in {"update_rows", "insert_row"}:
            operation_id = arguments.get("operation_id")
            if not isinstance(operation_id, str):
                raise CorpusValidationError("operation_contract")
            operation_ids.append(operation_id)

    if len(operation_ids) != len(set(operation_ids)):
        raise CorpusValidationError("operation_contract")
    expected_operations = set(fixture.private.expected.exact_attempted_by_operation)
    if (
        set(operation_ids) != expected_operations
        or set(fixture.private.expected.exact_committed_by_operation) != expected_operations
    ):
        raise CorpusValidationError("operation_contract")


def validate_dataops_corpus(roots: DataOpsResourceRoots | None = None) -> DataOpsCorpus:
    selected = packaged_dataops_roots() if roots is None else roots
    try:
        fixture_paths = sorted(selected.fixtures.glob("*.json"))
        catalog_paths = sorted(selected.catalogs.glob("*.json"))
        manifest_paths = sorted(selected.manifests.glob("*.json"))
    except OSError as exc:
        raise CorpusValidationError("invalid_resource") from exc
    if not (len(fixture_paths) == len(catalog_paths) == len(manifest_paths) == 10):
        raise CorpusValidationError("resource_count")
    try:
        fixtures = tuple(
            DataOpsTaskFixture.model_validate_json(path.read_bytes()) for path in fixture_paths
        )
        catalogs = tuple(
            DataOpsActionCatalog.model_validate_json(path.read_bytes()) for path in catalog_paths
        )
        manifests = tuple(
            DataOpsCorpusManifest.model_validate_json(path.read_bytes()) for path in manifest_paths
        )
        fixture_payloads = {
            item.task.fixture_id: json.loads(path.read_text(encoding="utf-8"))
            for item, path in zip(fixtures, fixture_paths, strict=True)
        }
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise CorpusValidationError("invalid_resource") from exc
    if (
        len({item.task.fixture_id for item in fixtures}) != 10
        or len({item.catalog_id for item in catalogs}) != 10
        or len({item.task_id for item in manifests}) != 10
    ):
        raise CorpusValidationError("duplicate_identity")
    for parsed_fixture, path in zip(fixtures, fixture_paths, strict=True):
        if path.stem != parsed_fixture.task.task_id.removeprefix("dataops."):
            raise CorpusValidationError("resource_filename")
    for parsed_catalog, path in zip(catalogs, catalog_paths, strict=True):
        if path.stem != parsed_catalog.task_id.removeprefix("dataops."):
            raise CorpusValidationError("resource_filename")
    for manifest, path in zip(manifests, manifest_paths, strict=True):
        if path.stem != manifest.task_id.removeprefix("dataops."):
            raise CorpusValidationError("resource_filename")
    fixture_by_id = {item.task.fixture_id: item for item in fixtures}
    catalog_by_id = {item.catalog_id: item for item in catalogs}
    manifest_by_task = {item.task_id: item for item in manifests}
    if len(fixture_by_id) != 10 or len(catalog_by_id) != 10 or len(manifest_by_task) != 10:
        raise CorpusValidationError("duplicate_identity")
    if set(manifest_by_task) != set(FIXED_DATAOPS_SPLITS):
        raise CorpusValidationError("split_contract")
    counts = {"train": 0, "validation": 0, "test": 0}
    seeds: set[int] = set()
    families: set[str] = set()
    for task_id, manifest in manifest_by_task.items():
        fixture = fixture_by_id.get(manifest.fixture_id)
        catalog = catalog_by_id.get(manifest.catalog_id)
        if fixture is None or catalog is None:
            raise CorpusValidationError("resource_reference")
        if (
            fixture.task.task_id != task_id
            or catalog.task_id != task_id
            or manifest.manifest_id != f"{task_id}.manifest.v1"
            or manifest.fixture_id != f"{task_id}.v1"
            or manifest.catalog_id != f"{task_id}.actions.v1"
        ):
            raise CorpusValidationError("identity_mismatch")
        if manifest.split != FIXED_DATAOPS_SPLITS[task_id]:
            raise CorpusValidationError("split_contract")
        if canonical_sha256(fixture_payloads[manifest.fixture_id]) != manifest.fixture_fingerprint:
            raise CorpusValidationError("fingerprint_mismatch")
        if dataops_catalog_fingerprint(catalog) != manifest.catalog_fingerprint:
            raise CorpusValidationError("fingerprint_mismatch")
        if any(entry.tool_name not in fixture.task.allowed_tools for entry in catalog.actions):
            raise CorpusValidationError("tool_allowlist")
        if tuple(fixture.task.allowed_tools) != DATAOPS_TOOL_NAMES:
            raise CorpusValidationError("tool_contract")
        if any(
            entry.action_ref != f"{task_id}.step-{entry.step_index}.v1"
            for entry in catalog.actions
        ):
            raise CorpusValidationError("action_reference")
        if manifest.seed in seeds:
            raise CorpusValidationError("duplicate_seed")
        if manifest.scenario_family in families:
            raise CorpusValidationError("duplicate_scenario_family")
        seeds.add(manifest.seed)
        families.add(manifest.scenario_family)
        _validate_action_contract(fixture, catalog)
        counts[manifest.split] += 1
    if counts != {"train": 6, "validation": 2, "test": 2}:
        raise CorpusValidationError("split_contract")
    return DataOpsCorpus(
        fixtures=tuple(sorted(fixtures, key=lambda item: item.task.task_id)),
        catalogs=tuple(sorted(catalogs, key=lambda item: item.task_id)),
        manifests=tuple(sorted(manifests, key=lambda item: item.task_id)),
        summary=DataOpsCorpusSummary(
            total=10,
            train=counts["train"],
            validation=counts["validation"],
            test=counts["test"],
        ),
    )


def validate_all_corpora() -> AllCorpusSummary:
    """Read and aggregate the three independent validators; never execute tasks."""
    from agent_learning_loop.corpus import validate_workspace_corpus
    from agent_learning_loop.incident_corpus import validate_incident_corpus

    workspace = validate_workspace_corpus().summary
    incident = validate_incident_corpus().summary
    dataops = validate_dataops_corpus().summary
    environments = {
        "workspace": {
            "total": workspace.total,
            "train": workspace.train,
            "validation": workspace.validation,
            "test": workspace.test,
        },
        "incident": {
            "total": incident.total,
            "train": incident.train,
            "validation": incident.validation,
            "test": incident.test,
        },
        "dataops": {
            "total": dataops.total,
            "train": dataops.train,
            "validation": dataops.validation,
            "test": dataops.test,
        },
    }
    if set(environments) != {"workspace", "incident", "dataops"} or any(
        counts != {"total": 10, "train": 6, "validation": 2, "test": 2}
        for counts in environments.values()
    ):
        raise CorpusValidationError("all_corpus_contract")
    return AllCorpusSummary(environments=environments)
