"""Versioned scripted action catalogs for DataOps v1."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.dataops_schemas import DataOpsAction
from agent_learning_loop.schemas import StrictModel


class DataOpsCatalogAction(StrictModel):
    step_index: int = Field(ge=1)
    action_ref: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.step-[0-9]+\.v1$")
    tool_name: str
    action: DataOpsAction

    @model_validator(mode="after")
    def require_matching_tool(self) -> Self:
        if self.tool_name != self.action.tool_name:
            raise ValueError("catalog_tool_mismatch")
        return self


class DataOpsActionCatalog(StrictModel):
    schema_version: Literal["1"] = "1"
    catalog_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.actions\.v1$")
    task_id: str = Field(pattern=r"^dataops\.[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    actions: list[DataOpsCatalogAction] = Field(min_length=1)
    provenance: Literal["Project-authored deterministic DataOps action catalog."]

    @model_validator(mode="after")
    def require_ordered_identity(self) -> Self:
        if self.catalog_id != f"{self.task_id}.actions.v1":
            raise ValueError("catalog_identity")
        if [item.step_index for item in self.actions] != list(range(1, len(self.actions) + 1)):
            raise ValueError("catalog_order")
        return self


def dataops_catalog_fingerprint(catalog: DataOpsActionCatalog) -> str:
    return canonical_sha256(catalog.model_dump(mode="json"))
