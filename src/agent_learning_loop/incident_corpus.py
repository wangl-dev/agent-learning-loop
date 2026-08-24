"""Fail-closed corpus validation for packaged Incident v1 resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, ValidationError

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.corpus import CorpusBudgets, CorpusProvenance, CorpusValidationError
from agent_learning_loop.incident_catalog import IncidentActionCatalog, incident_catalog_fingerprint
from agent_learning_loop.incident_schemas import IncidentTaskFixture
from agent_learning_loop.schemas import StrictModel

IncidentSplit = Literal["train", "validation", "test"]
FIXED_INCIDENT_SPLITS: dict[str, IncidentSplit] = {
    "incident.rollback-checkout-canary": "train",
    "incident.restart-stuck-order-worker": "train",
    "incident.enable-catalog-cache-fallback": "train",
    "incident.acknowledge-auto-recovered-search": "train",
    "incident.dedupe-notification-restart": "train",
    "incident.escalate-denied-payment-restart": "train",
    "incident.recover-auth-dependency-chain": "validation",
    "incident.isolate-inventory-config-change": "validation",
    "incident.reject-premature-checkout-ack": "test",
    "incident.escalate-ambiguous-api-errors": "test",
}


class IncidentCorpusManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    manifest_id: str = Field(pattern=r"^incident\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.manifest\.v1$")
    task_id: str = Field(pattern=r"^incident\.[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_version: Literal[1]
    environment_kind: Literal["incident"]
    fixture_id: str = Field(pattern=r"^incident\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.v1$")
    fixture_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_id: str = Field(pattern=r"^incident\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.actions\.v1$")
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: IncidentSplit
    seed: int = Field(ge=0)
    budgets: CorpusBudgets
    safety_constraints: list[str] = Field(min_length=1)
    verifier_id: Literal["incident-state-v1"]
    scenario_family: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    provenance: CorpusProvenance


class IncidentCorpusSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    environment: Literal["incident"] = "incident"
    total: int
    train: int
    validation: int
    test: int


@dataclass(frozen=True)
class IncidentCorpus:
    fixtures: tuple[IncidentTaskFixture, ...]
    catalogs: tuple[IncidentActionCatalog, ...]
    manifests: tuple[IncidentCorpusManifest, ...]
    summary: IncidentCorpusSummary


@dataclass(frozen=True)
class IncidentResourceRoots:
    fixtures: Path
    catalogs: Path
    manifests: Path


def packaged_incident_roots() -> IncidentResourceRoots:
    root = cast(Path, files("agent_learning_loop"))
    return IncidentResourceRoots(
        fixtures=root / "task_fixtures" / "incident",
        catalogs=root / "action_catalogs" / "incident",
        manifests=root / "task_manifests" / "incident",
    )


def validate_incident_corpus(
    roots: IncidentResourceRoots | None = None,
) -> IncidentCorpus:
    selected = packaged_incident_roots() if roots is None else roots
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
            IncidentTaskFixture.model_validate_json(path.read_bytes()) for path in fixture_paths
        )
        catalogs = tuple(
            IncidentActionCatalog.model_validate_json(path.read_bytes()) for path in catalog_paths
        )
        manifests = tuple(
            IncidentCorpusManifest.model_validate_json(path.read_bytes()) for path in manifest_paths
        )
        fixture_payload_by_id = {
            item.task.fixture_id: json.loads(path.read_text(encoding="utf-8"))
            for item, path in zip(fixtures, fixture_paths, strict=True)
        }
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise CorpusValidationError("invalid_resource") from exc
    fixture_by_id = {item.task.fixture_id: item for item in fixtures}
    catalog_by_id = {item.catalog_id: item for item in catalogs}
    manifest_by_task = {item.task_id: item for item in manifests}
    if len(fixture_by_id) != 10 or len(catalog_by_id) != 10 or len(manifest_by_task) != 10:
        raise CorpusValidationError("duplicate_identity")
    if set(manifest_by_task) != set(FIXED_INCIDENT_SPLITS):
        raise CorpusValidationError("split_contract")
    counts = {"train": 0, "validation": 0, "test": 0}
    families: dict[str, IncidentSplit] = {}
    for task_id, manifest in manifest_by_task.items():
        fixture = fixture_by_id.get(manifest.fixture_id)
        catalog = catalog_by_id.get(manifest.catalog_id)
        if fixture is None or catalog is None:
            raise CorpusValidationError("resource_reference")
        if (
            fixture.task.task_id != task_id
            or catalog.task_id != task_id
            or manifest.manifest_id != f"{task_id}.manifest.v1"
        ):
            raise CorpusValidationError("identity_mismatch")
        if manifest.split != FIXED_INCIDENT_SPLITS[task_id]:
            raise CorpusValidationError("split_contract")
        fixture_fingerprint = canonical_sha256(fixture_payload_by_id[manifest.fixture_id])
        if fixture_fingerprint != manifest.fixture_fingerprint:
            raise CorpusValidationError("fingerprint_mismatch")
        if incident_catalog_fingerprint(catalog) != manifest.catalog_fingerprint:
            raise CorpusValidationError("fingerprint_mismatch")
        if any(entry.tool_name not in fixture.task.allowed_tools for entry in catalog.actions):
            raise CorpusValidationError("tool_allowlist")
        previous = families.setdefault(manifest.scenario_family, manifest.split)
        if previous != manifest.split:
            raise CorpusValidationError("scenario_split")
        counts[manifest.split] += 1
    if counts != {"train": 6, "validation": 2, "test": 2}:
        raise CorpusValidationError("split_contract")
    return IncidentCorpus(
        fixtures=tuple(sorted(fixtures, key=lambda item: item.task.task_id)),
        catalogs=tuple(sorted(catalogs, key=lambda item: item.task_id)),
        manifests=tuple(sorted(manifests, key=lambda item: item.task_id)),
        summary=IncidentCorpusSummary(
            total=10,
            train=counts["train"],
            validation=counts["validation"],
            test=counts["test"],
        ),
    )
