"""Strict governance and fail-closed validation for the Workspace corpus."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, TypeVar, cast

from pydantic import BaseModel, Field, ValidationError

from agent_learning_loop.action_catalog import ActionCatalog, catalog_fingerprint
from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.schemas import StrictModel, WorkspaceTaskFixture

CorpusSplit = Literal["train", "validation", "test"]
NonEmptyText = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
ModelT = TypeVar("ModelT", bound=BaseModel)
ItemT = TypeVar("ItemT")

FIXED_WORKSPACE_SPLITS: dict[str, CorpusSplit] = {
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


class CorpusBudgets(StrictModel):
    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0.0)


class CorpusProvenance(StrictModel):
    source: Literal["project-authored-synthetic"]
    license: Literal["Apache-2.0"]


class WorkspaceCorpusManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    manifest_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_version: Literal[1]
    environment_kind: Literal["workspace"]
    fixture_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    fixture_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: CorpusSplit
    seed: int = Field(ge=0)
    budgets: CorpusBudgets
    safety_constraints: list[NonEmptyText] = Field(min_length=1)
    verifier_id: Literal["workspace-state-v1"]
    scenario_family: NonEmptyText
    tags: list[NonEmptyText] = Field(min_length=1)
    provenance: CorpusProvenance


class WorkspaceCorpusSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    environment: Literal["workspace"] = "workspace"
    total: int = Field(ge=0)
    train: int = Field(ge=0)
    validation: int = Field(ge=0)
    test: int = Field(ge=0)


@dataclass(frozen=True)
class CorpusResourceRoots:
    fixtures: Path
    catalogs: Path
    manifests: Path


@dataclass(frozen=True)
class ValidatedWorkspaceCorpus:
    manifests: tuple[WorkspaceCorpusManifest, ...]
    fixtures: tuple[WorkspaceTaskFixture, ...]
    catalogs: tuple[ActionCatalog, ...]
    summary: WorkspaceCorpusSummary


class CorpusValidationError(ValueError):
    """A stable, sanitized corpus validation category."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def packaged_corpus_roots() -> CorpusResourceRoots:
    package_root = cast(Path, files("agent_learning_loop"))
    return CorpusResourceRoots(
        fixtures=package_root / "task_fixtures" / "workspace",
        catalogs=package_root / "action_catalogs",
        manifests=package_root / "task_manifests" / "workspace",
    )


def validate_workspace_corpus(
    roots: CorpusResourceRoots | None = None,
) -> ValidatedWorkspaceCorpus:
    """Validate all packaged Workspace resources before any execution side effect."""
    selected = packaged_corpus_roots() if roots is None else roots
    manifest_paths = _json_resources(selected.manifests, "invalid_manifest")
    fixture_paths = _json_resources(selected.fixtures, "invalid_fixture")
    catalog_paths = _json_resources(selected.catalogs, "invalid_catalog")
    if not (
        len(manifest_paths) == len(fixture_paths) == len(catalog_paths) == 10
    ):
        raise CorpusValidationError("resource_count")

    manifests = tuple(
        _read_model(path, WorkspaceCorpusManifest, "invalid_manifest")
        for path in manifest_paths
    )
    fixtures = tuple(
        _read_model(path, WorkspaceTaskFixture, "invalid_fixture")
        for path in fixture_paths
    )
    catalogs = tuple(
        _read_model(path, ActionCatalog, "invalid_catalog")
        for path in catalog_paths
    )

    manifest_by_task = _unique_mapping(manifests, "task_id")
    _require_unique(manifests, "manifest_id")
    _require_unique(manifests, "fixture_id")
    _require_unique(manifests, "catalog_id")
    fixture_by_id = _unique_mapping(
        fixtures,
        "task.fixture_id",
    )
    catalog_by_id = _unique_mapping(catalogs, "catalog_id")
    _require_unique(fixtures, "task.task_id")
    _require_unique(catalogs, "task_id")

    if set(manifest_by_task) != set(FIXED_WORKSPACE_SPLITS):
        raise CorpusValidationError("split_contract")
    split_counts = {split: 0 for split in ("train", "validation", "test")}
    for task_id, manifest in manifest_by_task.items():
        split_counts[manifest.split] += 1
        if manifest.split != FIXED_WORKSPACE_SPLITS[task_id]:
            raise CorpusValidationError("split_contract")
    if split_counts != {"train": 6, "validation": 2, "test": 2}:
        raise CorpusValidationError("split_contract")

    if set(fixture_by_id) != {item.fixture_id for item in manifests}:
        raise CorpusValidationError("resource_reference")
    if set(catalog_by_id) != {item.catalog_id for item in manifests}:
        raise CorpusValidationError("resource_reference")

    families: dict[str, CorpusSplit] = {}
    for manifest in manifests:
        if manifest.manifest_id != f"{manifest.task_id}.manifest.v1":
            raise CorpusValidationError("identity_mismatch")
        fixture = fixture_by_id[manifest.fixture_id]
        catalog = catalog_by_id[manifest.catalog_id]
        if (
            fixture.task.task_id != manifest.task_id
            or fixture.task.fixture_id != manifest.fixture_id
            or catalog.task_id != manifest.task_id
            or catalog.catalog_id != manifest.catalog_id
        ):
            raise CorpusValidationError("identity_mismatch")
        if (
            canonical_sha256(fixture.model_dump(mode="json"))
            != manifest.fixture_fingerprint
            or catalog_fingerprint(catalog) != manifest.catalog_fingerprint
        ):
            raise CorpusValidationError("fingerprint_mismatch")
        if any(
            entry.tool_name not in fixture.task.allowed_tools
            for entry in catalog.actions
        ):
            raise CorpusValidationError("tool_allowlist")
        previous_split = families.setdefault(manifest.scenario_family, manifest.split)
        if previous_split != manifest.split:
            raise CorpusValidationError("scenario_split")

    ordered_manifests = tuple(sorted(manifests, key=lambda item: item.task_id))
    ordered_fixtures = tuple(sorted(fixtures, key=lambda item: item.task.task_id))
    ordered_catalogs = tuple(sorted(catalogs, key=lambda item: item.task_id))
    return ValidatedWorkspaceCorpus(
        manifests=ordered_manifests,
        fixtures=ordered_fixtures,
        catalogs=ordered_catalogs,
        summary=WorkspaceCorpusSummary(
            total=10,
            train=split_counts["train"],
            validation=split_counts["validation"],
            test=split_counts["test"],
        ),
    )


def _json_resources(root: Path, category: str) -> list[Path]:
    try:
        return sorted(
            (path for path in root.iterdir() if path.name.endswith(".json")),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise CorpusValidationError(category) from exc


def _read_model(
    path: Path,
    model: type[ModelT],
    category: str,
) -> ModelT:
    try:
        text = path.read_bytes().decode("utf-8", errors="strict")
        return model.model_validate_json(text)
    except (OSError, UnicodeError, ValidationError) as exc:
        raise CorpusValidationError(category) from exc


def _identity_value(item: object, field_path: str) -> str:
    value = item
    for field in field_path.split("."):
        value = getattr(value, field)
    if not isinstance(value, str):
        raise CorpusValidationError("invalid_identity")
    return value


def _require_unique(items: tuple[object, ...], field_path: str) -> None:
    values = [_identity_value(item, field_path) for item in items]
    if len(values) != len(set(values)):
        raise CorpusValidationError("duplicate_identity")


def _unique_mapping(items: tuple[ItemT, ...], field_path: str) -> dict[str, ItemT]:
    _require_unique(cast(tuple[object, ...], items), field_path)
    return {_identity_value(item, field_path): item for item in items}
