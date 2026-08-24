"""Versioned project-authored action catalogs for Policy and M3B replay."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.schemas import Action, StrictModel, ToolName


class ActionCatalogError(ValueError):
    """A packaged action catalog is missing or violates its reviewed identity."""


class ActionCatalogNotFoundError(ActionCatalogError):
    """The requested task has no project-authored action catalog."""


class ActionCatalogMismatchError(ActionCatalogError):
    """A catalog no longer matches its versioned golden fingerprint."""


class CatalogAction(StrictModel):
    step_index: int = Field(ge=1)
    action_ref: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    tool_name: ToolName
    action: Action

    @model_validator(mode="after")
    def tool_identity_matches_action(self) -> Self:
        if self.tool_name != self.action.tool_name:
            raise ValueError("catalog tool name does not match its strict Action")
        return self


class ActionCatalog(StrictModel):
    schema_version: Literal["1"] = "1"
    catalog_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    task_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    actions: list[CatalogAction] = Field(min_length=1)
    provenance: Literal["Project-authored deterministic action catalog."]

    @model_validator(mode="after")
    def catalog_is_ordered_and_versioned(self) -> Self:
        if self.catalog_id != f"{self.task_id}.actions.v1":
            raise ValueError("catalog ID does not version its task identity")
        if [entry.step_index for entry in self.actions] != list(
            range(1, len(self.actions) + 1)
        ):
            raise ValueError("catalog action steps must be ordered and continuous")
        refs = [entry.action_ref for entry in self.actions]
        if len(refs) != len(set(refs)):
            raise ValueError("catalog action references must be unique")
        if any(
            not ref.startswith(f"{self.task_id}.step-") or not ref.endswith(".v1")
            for ref in refs
        ):
            raise ValueError("catalog action reference does not match task/version")
        return self


GOLDEN_CATALOG_FINGERPRINTS = {
    "workspace.build-summary": "79a52f1f6cef8f91bfdc47e3398b71f7f9a7430bb0f086783e58e5acab79d8af",
    "workspace.fix-config": "b525ee02e439264500f7020abe5c02d8fd344651d91d4b3651686f210d0cf7c4",
    "workspace.update-status": "369a631bd2636a25e8b8f7bfbe9ed3ac5399f2485cc5a0a975454a7ee53def8b",
}


def action_fingerprint(action: Action) -> str:
    """Return the canonical identity of one complete strict Action."""
    return _fingerprint(action.model_dump(mode="json"))


def catalog_fingerprint(catalog: ActionCatalog) -> str:
    """Return the canonical identity of the complete ordered catalog."""
    return _fingerprint(catalog.model_dump(mode="json"))


def load_action_catalog(task_id: str) -> ActionCatalog:
    """Load one catalog from the validated packaged corpus."""
    from agent_learning_loop.corpus import (
        CorpusValidationError,
        validate_workspace_corpus,
    )

    try:
        catalogs = validate_workspace_corpus().catalogs
    except CorpusValidationError as exc:
        raise ActionCatalogMismatchError("packaged action catalog is invalid") from exc
    for catalog in catalogs:
        if catalog.task_id == task_id:
            return catalog
    raise ActionCatalogNotFoundError(f"unknown action catalog task: {task_id}")


def load_all_action_catalogs() -> list[ActionCatalog]:
    from agent_learning_loop.corpus import validate_workspace_corpus

    return list(validate_workspace_corpus().catalogs)


def _fingerprint(payload: object) -> str:
    return canonical_sha256(payload)
