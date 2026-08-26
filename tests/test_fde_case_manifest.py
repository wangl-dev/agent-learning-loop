from __future__ import annotations

from collections import Counter
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from agent_learning_loop.fde_case_manifest import (
    CONTROL_MAPPING,
    FdeCaseManifestError,
    compute_case_definition_fingerprint,
    load_fde_case,
    validate_fde_case_definition,
)
from agent_learning_loop.fde_case_schemas import FdeCaseDefinition


def test_packaged_case_has_exact_incident_identity_and_control_mapping() -> None:
    case = load_fde_case("incident-copilot-v1")

    assert case.case_id == "incident-copilot-v1"
    assert case.scenario_kind == "simulated"
    assert case.environment == "incident"
    assert case.selection.suite == "system-correctness"
    assert case.selection.suite_id == "system-correctness-v1"
    assert case.selection.environment == "incident"
    assert len(case.cells) == 10
    assert Counter(cell.split for cell in case.cells) == {
        "train": 6,
        "validation": 2,
        "test": 2,
    }
    assert sum(cell.split != "train" for cell in case.cells) == 4
    assert {
        control.control_id: tuple(control.cell_ids) for control in case.controls
    } == CONTROL_MAPPING
    held_out = {cell.cell_id for cell in case.cells if cell.split != "train"}
    assert all(held_out.intersection(control.cell_ids) for control in case.controls)
    assert case.definition_fingerprint == compute_case_definition_fingerprint(case)

    resource_root = cast(Path, files("agent_learning_loop")) / "fde_cases"
    assert [path.name for path in resource_root.glob("*.json")] == [
        "incident-copilot-v1.json"
    ]


def test_resigned_case_mapping_cannot_replace_packaged_identity() -> None:
    case = load_fde_case("incident-copilot-v1")
    payload = case.model_dump(mode="json")
    payload["cells"][0]["control_id"] = "approval-bound-change"
    payload["definition_fingerprint"] = compute_case_definition_fingerprint(payload)
    resigned = FdeCaseDefinition.model_validate(payload)

    with pytest.raises(FdeCaseManifestError, match="case_definition_identity"):
        validate_fde_case_definition(resigned)


def test_case_schema_rejects_unknown_and_duplicate_identity() -> None:
    case = load_fde_case("incident-copilot-v1")
    unknown = case.model_dump(mode="json")
    unknown["command"] = "run anything"
    with pytest.raises(ValidationError):
        FdeCaseDefinition.model_validate(unknown)

    duplicate = case.model_dump(mode="json")
    duplicate["cells"][1] = duplicate["cells"][0]
    duplicate["definition_fingerprint"] = compute_case_definition_fingerprint(duplicate)
    with pytest.raises(ValidationError):
        FdeCaseDefinition.model_validate(duplicate)

    empty = case.model_dump(mode="json")
    empty["cells"][0]["cell_id"] = ""
    with pytest.raises(ValidationError):
        FdeCaseDefinition.model_validate(empty)


@pytest.mark.parametrize("mutation", ["task", "split", "seed", "resource", "partition"])
def test_resigned_identity_changes_cannot_replace_packaged_case(mutation: str) -> None:
    case = load_fde_case("incident-copilot-v1")
    payload = deepcopy(case.model_dump(mode="json"))
    if mutation == "task":
        old_cell_id = payload["cells"][0]["cell_id"]
        payload["cells"][0]["cell_id"] = "system.incident.forged"
        payload["cells"][0]["task_id"] = "incident.forged"
        control_index = payload["controls"][0]["cell_ids"].index(old_cell_id)
        payload["controls"][0]["cell_ids"][control_index] = "system.incident.forged"
    elif mutation == "split":
        payload["cells"][0]["split"] = "validation"
    elif mutation == "seed":
        payload["cells"][0]["seed"] = 999
    elif mutation == "resource":
        payload["cells"][0]["resource_fingerprint"] = "0" * 64
    else:
        cell_id = payload["cells"][0]["cell_id"]
        payload["cells"][0]["control_id"] = "approval-bound-change"
        payload["controls"][0]["cell_ids"].remove(cell_id)
        payload["controls"][1]["cell_ids"].insert(0, cell_id)
    payload["definition_fingerprint"] = compute_case_definition_fingerprint(payload)
    resigned = FdeCaseDefinition.model_validate(payload)

    with pytest.raises(FdeCaseManifestError, match="case_definition_identity"):
        validate_fde_case_definition(resigned)
