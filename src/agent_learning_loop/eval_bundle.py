"""Deterministic JSON/JSONL/Markdown rendering for M5A Eval bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.eval_schemas import (
    EvalBundleManifest,
    EvalSummary,
    ExactRatio,
    NormalizedEvalRecord,
)


def canonical_json_text(value: object) -> str:
    """Render stable human-inspectable JSON with a final newline."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def records_jsonl_text(records: list[NormalizedEvalRecord]) -> str:
    return "".join(
        json.dumps(
            record.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
        for record in records
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_bundle_fingerprint(manifest: EvalBundleManifest | dict[str, object]) -> str:
    if isinstance(manifest, EvalBundleManifest):
        payload = manifest.model_dump(mode="json", exclude={"bundle_fingerprint"})
    else:
        payload = dict(manifest)
        payload.pop("bundle_fingerprint", None)
    return canonical_sha256(payload)


def _format_ratio(value: ExactRatio | Literal["N/A"]) -> str:
    if value == "N/A":
        return "N/A"
    if not isinstance(value, ExactRatio):
        raise TypeError("unknown_ratio_value")
    return f"{value.numerator}/{value.denominator} ({value.rate:.6g})"


def render_eval_report(manifest: EvalBundleManifest, summary: EvalSummary) -> str:
    """Render Markdown only from the same validated manifest and summary models."""
    lines = [
        "# Eval bundle",
        "",
        f"- source commit: `{manifest.source_commit}`",
        f"- suites: `{', '.join(summary.suite_ids)}`",
        (
            "- selected denominator: "
            f"`{manifest.selection.selected_total}/{manifest.selection.candidate_total}`"
        ),
        f"- selected environment: `{manifest.selection.environment or 'all'}`",
        f"- selected split: `{manifest.selection.split or 'all'}`",
        f"- selected tag: `{manifest.selection.tag or 'all'}`",
        f"- selected pair: `{manifest.selection.pair or 'all'}`",
        "",
        "## Aggregate exact metrics",
        "",
        f"- verifier state success: `{_format_ratio(summary.verifier_state_success)}`",
        f"- Runtime completion: `{_format_ratio(summary.runtime_completion_success)}`",
        f"- duplicate side effects: `{_format_ratio(summary.duplicate_side_effects)}`",
        f"- physical executions: `{_format_ratio(summary.physical_executions)}`",
        f"- physical writes: `{_format_ratio(summary.physical_writes)}`",
        f"- retries: `{_format_ratio(summary.retries)}`",
        f"- idempotency hits: `{_format_ratio(summary.idempotency_hits)}`",
        f"- model: {summary.model}",
        f"- token cost: {summary.token_cost}",
        f"- latency: {summary.latency}",
        "",
        "## System correctness by environment",
        "",
    ]
    environment_slices = [
        item for item in summary.system_slices if item.dimension == "environment"
    ]
    if environment_slices:
        lines.extend(["| environment | verifier passed |", "|---|---:|"])
        lines.extend(
            f"| {item.value} | {item.verifier_passed}/{item.selected} |"
            for item in environment_slices
        )
    else:
        lines.append("No system-correctness environment is selected in this bundle.")
    lines.extend(["", "## System correctness by split", ""])
    split_slices = [item for item in summary.system_slices if item.dimension == "split"]
    if split_slices:
        lines.extend(["| split | verifier passed |", "|---|---:|"])
        lines.extend(
            f"| {item.value} | {item.verifier_passed}/{item.selected} |"
            for item in split_slices
        )
    else:
        lines.append("No system-correctness split is selected in this bundle.")
    if manifest.selection.tag is not None:
        lines.extend(["", "## Selected system tag", ""])
        tag_slices = [
            item
            for item in summary.system_slices
            if item.dimension == "tag" and item.value == manifest.selection.tag
        ]
        if tag_slices:
            item = tag_slices[0]
            lines.append(
                f"`{item.value}`: {item.verifier_passed}/{item.selected} verifier passed."
            )
        else:
            lines.append("The selected bundle contains no system-correctness tag slice.")
    lines.extend(
        [
        "",
        "## Reliability cells",
        "",
        ]
    )
    if summary.reliability_cells:
        lines.extend(
            [
                "| cell | terminal | state | completion | executions | writes | duplicates | "
                "retries | hits | error |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        lines.extend(
            "| "
            f"{item.cell_id} | {item.terminal} | {str(item.verifier_state_success).lower()} | "
            f"{str(item.runtime_completion_success).lower()} | "
            f"{item.physical_executions} | {item.physical_writes} | "
            f"{item.duplicate_side_effects} | {item.retries} | {item.idempotency_hits} | "
            f"{item.error_category or 'N/A'} |"
            for item in summary.reliability_cells
        )
    else:
        lines.append("No Runtime reliability cell is selected in this bundle.")
    lines.extend(
        [
        "",
        "## Paired comparisons",
        "",
        ]
    )
    if summary.pair_deltas:
        lines.extend(
            [
                "| pair | completion Δ | verifier Δ | physical execution Δ | "
                "physical write Δ | duplicate Δ | retry Δ | idempotency-hit Δ |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        lines.extend(
                "| "
                f"{item.comparison_id} | {item.completion_delta} | "
                f"{item.verifier_delta} | {item.physical_execution_delta} | "
                f"{item.physical_write_delta} | {item.duplicate_side_effect_delta} | "
                f"{item.retry_delta} | {item.idempotency_hit_delta} |"
            for item in summary.pair_deltas
        )
    else:
        lines.append("No paired comparison is selected in this bundle.")
    lines.extend(["", "## Diagnostics", ""])
    if summary.diagnostics:
        lines.extend(
            f"- `{item.cell_id}`: {'passed' if item.passed else 'failed'} — {item.detail}"
            for item in summary.diagnostics
        )
    else:
        lines.append("No recovery/replay diagnostic is selected in this bundle.")
    lines.extend(["", "## Oracle deviations", ""])
    if summary.oracle_failure_cell_ids:
        lines.extend(
            f"- `{item.cell_id}`: `{item.error_category}` "
            f"(raw: `{item.raw_result_path}`)"
            for item in summary.oracle_failures
        )
    else:
        lines.append("No selected cell deviated from its pre-registered oracle.")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "Scripted system-correctness cells do not measure Agent or model capability.",
            "Expected naive Runtime failures remain in the denominator and can satisfy the oracle.",
            "The replay result is a fixed 1/1 vertical-slice diagnostic, not an aggregate rate.",
            "Single-run latency is observed/non-comparable; no p50 or p95 is reported.",
            "SHA-256 detects damage and inconsistent artifacts; it is not a signature.",
            "The source commit was explicitly supplied by the caller; M5B will attribute the "
            "canonical run to a checked Git revision.",
            "",
        ]
    )
    return "\n".join(lines)
