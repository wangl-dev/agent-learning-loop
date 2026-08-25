"""Strict M5A contracts for pre-registered Eval suites and bundles."""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from agent_learning_loop.corpus import CorpusSplit
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeState
from agent_learning_loop.schemas import StrictModel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SourceCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Identity = Annotated[str, Field(min_length=1, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
RelativePath = Annotated[str, Field(min_length=1)]
StatusValue: TypeAlias = bool | Literal["N/A"]
EnvironmentName = Literal["workspace", "incident", "dataops"]
SuiteId = Literal[
    "system-correctness-v1",
    "runtime-reliability-v1",
    "recovery-replay-v1",
]
SuiteSelector = Literal[
    "system-correctness",
    "runtime-reliability",
    "recovery-replay",
    "all",
]
BUNDLE_DIGEST_LIMITATION = (
    "SHA-256 detects damage and inconsistent artifacts; it is not a signature against "
    "an actor who can rewrite the whole bundle."
)


def _validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or ":" in normalized.split("/", maxsplit=1)[0]
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("unsafe_relative_path")
    return value


class EvalProvenance(StrictModel):
    source: Literal["project-authored-synthetic"] = "project-authored-synthetic"
    license: Literal["Apache-2.0"] = "Apache-2.0"


class SystemOracle(StrictModel):
    verifier_state_success: Literal[True] = True
    runtime_completion_success: Literal["N/A"] = "N/A"
    outcome: Literal["passed"] = "passed"


class ReliabilityOracle(StrictModel):
    verifier_state_success: bool
    runtime_completion_success: bool
    terminal_state: RuntimeState
    error_category: str | None = None
    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    physical_executions: int = Field(ge=0)
    physical_write_executions: int | None = Field(default=None, ge=0)
    side_effect_executions: int = Field(ge=0)
    duplicate_side_effects: int = Field(ge=0)
    retries: int = Field(ge=0)
    idempotency_hits: int = Field(ge=0)


class RecoveryOracle(StrictModel):
    diagnostic_passed: Literal[True] = True
    verifier_state_success: StatusValue
    runtime_completion_success: StatusValue
    terminal: str = Field(min_length=1)


class SystemEvalCell(StrictModel):
    schema_version: Literal["1"] = "1"
    kind: Literal["system"] = "system"
    suite_id: Literal["system-correctness-v1"] = "system-correctness-v1"
    cell_id: Identity
    task_id: Identity
    environment: EnvironmentName
    split: CorpusSplit
    tags: list[str] = Field(min_length=1)
    seed: int = Field(ge=0)
    resource_id: Identity
    resource_fingerprint: Sha256
    fixture_id: Identity
    fixture_fingerprint: Sha256
    catalog_id: Identity
    catalog_fingerprint: Sha256
    pair_id: None = None
    arm: None = None
    oracle: SystemOracle
    provenance: EvalProvenance
    limitation: str = Field(min_length=1)

    @model_validator(mode="after")
    def identity_matches_task(self) -> Self:
        if self.cell_id != f"system.{self.task_id}":
            raise ValueError("system_cell_identity")
        if not self.task_id.startswith(f"{self.environment}."):
            raise ValueError("system_environment_identity")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("duplicate_eval_tag")
        return self


class ReliabilityEvalCell(StrictModel):
    schema_version: Literal["1"] = "1"
    kind: Literal["reliability"] = "reliability"
    suite_id: Literal["runtime-reliability-v1"] = "runtime-reliability-v1"
    cell_id: Identity
    task_id: Identity
    environment: Literal["workspace"] = "workspace"
    split: None = None
    tags: list[str] = Field(min_length=1)
    seed: int = Field(ge=0)
    resource_id: Identity
    resource_fingerprint: Sha256
    fixture_id: Identity
    fixture_fingerprint: Sha256
    catalog_id: Identity
    catalog_fingerprint: Sha256
    schedule_id: Identity
    schedule_fingerprint: Sha256
    runtime_config: RuntimeConfig
    pair_id: Identity | None = None
    arm: Literal["baseline", "mechanism", "context"]
    oracle: ReliabilityOracle
    provenance: EvalProvenance
    limitation: str = Field(min_length=1)

    @model_validator(mode="after")
    def config_matches_cell(self) -> Self:
        if self.runtime_config.schedule_id != self.schedule_id:
            raise ValueError("reliability_schedule_identity")
        if self.runtime_config.schedule_fingerprint != self.schedule_fingerprint:
            raise ValueError("reliability_schedule_fingerprint")
        if self.runtime_config.seed != self.seed:
            raise ValueError("reliability_seed_identity")
        if self.arm == "context" and self.pair_id is not None:
            raise ValueError("context_cell_cannot_claim_pair")
        if self.arm != "context" and self.pair_id is None:
            raise ValueError("paired_cell_requires_pair")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("duplicate_eval_tag")
        return self


class RecoveryEvalCell(StrictModel):
    schema_version: Literal["1"] = "1"
    kind: Literal["recovery"] = "recovery"
    suite_id: Literal["recovery-replay-v1"] = "recovery-replay-v1"
    cell_id: Identity
    diagnostic: Literal["checkpoint_off", "checkpoint_on", "reference", "action_replay"]
    task_id: Literal["workspace.fix-config"] = "workspace.fix-config"
    environment: Literal["workspace"] = "workspace"
    split: None = None
    tags: list[str] = Field(min_length=1)
    seed: int = Field(ge=0)
    resource_id: Identity
    resource_fingerprint: Sha256
    fixture_id: Identity
    fixture_fingerprint: Sha256
    catalog_id: Identity
    catalog_fingerprint: Sha256
    schedule_id: Identity
    schedule_fingerprint: Sha256
    interruption_schedule_id: Identity | None = None
    interruption_schedule_fingerprint: Sha256 | None = None
    runtime_config: RuntimeConfig
    checkpointing: Literal["on", "off"]
    record_actions: bool
    pair_id: None = None
    arm: None = None
    oracle: RecoveryOracle
    provenance: EvalProvenance
    limitation: str = Field(min_length=1)

    @model_validator(mode="after")
    def diagnostic_configuration_is_fixed(self) -> Self:
        if (self.interruption_schedule_id is None) != (
            self.interruption_schedule_fingerprint is None
        ):
            raise ValueError("recovery_interruption_identity_partial")
        interrupted = self.diagnostic in {"checkpoint_off", "checkpoint_on"}
        if interrupted != (self.interruption_schedule_id is not None):
            raise ValueError("recovery_interruption_identity")
        if self.diagnostic == "checkpoint_on" and self.checkpointing != "on":
            raise ValueError("recovery_checkpoint_on_config")
        if self.diagnostic != "checkpoint_on" and self.checkpointing != "off":
            raise ValueError("recovery_checkpoint_off_config")
        if self.record_actions != (self.diagnostic == "action_replay"):
            raise ValueError("recovery_action_recording_config")
        if self.runtime_config.schedule_id != self.schedule_id:
            raise ValueError("recovery_schedule_identity")
        if self.runtime_config.schedule_fingerprint != self.schedule_fingerprint:
            raise ValueError("recovery_schedule_fingerprint")
        if self.runtime_config.seed != self.seed:
            raise ValueError("recovery_seed_identity")
        return self


EvalCell: TypeAlias = Annotated[
    SystemEvalCell | ReliabilityEvalCell | RecoveryEvalCell,
    Field(discriminator="kind"),
]


class EvalComparison(StrictModel):
    comparison_id: Identity
    baseline_cell_id: Identity
    mechanism_cell_id: Identity
    allowed_config_differences: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def comparison_is_non_self_and_unique(self) -> Self:
        if self.baseline_cell_id == self.mechanism_cell_id:
            raise ValueError("comparison_self_pair")
        if len(self.allowed_config_differences) != len(
            set(self.allowed_config_differences)
        ):
            raise ValueError("duplicate_allowed_config_difference")
        return self


class EvalSuiteManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    manifest_id: Identity
    suite_id: SuiteId
    suite_version: Literal[1] = 1
    cells: list[EvalCell] = Field(min_length=1)
    comparisons: list[EvalComparison] = Field(default_factory=list)
    provenance: EvalProvenance
    limitation: str = Field(min_length=1)
    manifest_fingerprint: Sha256

    @model_validator(mode="after")
    def suite_identity_is_complete(self) -> Self:
        if self.manifest_id != f"{self.suite_id}.manifest.v1":
            raise ValueError("eval_suite_manifest_identity")
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("duplicate_eval_cell")
        if any(cell.suite_id != self.suite_id for cell in self.cells):
            raise ValueError("eval_cell_suite_identity")
        expected_count = {
            "system-correctness-v1": 30,
            "runtime-reliability-v1": 7,
            "recovery-replay-v1": 4,
        }[self.suite_id]
        if len(self.cells) != expected_count:
            raise ValueError("eval_suite_cell_count")
        comparison_ids = [item.comparison_id for item in self.comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("duplicate_eval_comparison")
        by_id = {cell.cell_id: cell for cell in self.cells}
        for comparison in self.comparisons:
            baseline = by_id.get(comparison.baseline_cell_id)
            mechanism = by_id.get(comparison.mechanism_cell_id)
            if not isinstance(baseline, ReliabilityEvalCell) or not isinstance(
                mechanism, ReliabilityEvalCell
            ):
                raise ValueError("comparison_cell_reference")
            if (
                baseline.pair_id != comparison.comparison_id
                or mechanism.pair_id != comparison.comparison_id
                or baseline.arm != "baseline"
                or mechanism.arm != "mechanism"
            ):
                raise ValueError("comparison_arm_identity")
        expected_comparisons = 3 if self.suite_id == "runtime-reliability-v1" else 0
        if len(self.comparisons) != expected_comparisons:
            raise ValueError("eval_suite_comparison_count")
        return self


class EvalSelectionSpec(StrictModel):
    suite: SuiteSelector
    environment: EnvironmentName | None = None
    split: CorpusSplit | None = None
    tag: str | None = None
    pair: Identity | None = None
    candidate_total: int = Field(gt=0)
    selected_total: int = Field(gt=0)
    cell_ids: list[Identity] = Field(min_length=1)

    @model_validator(mode="after")
    def count_matches_cells(self) -> Self:
        if self.selected_total != len(self.cell_ids):
            raise ValueError("selection_count_mismatch")
        if len(self.cell_ids) != len(set(self.cell_ids)):
            raise ValueError("selection_duplicate_cell")
        if self.selected_total > self.candidate_total:
            raise ValueError("selection_exceeds_candidates")
        return self


class NormalizedEvalRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    kind: Literal["system", "reliability", "recovery"]
    suite_id: SuiteId
    cell_id: Identity
    pair_id: Identity | None = None
    arm: Literal["baseline", "mechanism", "context"] | None = None
    source_commit: SourceCommit
    environment: EnvironmentName
    task_id: Identity
    split: CorpusSplit | None
    tags: list[str] = Field(min_length=1)
    seed: int = Field(ge=0)
    resource_id: Identity
    resource_fingerprint: Sha256
    schedule_id: Identity | None = None
    schedule_fingerprint: Sha256 | None = None
    config_fingerprint: Sha256
    raw_result_path: RelativePath
    raw_result_sha256: Sha256
    cell_contract_passed: bool
    verifier_state_success: StatusValue
    runtime_completion_success: StatusValue
    terminal: str = Field(min_length=1)
    error_category: str | None = None
    steps: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    physical_executions: int | None = Field(default=None, ge=0)
    physical_write_executions: int | None = Field(default=None, ge=0)
    side_effect_executions: int | None = Field(default=None, ge=0)
    duplicate_side_effects: int | None = Field(default=None, ge=0)
    retries: int | None = Field(default=None, ge=0)
    idempotency_hits: int | None = Field(default=None, ge=0)
    dataops_attempted: int | None = Field(default=None, ge=0)
    dataops_committed: int | None = Field(default=None, ge=0)
    incident_terminal: str | None = None
    incident_safety_success: bool | None = None
    diagnostic: str | None = None

    @field_validator("raw_result_path")
    @classmethod
    def raw_result_path_is_relative(cls, value: str) -> str:
        return _validate_relative_path(value)

    @model_validator(mode="after")
    def applicable_fields_are_explicit(self) -> Self:
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("normalized_duplicate_tag")
        if (self.schedule_id is None) != (self.schedule_fingerprint is None):
            raise ValueError("normalized_schedule_identity_partial")
        if self.kind == "system":
            if self.suite_id != "system-correctness-v1" or self.split is None:
                raise ValueError("system_record_identity")
            if self.runtime_completion_success != "N/A":
                raise ValueError("system_runtime_completion_must_be_na")
            if self.pair_id is not None or self.arm is not None or self.diagnostic is not None:
                raise ValueError("system_record_non_applicable_identity")
            if self.schedule_id is not None:
                raise ValueError("system_record_non_applicable_schedule")
            system_usage = (
                self.steps,
                self.tool_calls,
                self.physical_executions,
                self.physical_write_executions,
                self.side_effect_executions,
                self.duplicate_side_effects,
                self.retries,
                self.idempotency_hits,
            )
            if any(value is not None for value in system_usage):
                raise ValueError("system_record_non_applicable_usage")
            if self.environment == "dataops":
                if self.dataops_attempted is None or self.dataops_committed is None:
                    raise ValueError("dataops_record_effects_missing")
            elif self.dataops_attempted is not None or self.dataops_committed is not None:
                raise ValueError("non_dataops_record_effects")
            if self.environment == "incident":
                if self.incident_terminal is None or self.incident_safety_success is None:
                    raise ValueError("incident_record_summary_missing")
            elif self.incident_terminal is not None or self.incident_safety_success is not None:
                raise ValueError("non_incident_record_summary")
        elif self.kind == "reliability":
            metrics = (
                self.steps,
                self.tool_calls,
                self.physical_executions,
                self.side_effect_executions,
                self.duplicate_side_effects,
                self.retries,
                self.idempotency_hits,
            )
            if self.suite_id != "runtime-reliability-v1" or self.split is not None:
                raise ValueError("reliability_record_identity")
            if self.schedule_id is None or self.physical_write_executions is None:
                raise ValueError("reliability_schedule_or_write_usage_missing")
            if not isinstance(self.runtime_completion_success, bool) or not isinstance(
                self.verifier_state_success, bool
            ):
                raise ValueError("reliability_status_must_be_boolean")
            if any(value is None for value in metrics):
                raise ValueError("reliability_usage_missing")
            if self.dataops_attempted is not None or self.incident_terminal is not None:
                raise ValueError("reliability_non_applicable_summary")
            if (
                self.dataops_committed is not None
                or self.incident_safety_success is not None
                or self.diagnostic is not None
            ):
                raise ValueError("reliability_non_applicable_fields")
        else:
            if self.suite_id != "recovery-replay-v1" or self.split is not None:
                raise ValueError("recovery_record_identity")
            if self.schedule_id is None:
                raise ValueError("recovery_schedule_identity_missing")
            if self.pair_id is not None or self.arm is not None or self.diagnostic is None:
                raise ValueError("recovery_record_diagnostic_identity")
            if (
                self.physical_executions is None
                or self.physical_write_executions is None
            ):
                raise ValueError("recovery_physical_usage_missing")
            recovery_non_applicable = (
                self.steps,
                self.tool_calls,
                self.side_effect_executions,
                self.retries,
                self.idempotency_hits,
                self.dataops_attempted,
                self.dataops_committed,
                self.incident_terminal,
                self.incident_safety_success,
            )
            if any(value is not None for value in recovery_non_applicable):
                raise ValueError("recovery_record_non_applicable_fields")
        return self


class ExactRatio(StrictModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    rate: float = Field(ge=0.0)

    @model_validator(mode="after")
    def rate_is_exact(self) -> Self:
        if self.rate != self.numerator / self.denominator:
            raise ValueError("ratio_contradiction")
        return self


class SystemSliceSummary(StrictModel):
    dimension: Literal["environment", "split", "tag"]
    value: str = Field(min_length=1)
    selected: int = Field(gt=0)
    verifier_passed: int = Field(ge=0)

    @model_validator(mode="after")
    def passed_does_not_exceed_selected(self) -> Self:
        if self.verifier_passed > self.selected:
            raise ValueError("slice_count_contradiction")
        return self


class PairDelta(StrictModel):
    comparison_id: Identity
    baseline_cell_id: Identity
    mechanism_cell_id: Identity
    completion_delta: int = Field(ge=-1, le=1)
    verifier_delta: int = Field(ge=-1, le=1)
    duplicate_side_effect_delta: int
    physical_execution_delta: int
    physical_write_delta: int
    retry_delta: int
    idempotency_hit_delta: int


class DiagnosticSummary(StrictModel):
    cell_id: Identity
    diagnostic: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class ReliabilityCellSummary(StrictModel):
    cell_id: Identity
    pair_id: Identity | None = None
    arm: Literal["baseline", "mechanism", "context"]
    terminal: str = Field(min_length=1)
    error_category: str | None = None
    verifier_state_success: bool
    runtime_completion_success: bool
    physical_executions: int = Field(ge=0)
    physical_writes: int = Field(ge=0)
    duplicate_side_effects: int = Field(ge=0)
    retries: int = Field(ge=0)
    idempotency_hits: int = Field(ge=0)


class OracleFailureSummary(StrictModel):
    cell_id: Identity
    error_category: str = Field(min_length=1)
    raw_result_path: RelativePath

    @field_validator("raw_result_path")
    @classmethod
    def failure_path_is_relative(cls, value: str) -> str:
        return _validate_relative_path(value)


class EvalSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    suite_ids: list[SuiteId] = Field(min_length=1)
    selected_total: int = Field(gt=0)
    verifier_state_success: ExactRatio
    runtime_completion_success: ExactRatio | Literal["N/A"]
    duplicate_side_effects: ExactRatio | Literal["N/A"]
    physical_executions: ExactRatio | Literal["N/A"]
    physical_writes: ExactRatio | Literal["N/A"]
    retries: ExactRatio | Literal["N/A"]
    idempotency_hits: ExactRatio | Literal["N/A"]
    system_slices: list[SystemSliceSummary]
    reliability_cells: list[ReliabilityCellSummary]
    pair_deltas: list[PairDelta]
    diagnostics: list[DiagnosticSummary]
    oracle_failure_cell_ids: list[Identity]
    oracle_failures: list[OracleFailureSummary]
    model: Literal["N/A"] = "N/A"
    token_cost: Literal["N/A"] = "N/A"
    latency: Literal["observed/non-comparable"] = "observed/non-comparable"

    @model_validator(mode="after")
    def summary_identities_are_unique(self) -> Self:
        if len(self.suite_ids) != len(set(self.suite_ids)):
            raise ValueError("duplicate_summary_suite")
        if len(self.oracle_failure_cell_ids) != len(set(self.oracle_failure_cell_ids)):
            raise ValueError("duplicate_failure_cell")
        failure_ids = [item.cell_id for item in self.oracle_failures]
        if failure_ids != self.oracle_failure_cell_ids:
            raise ValueError("failure_slice_identity_mismatch")
        pair_ids = [item.comparison_id for item in self.pair_deltas]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("duplicate_pair_delta")
        diagnostic_ids = [item.cell_id for item in self.diagnostics]
        if len(diagnostic_ids) != len(set(diagnostic_ids)):
            raise ValueError("duplicate_diagnostic")
        reliability_ids = [item.cell_id for item in self.reliability_cells]
        if len(reliability_ids) != len(set(reliability_ids)):
            raise ValueError("duplicate_reliability_summary")
        return self


class EvalArtifact(StrictModel):
    path: RelativePath
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def artifact_path_is_relative(cls, value: str) -> str:
        return _validate_relative_path(value)


class EvalBundleManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    source_commit: SourceCommit
    package_version: Literal["0.1.0.dev0"] = "0.1.0.dev0"
    proposal_version: Literal["1.10"] = "1.10"
    selection: EvalSelectionSpec
    suite_fingerprints: dict[SuiteId, Sha256] = Field(min_length=1)
    artifacts: list[EvalArtifact] = Field(min_length=1)
    bundle_fingerprint: Sha256
    limitation: str = BUNDLE_DIGEST_LIMITATION

    @model_validator(mode="after")
    def artifact_identity_is_unique(self) -> Self:
        paths = [item.path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate_bundle_artifact")
        if self.limitation != BUNDLE_DIGEST_LIMITATION:
            raise ValueError("bundle_limitation_identity")
        return self


class RecoveryDiagnosticArtifact(StrictModel):
    schema_version: Literal["1"] = "1"
    cell_id: Identity
    diagnostic: str = Field(min_length=1)
    passed: bool
    verifier_state_success: StatusValue
    runtime_completion_success: StatusValue
    terminal: str = Field(min_length=1)
    error_category: str | None = None
    source_result: RelativePath | None = None
    replay_result: RelativePath | None = None
    source_unchanged: StatusValue
    second_process_used: bool
    second_process_exit_code: int | None = Field(default=None, ge=0)
    policy_calls: int | None = Field(default=None, ge=0)
    physical_executions: int = Field(ge=0)
    physical_write_executions: int = Field(ge=0)
    duplicate_side_effects: int | None = Field(default=None, ge=0)
    vertical_slice_matches: int | None = Field(default=None, ge=0, le=1)
    vertical_slice_total: int | None = Field(default=None, ge=0, le=1)
    reference_match: bool | None = None

    @field_validator("source_result", "replay_result")
    @classmethod
    def optional_paths_are_relative(cls, value: str | None) -> str | None:
        return None if value is None else _validate_relative_path(value)

    @model_validator(mode="after")
    def second_process_evidence_is_explicit(self) -> Self:
        if self.second_process_used != (self.second_process_exit_code is not None):
            raise ValueError("second_process_exit_identity")
        return self


class EvalValidationResult(StrictModel):
    schema_version: Literal["1"] = "1"
    status: Literal["valid"] = "valid"
    source_commit: SourceCommit
    selected_cells: int = Field(gt=0)
    source_bytes_unchanged: Literal[True] = True
    execution_calls: Literal[0] = 0
