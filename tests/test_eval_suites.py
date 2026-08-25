from __future__ import annotations

import copy
import json
from importlib.resources import files

import pytest
from pydantic import ValidationError

from agent_learning_loop.eval_schemas import EvalSuiteManifest
from agent_learning_loop.eval_suites import (
    EvalSuiteValidationError,
    load_eval_suites,
    select_eval_cells,
    validate_comparison,
)
from scripts.generate_eval_suites import render_eval_suites_resource


def test_packaged_suites_have_pre_registered_30_7_4_cells() -> None:
    suites = load_eval_suites()

    assert set(suites) == {
        "system-correctness-v1",
        "runtime-reliability-v1",
        "recovery-replay-v1",
    }
    assert {suite_id: len(suite.cells) for suite_id, suite in suites.items()} == {
        "system-correctness-v1": 30,
        "runtime-reliability-v1": 7,
        "recovery-replay-v1": 4,
    }
    system = suites["system-correctness-v1"]
    assert sum(cell.split == "train" for cell in system.cells) == 18
    assert sum(cell.split == "validation" for cell in system.cells) == 6
    assert sum(cell.split == "test" for cell in system.cells) == 6
    assert {cell.environment for cell in system.cells} == {
        "workspace",
        "incident",
        "dataops",
    }
    packaged = files("agent_learning_loop").joinpath(
        "eval_suites", "suites-v1.json"
    )
    assert packaged.read_text(encoding="utf-8") == render_eval_suites_resource()


def test_system_filters_preserve_real_selected_denominator() -> None:
    suites = load_eval_suites()
    selected = select_eval_cells(
        suites,
        "system-correctness",
        environment="workspace",
        split="train",
    )

    assert selected.candidate_total == 30
    assert selected.selected_total == 6
    assert len(selected.cell_ids) == 6
    assert all(cell.environment == "workspace" and cell.split == "train" for cell in selected.cells)

    with pytest.raises(EvalSuiteValidationError, match="unknown_tag"):
        select_eval_cells(suites, "system-correctness", tag="not-registered")
    with pytest.raises(EvalSuiteValidationError, match="empty_selection"):
        select_eval_cells(
            suites,
            "system-correctness",
            environment="dataops",
            split="test",
            tag="ack",
        )


def test_reliability_pair_selection_always_includes_both_arms() -> None:
    suites = load_eval_suites()
    selected = select_eval_cells(
        suites,
        "runtime-reliability",
        pair="lost-result-idempotency",
    )

    assert selected.cell_ids == ["lost.retry", "lost.idempotent"]
    assert {cell.arm for cell in selected.cells} == {"baseline", "mechanism"}
    comparison = validate_comparison(
        suites["runtime-reliability-v1"], "lost-result-idempotency"
    )
    assert comparison.allowed_config_differences == ["mode", "idempotency_enabled"]

    with pytest.raises(EvalSuiteValidationError, match="unsupported_filter"):
        select_eval_cells(
            suites,
            "runtime-reliability",
            environment="workspace",
        )


def test_suite_schema_rejects_duplicate_cell_and_pair_identity_drift() -> None:
    suite = load_eval_suites()["runtime-reliability-v1"]
    duplicate = suite.model_dump(mode="json")
    duplicate["cells"].append(copy.deepcopy(duplicate["cells"][0]))
    with pytest.raises(ValidationError, match="duplicate_eval_cell"):
        EvalSuiteManifest.model_validate_json(json.dumps(duplicate))

    changed = suite.model_dump(mode="json")
    changed["cells"][1]["runtime_config"]["max_tool_calls"] = 99
    changed_suite = EvalSuiteManifest.model_validate_json(json.dumps(changed))
    with pytest.raises(EvalSuiteValidationError, match="pair_config_mismatch"):
        validate_comparison(changed_suite, "transient-retry")
