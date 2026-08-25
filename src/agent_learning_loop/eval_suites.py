"""Load and cross-check the three pre-registered M5A Eval suites."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.corpus import CorpusSplit, validate_workspace_corpus
from agent_learning_loop.dataops_corpus import validate_dataops_corpus
from agent_learning_loop.eval_schemas import (
    EnvironmentName,
    EvalCell,
    EvalComparison,
    EvalProvenance,
    EvalSelectionSpec,
    EvalSuiteManifest,
    RecoveryEvalCell,
    RecoveryOracle,
    ReliabilityEvalCell,
    ReliabilityOracle,
    StatusValue,
    SuiteId,
    SuiteSelector,
    SystemEvalCell,
    SystemOracle,
)
from agent_learning_loop.failure_schedules import (
    fingerprint_schedule,
    load_failure_schedule,
)
from agent_learning_loop.incident_corpus import validate_incident_corpus
from agent_learning_loop.interruption_schedules import (
    fingerprint_interruption_schedule,
    load_interruption_schedule,
)
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode, RuntimeState

CANONICAL_EVAL_SUITE_FINGERPRINTS = {
    "system-correctness-v1": (
        "624dfb19c2b9575056dd9d24a92e3dcb4852617eb538ee3541fb28cae933488e"
    ),
    "runtime-reliability-v1": (
        "a8c5e2389ce1bbe31ae7895ecbfe211be3460aee40563c4c13efb0523d89ac2e"
    ),
    "recovery-replay-v1": (
        "4fb499de8c42ac2d78aaa962c6e6fda2419e7df5fda45c64287d5b78d23b9a97"
    ),
}


class EvalSuiteValidationError(ValueError):
    """A stable fail-closed category for suite or selection drift."""


@dataclass(frozen=True)
class SelectedEvalCells:
    spec: EvalSelectionSpec
    cells: tuple[EvalCell, ...]

    @property
    def candidate_total(self) -> int:
        return self.spec.candidate_total

    @property
    def selected_total(self) -> int:
        return self.spec.selected_total

    @property
    def cell_ids(self) -> list[str]:
        return list(self.spec.cell_ids)


def _runtime_config(mode: RuntimeMode, schedule_id: str) -> RuntimeConfig:
    schedule = load_failure_schedule(schedule_id)
    return RuntimeConfig.for_mode(
        mode,
        schedule_id=schedule_id,
        seed=schedule.seed,
        schedule_fingerprint=fingerprint_schedule(schedule),
        max_steps=8,
        max_tool_calls=12,
        timeout_seconds=30.0,
        retry_backoff_seconds=[0.0],
    )


def _system_cells() -> list[SystemEvalCell]:
    cells: list[SystemEvalCell] = []
    corpora = (
        ("workspace", validate_workspace_corpus()),
        ("incident", validate_incident_corpus()),
        ("dataops", validate_dataops_corpus()),
    )
    for environment, corpus in corpora:
        for manifest in corpus.manifests:
            cells.append(
                SystemEvalCell(
                    cell_id=f"system.{manifest.task_id}",
                    task_id=manifest.task_id,
                    environment=cast(EnvironmentName, environment),
                    split=manifest.split,
                    tags=list(manifest.tags),
                    seed=manifest.seed,
                    resource_id=manifest.manifest_id,
                    resource_fingerprint=canonical_sha256(
                        manifest.model_dump(mode="json")
                    ),
                    fixture_id=manifest.fixture_id,
                    fixture_fingerprint=manifest.fixture_fingerprint,
                    catalog_id=manifest.catalog_id,
                    catalog_fingerprint=manifest.catalog_fingerprint,
                    oracle=SystemOracle(),
                    provenance=EvalProvenance(),
                    limitation=(
                        "Scripted system-correctness cell; not an Agent or model "
                        "capability result."
                    ),
                )
            )
    return sorted(cells, key=lambda item: item.cell_id)


def _reliability_cell(
    *,
    cell_id: str,
    task_id: str,
    schedule_id: str,
    mode: RuntimeMode,
    pair_id: str | None,
    arm: Literal["baseline", "mechanism", "context"],
    terminal_state: RuntimeState,
    verifier_state_success: bool,
    error_category: str | None,
    steps: int,
    tool_calls: int,
    physical_executions: int,
    physical_write_executions: int,
    side_effect_executions: int,
    duplicate_side_effects: int,
    retries: int,
    idempotency_hits: int,
    tags: list[str],
) -> ReliabilityEvalCell:
    manifest = next(
        item
        for item in validate_workspace_corpus().manifests
        if item.task_id == task_id
    )
    schedule = load_failure_schedule(schedule_id)
    config = _runtime_config(mode, schedule_id)
    return ReliabilityEvalCell(
        cell_id=cell_id,
        task_id=task_id,
        tags=tags,
        seed=schedule.seed,
        resource_id=manifest.manifest_id,
        resource_fingerprint=canonical_sha256(manifest.model_dump(mode="json")),
        fixture_id=manifest.fixture_id,
        fixture_fingerprint=manifest.fixture_fingerprint,
        catalog_id=manifest.catalog_id,
        catalog_fingerprint=manifest.catalog_fingerprint,
        schedule_id=schedule_id,
        schedule_fingerprint=fingerprint_schedule(schedule),
        runtime_config=config,
        pair_id=pair_id,
        arm=arm,
        oracle=ReliabilityOracle(
            verifier_state_success=verifier_state_success,
            runtime_completion_success=terminal_state is RuntimeState.SUCCEEDED,
            terminal_state=terminal_state,
            error_category=error_category,
            steps=steps,
            tool_calls=tool_calls,
            physical_executions=physical_executions,
            physical_write_executions=physical_write_executions,
            side_effect_executions=side_effect_executions,
            duplicate_side_effects=duplicate_side_effects,
            retries=retries,
            idempotency_hits=idempotency_hits,
        ),
        provenance=EvalProvenance(),
        limitation=(
            "One deterministic M2 Runtime cell; expected naive failure is data, "
            "not infrastructure failure."
        ),
    )


def _reliability_cells() -> list[ReliabilityEvalCell]:
    return [
        _reliability_cell(
            cell_id="transient.naive",
            task_id="workspace.build-summary",
            schedule_id="workspace.transient-read.v1",
            mode=RuntimeMode.NAIVE,
            pair_id="transient-retry",
            arm="baseline",
            terminal_state=RuntimeState.FAILED,
            verifier_state_success=False,
            error_category="tool_transient",
            steps=1,
            tool_calls=1,
            physical_executions=0,
            physical_write_executions=0,
            side_effect_executions=0,
            duplicate_side_effects=0,
            retries=0,
            idempotency_hits=0,
            tags=["runtime", "transient", "retry"],
        ),
        _reliability_cell(
            cell_id="transient.retry",
            task_id="workspace.build-summary",
            schedule_id="workspace.transient-read.v1",
            mode=RuntimeMode.RETRY_ONLY,
            pair_id="transient-retry",
            arm="mechanism",
            terminal_state=RuntimeState.SUCCEEDED,
            verifier_state_success=True,
            error_category=None,
            steps=4,
            tool_calls=4,
            physical_executions=3,
            physical_write_executions=1,
            side_effect_executions=1,
            duplicate_side_effects=0,
            retries=1,
            idempotency_hits=0,
            tags=["runtime", "transient", "retry"],
        ),
        _reliability_cell(
            cell_id="timeout.naive",
            task_id="workspace.update-status",
            schedule_id="workspace.logical-timeout.v1",
            mode=RuntimeMode.NAIVE,
            pair_id="timeout-retry",
            arm="baseline",
            terminal_state=RuntimeState.TIMED_OUT,
            verifier_state_success=False,
            error_category="timeout",
            steps=2,
            tool_calls=2,
            physical_executions=1,
            physical_write_executions=0,
            side_effect_executions=0,
            duplicate_side_effects=0,
            retries=0,
            idempotency_hits=0,
            tags=["runtime", "logical-timeout", "retry"],
        ),
        _reliability_cell(
            cell_id="timeout.retry",
            task_id="workspace.update-status",
            schedule_id="workspace.logical-timeout.v1",
            mode=RuntimeMode.RETRY_ONLY,
            pair_id="timeout-retry",
            arm="mechanism",
            terminal_state=RuntimeState.SUCCEEDED,
            verifier_state_success=True,
            error_category=None,
            steps=4,
            tool_calls=4,
            physical_executions=3,
            physical_write_executions=1,
            side_effect_executions=1,
            duplicate_side_effects=0,
            retries=1,
            idempotency_hits=0,
            tags=["runtime", "logical-timeout", "retry"],
        ),
        _reliability_cell(
            cell_id="lost.naive",
            task_id="workspace.fix-config",
            schedule_id="workspace.lost-write-result.v1",
            mode=RuntimeMode.NAIVE,
            pair_id=None,
            arm="context",
            terminal_state=RuntimeState.FAILED,
            verifier_state_success=True,
            error_category="tool_transient",
            steps=2,
            tool_calls=2,
            physical_executions=2,
            physical_write_executions=1,
            side_effect_executions=1,
            duplicate_side_effects=0,
            retries=0,
            idempotency_hits=0,
            tags=["runtime", "lost-result", "state-completion-split"],
        ),
        _reliability_cell(
            cell_id="lost.retry",
            task_id="workspace.fix-config",
            schedule_id="workspace.lost-write-result.v1",
            mode=RuntimeMode.RETRY_ONLY,
            pair_id="lost-result-idempotency",
            arm="baseline",
            terminal_state=RuntimeState.SUCCEEDED,
            verifier_state_success=True,
            error_category=None,
            steps=3,
            tool_calls=3,
            physical_executions=3,
            physical_write_executions=2,
            side_effect_executions=2,
            duplicate_side_effects=1,
            retries=1,
            idempotency_hits=0,
            tags=["runtime", "lost-result", "idempotency"],
        ),
        _reliability_cell(
            cell_id="lost.idempotent",
            task_id="workspace.fix-config",
            schedule_id="workspace.lost-write-result.v1",
            mode=RuntimeMode.SAFEGUARDED,
            pair_id="lost-result-idempotency",
            arm="mechanism",
            terminal_state=RuntimeState.SUCCEEDED,
            verifier_state_success=True,
            error_category=None,
            steps=3,
            tool_calls=3,
            physical_executions=2,
            physical_write_executions=1,
            side_effect_executions=1,
            duplicate_side_effects=0,
            retries=1,
            idempotency_hits=1,
            tags=["runtime", "lost-result", "idempotency"],
        ),
    ]


def _recovery_cell(
    *,
    cell_id: str,
    diagnostic: Literal["checkpoint_off", "checkpoint_on", "reference", "action_replay"],
    checkpointing: Literal["on", "off"],
    interrupted: bool,
    record_actions: bool,
    verifier_state_success: StatusValue,
    runtime_completion_success: StatusValue,
    terminal: str,
    tags: list[str],
) -> RecoveryEvalCell:
    manifest = next(
        item
        for item in validate_workspace_corpus().manifests
        if item.task_id == "workspace.fix-config"
    )
    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    interruption = load_interruption_schedule("workspace.post-write-boundary.v1")
    config = _runtime_config(RuntimeMode.SAFEGUARDED, schedule.schedule_id)
    identity = {
        "task_id": "workspace.fix-config",
        "fixture_id": manifest.fixture_id,
        "fixture_fingerprint": manifest.fixture_fingerprint,
        "catalog_id": manifest.catalog_id,
        "catalog_fingerprint": manifest.catalog_fingerprint,
        "schedule_id": schedule.schedule_id,
        "schedule_fingerprint": fingerprint_schedule(schedule),
        "interruption_schedule_id": interruption.schedule_id if interrupted else None,
        "interruption_schedule_fingerprint": (
            fingerprint_interruption_schedule(interruption) if interrupted else None
        ),
        "runtime_config": config.model_dump(mode="json"),
        "checkpointing": checkpointing,
        "record_actions": record_actions,
        "diagnostic": diagnostic,
    }
    return RecoveryEvalCell(
        cell_id=cell_id,
        diagnostic=diagnostic,
        tags=tags,
        seed=schedule.seed,
        resource_id=f"{cell_id}.v1",
        resource_fingerprint=canonical_sha256(identity),
        fixture_id=manifest.fixture_id,
        fixture_fingerprint=manifest.fixture_fingerprint,
        catalog_id=manifest.catalog_id,
        catalog_fingerprint=manifest.catalog_fingerprint,
        schedule_id=schedule.schedule_id,
        schedule_fingerprint=fingerprint_schedule(schedule),
        interruption_schedule_id=interruption.schedule_id if interrupted else None,
        interruption_schedule_fingerprint=(
            fingerprint_interruption_schedule(interruption) if interrupted else None
        ),
        runtime_config=config,
        checkpointing=checkpointing,
        record_actions=record_actions,
        oracle=RecoveryOracle(
            verifier_state_success=verifier_state_success,
            runtime_completion_success=runtime_completion_success,
            terminal=terminal,
        ),
        provenance=EvalProvenance(),
        limitation=(
            "Fixed M3 recovery/replay diagnostic; no general recovery or replay rate claim."
        ),
    )


def _recovery_cells() -> list[RecoveryEvalCell]:
    return [
        _recovery_cell(
            cell_id="recovery.checkpoint-off",
            diagnostic="checkpoint_off",
            checkpointing="off",
            interrupted=True,
            record_actions=False,
            verifier_state_success="N/A",
            runtime_completion_success=False,
            terminal="CONTROLLED_INTERRUPTION_NO_CHECKPOINT",
            tags=["recovery", "checkpoint", "expected-refusal"],
        ),
        _recovery_cell(
            cell_id="recovery.checkpoint-on",
            diagnostic="checkpoint_on",
            checkpointing="on",
            interrupted=True,
            record_actions=False,
            verifier_state_success=True,
            runtime_completion_success=True,
            terminal="SUCCEEDED",
            tags=["recovery", "checkpoint", "second-process"],
        ),
        _recovery_cell(
            cell_id="recovery.reference",
            diagnostic="reference",
            checkpointing="off",
            interrupted=False,
            record_actions=False,
            verifier_state_success=True,
            runtime_completion_success=True,
            terminal="SUCCEEDED",
            tags=["recovery", "reference"],
        ),
        _recovery_cell(
            cell_id="recovery.action-replay",
            diagnostic="action_replay",
            checkpointing="off",
            interrupted=False,
            record_actions=True,
            verifier_state_success=True,
            runtime_completion_success="N/A",
            terminal="MATCHED",
            tags=["replay", "vertical-slice"],
        ),
    ]


def _suite(
    suite_id: SuiteId,
    cells: list[EvalCell],
    comparisons: list[EvalComparison],
    limitation: str,
) -> EvalSuiteManifest:
    payload = {
        "schema_version": "1",
        "manifest_id": f"{suite_id}.manifest.v1",
        "suite_id": suite_id,
        "suite_version": 1,
        "cells": [cell.model_dump(mode="json") for cell in cells],
        "comparisons": [item.model_dump(mode="json") for item in comparisons],
        "provenance": EvalProvenance().model_dump(mode="json"),
        "limitation": limitation,
    }
    fingerprint = canonical_sha256(payload)
    return EvalSuiteManifest.model_validate_json(
        json.dumps({**payload, "manifest_fingerprint": fingerprint})
    )


def expected_eval_suites() -> dict[str, EvalSuiteManifest]:
    """Build the registered truth from frozen corpus and schedule identities."""
    comparisons = [
        EvalComparison(
            comparison_id="transient-retry",
            baseline_cell_id="transient.naive",
            mechanism_cell_id="transient.retry",
            allowed_config_differences=[
                "mode",
                "retry_enabled",
                "max_attempts",
                "retry_backoff_seconds",
            ],
        ),
        EvalComparison(
            comparison_id="timeout-retry",
            baseline_cell_id="timeout.naive",
            mechanism_cell_id="timeout.retry",
            allowed_config_differences=[
                "mode",
                "retry_enabled",
                "max_attempts",
                "retry_backoff_seconds",
            ],
        ),
        EvalComparison(
            comparison_id="lost-result-idempotency",
            baseline_cell_id="lost.retry",
            mechanism_cell_id="lost.idempotent",
            allowed_config_differences=["mode", "idempotency_enabled"],
        ),
    ]
    suites = [
        _suite(
            "system-correctness-v1",
            [cast(EvalCell, cell) for cell in _system_cells()],
            [],
            (
                "Thirty scripted cells validate system contracts; they do not measure "
                "model capability."
            ),
        ),
        _suite(
            "runtime-reliability-v1",
            [cast(EvalCell, cell) for cell in _reliability_cells()],
            comparisons,
            "Seven deterministic M2 cells isolate retry and idempotency; no performance claim.",
        ),
        _suite(
            "recovery-replay-v1",
            [cast(EvalCell, cell) for cell in _recovery_cells()],
            [],
            "Four fixed M3 diagnostics include one 1/1 replay slice; no aggregate replay rate.",
        ),
    ]
    return {suite.suite_id: suite for suite in suites}


def load_eval_suites() -> dict[str, EvalSuiteManifest]:
    """Load packaged JSON and compare every field to the frozen registered truth."""
    try:
        resource = files("agent_learning_loop").joinpath("eval_suites", "suites-v1.json")
        suites = TypeAdapter(list[EvalSuiteManifest]).validate_json(
            resource.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise EvalSuiteValidationError("invalid_eval_suite_resource") from exc
    by_id: dict[str, EvalSuiteManifest] = {
        suite.suite_id: suite for suite in suites
    }
    if len(by_id) != 3 or set(by_id) != set(CANONICAL_EVAL_SUITE_FINGERPRINTS):
        raise EvalSuiteValidationError("eval_suite_identity")
    expected = expected_eval_suites()
    for suite_id, suite in by_id.items():
        payload = suite.model_dump(mode="json", exclude={"manifest_fingerprint"})
        if (
            suite.manifest_fingerprint != canonical_sha256(payload)
            or suite.manifest_fingerprint
            != CANONICAL_EVAL_SUITE_FINGERPRINTS[suite_id]
            or suite != expected[suite_id]
        ):
            raise EvalSuiteValidationError("eval_suite_fingerprint")
    return by_id


def validate_comparison(
    suite: EvalSuiteManifest, comparison_id: str
) -> EvalComparison:
    comparison = next(
        (item for item in suite.comparisons if item.comparison_id == comparison_id),
        None,
    )
    if comparison is None:
        raise EvalSuiteValidationError("unknown_pair")
    by_id = {cell.cell_id: cell for cell in suite.cells}
    baseline = by_id[comparison.baseline_cell_id]
    mechanism = by_id[comparison.mechanism_cell_id]
    if not isinstance(baseline, ReliabilityEvalCell) or not isinstance(
        mechanism, ReliabilityEvalCell
    ):
        raise EvalSuiteValidationError("pair_cell_kind")
    identity_fields = (
        "task_id",
        "environment",
        "seed",
        "resource_id",
        "resource_fingerprint",
        "fixture_id",
        "fixture_fingerprint",
        "catalog_id",
        "catalog_fingerprint",
        "schedule_id",
        "schedule_fingerprint",
    )
    if any(getattr(baseline, field) != getattr(mechanism, field) for field in identity_fields):
        raise EvalSuiteValidationError("pair_identity_mismatch")
    base_config = baseline.runtime_config.model_dump(mode="json")
    mechanism_config = mechanism.runtime_config.model_dump(mode="json")
    differing = {
        key for key in base_config if base_config[key] != mechanism_config[key]
    }
    if differing != set(comparison.allowed_config_differences):
        raise EvalSuiteValidationError("pair_config_mismatch")
    return comparison


def select_eval_cells(
    suites: dict[str, EvalSuiteManifest],
    suite: SuiteSelector,
    *,
    environment: EnvironmentName | None = None,
    split: CorpusSplit | None = None,
    tag: str | None = None,
    pair: str | None = None,
) -> SelectedEvalCells:
    if suite == "all":
        if any(value is not None for value in (environment, split, tag, pair)):
            raise EvalSuiteValidationError("unsupported_filter")
        cells = tuple(
            cell
            for suite_id in (
                "system-correctness-v1",
                "runtime-reliability-v1",
                "recovery-replay-v1",
            )
            for cell in suites[suite_id].cells
        )
        return _selected(suite, cells, 41)
    suite_id = {
        "system-correctness": "system-correctness-v1",
        "runtime-reliability": "runtime-reliability-v1",
        "recovery-replay": "recovery-replay-v1",
    }[suite]
    manifest = suites[suite_id]
    if suite == "system-correctness":
        if pair is not None:
            raise EvalSuiteValidationError("unsupported_filter")
        known_tags = {value for cell in manifest.cells for value in cell.tags}
        if tag is not None and tag not in known_tags:
            raise EvalSuiteValidationError("unknown_tag")
        cells = tuple(
            cell
            for cell in manifest.cells
            if (environment is None or cell.environment == environment)
            and (split is None or cell.split == split)
            and (tag is None or tag in cell.tags)
        )
    elif suite == "runtime-reliability":
        if any(value is not None for value in (environment, split, tag)):
            raise EvalSuiteValidationError("unsupported_filter")
        if pair is None:
            cells = tuple(manifest.cells)
            for comparison in manifest.comparisons:
                validate_comparison(manifest, comparison.comparison_id)
        else:
            comparison = validate_comparison(manifest, pair)
            by_id = {cell.cell_id: cell for cell in manifest.cells}
            cells = (
                by_id[comparison.baseline_cell_id],
                by_id[comparison.mechanism_cell_id],
            )
    else:
        if any(value is not None for value in (environment, split, tag, pair)):
            raise EvalSuiteValidationError("unsupported_filter")
        cells = tuple(manifest.cells)
    if not cells:
        raise EvalSuiteValidationError("empty_selection")
    return _selected(
        suite,
        cells,
        len(manifest.cells),
        environment=environment,
        split=split,
        tag=tag,
        pair=pair,
    )


def _selected(
    suite: SuiteSelector,
    cells: tuple[EvalCell, ...],
    candidate_total: int,
    *,
    environment: EnvironmentName | None = None,
    split: CorpusSplit | None = None,
    tag: str | None = None,
    pair: str | None = None,
) -> SelectedEvalCells:
    spec = EvalSelectionSpec(
        suite=suite,
        environment=environment,
        split=split,
        tag=tag,
        pair=pair,
        candidate_total=candidate_total,
        selected_total=len(cells),
        cell_ids=[cell.cell_id for cell in cells],
    )
    return SelectedEvalCells(spec=spec, cells=cells)
