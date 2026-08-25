"""Recompute M5A metrics from normalized records, never saved summaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Literal

from agent_learning_loop.eval_schemas import (
    DiagnosticSummary,
    EvalComparison,
    EvalSummary,
    ExactRatio,
    NormalizedEvalRecord,
    OracleFailureSummary,
    PairDelta,
    ReliabilityCellSummary,
    SystemSliceSummary,
)


def _ratio(values: list[int]) -> ExactRatio | Literal["N/A"]:
    if not values:
        return "N/A"
    return ExactRatio(
        numerator=sum(values),
        denominator=len(values),
        rate=sum(values) / len(values),
    )


def aggregate_eval_records(
    records: list[NormalizedEvalRecord],
    comparisons: Iterable[EvalComparison],
) -> EvalSummary:
    """Build exact counts and deltas from one unique record per selected cell."""
    if not records:
        raise ValueError("empty_eval_records")
    by_cell = {record.cell_id: record for record in records}
    if len(by_cell) != len(records):
        raise ValueError("duplicate_record_cell")

    pair_deltas: list[PairDelta] = []
    for comparison in comparisons:
        baseline = by_cell.get(comparison.baseline_cell_id)
        mechanism = by_cell.get(comparison.mechanism_cell_id)
        if (baseline is None) != (mechanism is None):
            raise ValueError("incomplete_pair")
        if baseline is None or mechanism is None:
            continue
        if (
            baseline.pair_id != comparison.comparison_id
            or mechanism.pair_id != comparison.comparison_id
            or baseline.arm != "baseline"
            or mechanism.arm != "mechanism"
        ):
            raise ValueError("pair_record_identity")
        if not isinstance(baseline.runtime_completion_success, bool) or not isinstance(
            mechanism.runtime_completion_success, bool
        ):
            raise ValueError("pair_runtime_status")
        if not isinstance(baseline.verifier_state_success, bool) or not isinstance(
            mechanism.verifier_state_success, bool
        ):
            raise ValueError("pair_verifier_status")
        required = (
            baseline.duplicate_side_effects,
            mechanism.duplicate_side_effects,
            baseline.physical_executions,
            mechanism.physical_executions,
            baseline.physical_write_executions,
            mechanism.physical_write_executions,
            baseline.retries,
            mechanism.retries,
            baseline.idempotency_hits,
            mechanism.idempotency_hits,
        )
        if any(value is None for value in required):
            raise ValueError("pair_usage_missing")
        pair_deltas.append(
            PairDelta(
                comparison_id=comparison.comparison_id,
                baseline_cell_id=baseline.cell_id,
                mechanism_cell_id=mechanism.cell_id,
                completion_delta=int(mechanism.runtime_completion_success)
                - int(baseline.runtime_completion_success),
                verifier_delta=int(mechanism.verifier_state_success)
                - int(baseline.verifier_state_success),
                duplicate_side_effect_delta=(mechanism.duplicate_side_effects or 0)
                - (baseline.duplicate_side_effects or 0),
                physical_execution_delta=(mechanism.physical_executions or 0)
                - (baseline.physical_executions or 0),
                physical_write_delta=(mechanism.physical_write_executions or 0)
                - (baseline.physical_write_executions or 0),
                retry_delta=(mechanism.retries or 0) - (baseline.retries or 0),
                idempotency_hit_delta=(mechanism.idempotency_hits or 0)
                - (baseline.idempotency_hits or 0),
            )
        )

    verifier_values = [
        int(record.verifier_state_success)
        for record in records
        if isinstance(record.verifier_state_success, bool)
    ]
    if not verifier_values:
        raise ValueError("empty_verifier_denominator")
    runtime_values = [
        int(record.runtime_completion_success)
        for record in records
        if isinstance(record.runtime_completion_success, bool)
    ]
    system_records = [record for record in records if record.kind == "system"]
    slices: list[SystemSliceSummary] = []
    dimensions: tuple[Literal["environment", "split", "tag"], ...] = (
        "environment",
        "split",
        "tag",
    )
    for dimension in dimensions:
        grouped: dict[str, list[NormalizedEvalRecord]] = defaultdict(list)
        for record in system_records:
            values = record.tags if dimension == "tag" else [getattr(record, dimension)]
            for value in values:
                if value is not None:
                    grouped[str(value)].append(record)
        for value, grouped_records in sorted(grouped.items()):
            slices.append(
                SystemSliceSummary(
                    dimension=dimension,
                    value=value,
                    selected=len(grouped_records),
                    verifier_passed=sum(
                        record.verifier_state_success is True for record in grouped_records
                    ),
                )
            )

    diagnostics = [
        DiagnosticSummary(
            cell_id=record.cell_id,
            diagnostic=record.diagnostic or "unknown",
            passed=record.cell_contract_passed,
            detail=(
                "1/1 vertical-slice diagnostic"
                if record.diagnostic == "action_replay"
                else "fixed recovery diagnostic"
            ),
        )
        for record in records
        if record.kind == "recovery"
    ]
    suite_ids = list(dict.fromkeys(record.suite_id for record in records))
    reliability_cells = [
        ReliabilityCellSummary(
            cell_id=record.cell_id,
            pair_id=record.pair_id,
            arm=record.arm or "context",
            terminal=record.terminal,
            error_category=record.error_category,
            verifier_state_success=record.verifier_state_success is True,
            runtime_completion_success=record.runtime_completion_success is True,
            physical_executions=record.physical_executions or 0,
            physical_writes=record.physical_write_executions or 0,
            duplicate_side_effects=record.duplicate_side_effects or 0,
            retries=record.retries or 0,
            idempotency_hits=record.idempotency_hits or 0,
        )
        for record in records
        if record.kind == "reliability"
    ]
    return EvalSummary(
        suite_ids=suite_ids,
        selected_total=len(records),
        verifier_state_success=ExactRatio(
            numerator=sum(verifier_values),
            denominator=len(verifier_values),
            rate=sum(verifier_values) / len(verifier_values),
        ),
        runtime_completion_success=_ratio(runtime_values),
        duplicate_side_effects=_ratio(
            [
                record.duplicate_side_effects
                for record in records
                if record.duplicate_side_effects is not None
            ]
        ),
        physical_executions=_ratio(
            [
                record.physical_executions
                for record in records
                if record.physical_executions is not None
            ]
        ),
        physical_writes=_ratio(
            [
                record.physical_write_executions
                for record in records
                if record.physical_write_executions is not None
            ]
        ),
        retries=_ratio(
            [record.retries for record in records if record.retries is not None]
        ),
        idempotency_hits=_ratio(
            [
                record.idempotency_hits
                for record in records
                if record.idempotency_hits is not None
            ]
        ),
        system_slices=slices,
        reliability_cells=reliability_cells,
        pair_deltas=pair_deltas,
        diagnostics=diagnostics,
        oracle_failure_cell_ids=sorted(
            record.cell_id for record in records if not record.cell_contract_passed
        ),
        oracle_failures=[
            OracleFailureSummary(
                cell_id=record.cell_id,
                error_category=record.error_category or "oracle_deviation",
                raw_result_path=record.raw_result_path,
            )
            for record in sorted(records, key=lambda item: item.cell_id)
            if not record.cell_contract_passed
        ],
    )
