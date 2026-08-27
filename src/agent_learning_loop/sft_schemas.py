"""Strict, provider-neutral contracts for M7A SFT development candidates."""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal, Self, TypeAlias

from pydantic import Field, JsonValue, model_validator

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.eval_schemas import EnvironmentName, SourceCommit
from agent_learning_loop.schemas import StrictModel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[str, Field(min_length=1, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
NonEmptyText = Annotated[str, Field(min_length=1)]
RelativePath = Annotated[str, Field(min_length=1)]

SFT_CONTRACT_VERSION: Final = "sft-scripted-oracle-v1"
SFT_DATASET_ID: Final = "agent-learning-loop-sft-development-v1"
SFT_ARTIFACT_PATHS: Final = (
    "dataset-manifest.json",
    "samples.jsonl",
    "quality-report.json",
    "report.md",
)
_PROHIBITED_SAMPLE_KEYS: Final = {
    "private",
    "setup",
    "expected",
    "verifier",
    "checks",
    "detail",
    "score",
    "audit",
    "run_id",
    "action_ref",
    "before_digest",
    "after_digest",
    "primary_key_digest",
    "database_digest",
}
_SECRET_LIKE: Final = re.compile(
    r"(?i)(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:authorization|cookie)\s*[:=]|"
    r"\b(?:api[_-]?key|password|secret|token)\s*[:=]|\bsk-[a-z0-9_-]{8,}|"
    r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})"
)
_MACHINE_PATH: Final = re.compile(
    r"(?ix)(?:"
    r"(?<![a-z0-9])(?:[a-z]:[\\/])"
    r"|(?<!\\)\\\\[a-z0-9._$-]+[\\/][a-z0-9._$ -]+"
    r"|(?<![a-z0-9:/])/(?:home|users|root|tmp|var|etc|opt|usr|srv|mnt|media|"
    r"run|dev|proc|sys|private|volumes|applications|library|system)"
    r"(?:/|(?=$|[\s.,;:!?)]))"
    r")"
)


def scan_sft_sensitive_text(value: str) -> tuple[str, ...]:
    """Return the shared M7A machine-path and secret findings for one string."""
    findings: list[str] = []
    if _MACHINE_PATH.search(value):
        findings.append("machine_path")
    if _SECRET_LIKE.search(value):
        findings.append("secret_like")
    return tuple(findings)


def _is_safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return not (
        normalized.startswith("/")
        or ":" in normalized.split("/", maxsplit=1)[0]
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    )


def _require_minimized_value(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _PROHIBITED_SAMPLE_KEYS:
                raise ValueError("sft_prohibited_sample_field")
            _require_minimized_value(item)
    elif isinstance(value, list):
        for item in value:
            _require_minimized_value(item)
    elif isinstance(value, str) and scan_sft_sensitive_text(value):
        raise ValueError("sft_path_or_secret_like_value")


class SftProvenance(StrictModel):
    source: Literal["project-authored-synthetic"] = "project-authored-synthetic"
    license: Literal["Apache-2.0"] = "Apache-2.0"


class SftTableScope(StrictModel):
    table: Identity
    readable_columns: list[Identity] = Field(min_length=1)
    mutable_columns: list[Identity] = Field(default_factory=list)
    predicate_columns: list[Identity] = Field(min_length=1)
    max_mutated_rows: int = Field(gt=0)
    allow_insert: bool = False

    @model_validator(mode="after")
    def require_unique_bounded_columns(self) -> Self:
        groups = (self.readable_columns, self.mutable_columns, self.predicate_columns)
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("duplicate_public_scope_column")
        if not set(self.mutable_columns) <= set(self.readable_columns):
            raise ValueError("mutable_scope_not_readable")
        if not set(self.predicate_columns) <= set(self.readable_columns):
            raise ValueError("predicate_scope_not_readable")
        return self


class SftTaskContext(StrictModel):
    instruction: NonEmptyText
    allowed_tools: list[Identity] = Field(min_length=1)
    public_scope: list[SftTableScope] = Field(default_factory=list)
    constraints: list[NonEmptyText] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_public_fields(self) -> Self:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("duplicate_allowed_tool")
        tables = [scope.table for scope in self.public_scope]
        if len(tables) != len(set(tables)):
            raise ValueError("duplicate_public_scope_table")
        if len(self.constraints) != len(set(self.constraints)):
            raise ValueError("duplicate_public_constraint")
        return self


class SftResourceIdentity(StrictModel):
    manifest_id: Identity
    manifest_fingerprint: Sha256
    fixture_id: Identity
    fixture_fingerprint: Sha256
    catalog_id: Identity
    catalog_fingerprint: Sha256


class SftSourceArtifact(StrictModel):
    role: Literal["events", "result"]
    path: RelativePath
    sha256: Sha256

    @model_validator(mode="after")
    def require_safe_path(self) -> Self:
        if not _is_safe_relative_path(self.path):
            raise ValueError("unsafe_source_artifact_path")
        expected_name = "events.jsonl" if self.role == "events" else "result.json"
        if not self.path.endswith(f"/{expected_name}"):
            raise ValueError("source_artifact_role_path_mismatch")
        return self


class SftAssistantAction(StrictModel):
    role: Literal["assistant_action"] = "assistant_action"
    tool_name: Identity
    arguments: dict[str, JsonValue]


class SftToolResult(StrictModel):
    role: Literal["tool_result"] = "tool_result"
    tool_name: Identity
    status: Literal["ok", "rejected", "error"]
    payload: dict[str, JsonValue]
    error_category: str | None = None
    idempotency_hit: bool = False


SftTurn: TypeAlias = Annotated[
    SftAssistantAction | SftToolResult,
    Field(discriminator="role"),
]


class SftSampleQuality(StrictModel):
    eligible: Literal[True] = True
    source_eval_validated: Literal[True] = True
    raw_action_result_bound: Literal[True] = True
    public_fields_only: Literal[True] = True
    leakage_scan_passed: Literal[True] = True


class SftSample(StrictModel):
    schema_version: Literal["1"] = "1"
    contract_version: Literal["sft-scripted-oracle-v1"] = SFT_CONTRACT_VERSION
    sample_id: Identity
    task_id: Identity
    environment: EnvironmentName
    split: Literal["train"] = "train"
    scenario_family: Identity
    seed: int = Field(ge=0)
    tags: list[Identity] = Field(min_length=1)
    generation_mode: Literal["scripted_oracle"] = "scripted_oracle"
    source_commit: SourceCommit
    source_suite_id: Literal["system-correctness-v1"] = "system-correctness-v1"
    source_cell_id: Identity
    source_artifacts: list[SftSourceArtifact] = Field(min_length=2, max_length=2)
    resource: SftResourceIdentity
    provenance: SftProvenance
    task: SftTaskContext
    turns: list[SftTurn] = Field(min_length=2)
    quality: SftSampleQuality
    sample_fingerprint: Sha256

    @model_validator(mode="after")
    def require_bound_ordered_sample(self) -> Self:
        if self.sample_id != f"sft.{self.task_id}.v1":
            raise ValueError("sample_identity_mismatch")
        if self.source_cell_id != f"system.{self.task_id}":
            raise ValueError("sample_cell_identity_mismatch")
        if not self.task_id.startswith(f"{self.environment}."):
            raise ValueError("sample_environment_identity_mismatch")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("duplicate_sample_tag")
        if [item.role for item in self.source_artifacts] != ["events", "result"]:
            raise ValueError("source_artifact_order")
        run_root = f"runs/system-correctness-v1/{self.source_cell_id}"
        if [item.path for item in self.source_artifacts] != [
            f"{run_root}/events.jsonl",
            f"{run_root}/result.json",
        ]:
            raise ValueError("source_artifact_identity")
        if self.resource.manifest_id != f"{self.task_id}.manifest.v1":
            raise ValueError("resource_manifest_identity")
        if self.resource.fixture_id != f"{self.task_id}.v1":
            raise ValueError("resource_fixture_identity")
        if self.resource.catalog_id != f"{self.task_id}.actions.v1":
            raise ValueError("resource_catalog_identity")
        if (self.environment == "dataops") != bool(self.task.public_scope):
            raise ValueError("public_scope_environment_mismatch")
        if len(self.turns) % 2 != 0:
            raise ValueError("unpaired_sft_turn")
        for index in range(0, len(self.turns), 2):
            action = self.turns[index]
            result = self.turns[index + 1]
            if not isinstance(action, SftAssistantAction) or not isinstance(
                result, SftToolResult
            ):
                raise ValueError("sft_turn_order")
            if action.tool_name != result.tool_name:
                raise ValueError("sft_action_result_tool_mismatch")
            if action.tool_name not in self.task.allowed_tools:
                raise ValueError("sft_action_outside_allowlist")
        _require_minimized_value(
            self.model_dump(mode="json", exclude={"sample_fingerprint"})
        )
        expected_fingerprint = canonical_sha256(
            self.model_dump(mode="json", exclude={"sample_fingerprint"})
        )
        if self.sample_fingerprint != expected_fingerprint:
            raise ValueError("sample_fingerprint_mismatch")
        return self


class SftArtifact(StrictModel):
    path: RelativePath
    sha256: Sha256

    @model_validator(mode="after")
    def require_safe_path(self) -> Self:
        if not _is_safe_relative_path(self.path):
            raise ValueError("unsafe_dataset_artifact_path")
        return self


class SftCandidateManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: Literal["agent-learning-loop-sft-development-v1"] = SFT_DATASET_ID
    contract_version: Literal["sft-scripted-oracle-v1"] = SFT_CONTRACT_VERSION
    stage: Literal["development_candidate"] = "development_candidate"
    exporter_commit: None = None
    source_eval_manifest_sha256: Sha256
    source_eval_bundle_fingerprint: Sha256
    source_eval_selected_cells: int = Field(ge=30)
    system_suite_fingerprint: Sha256
    source_commit: SourceCommit
    sample_count: Literal[18] = 18
    environment_counts: dict[EnvironmentName, int]
    held_out_excluded: dict[Literal["validation", "test"], int]
    generation_mode: Literal["scripted_oracle"] = "scripted_oracle"
    model_generated_samples: Literal[0] = 0
    preference_pairs: Literal[0] = 0
    provenance: SftProvenance
    sample_ids: list[Identity] = Field(min_length=18, max_length=18)
    sample_fingerprints: list[Sha256] = Field(min_length=18, max_length=18)
    artifacts: list[SftArtifact] = Field(min_length=3, max_length=3)
    bundle_fingerprint: Sha256
    limitation: NonEmptyText

    @model_validator(mode="after")
    def require_complete_candidate_identity(self) -> Self:
        if self.environment_counts != {
            "workspace": 6,
            "incident": 6,
            "dataops": 6,
        }:
            raise ValueError("sft_environment_count_contract")
        if self.held_out_excluded != {"validation": 6, "test": 6}:
            raise ValueError("sft_held_out_count_contract")
        if len(self.sample_ids) != len(set(self.sample_ids)):
            raise ValueError("duplicate_manifest_sample_id")
        if len(self.sample_fingerprints) != len(set(self.sample_fingerprints)):
            raise ValueError("duplicate_manifest_sample_fingerprint")
        if [item.path for item in self.artifacts] != [
            "samples.jsonl",
            "quality-report.json",
            "report.md",
        ]:
            raise ValueError("dataset_manifest_artifact_order")
        expected_fingerprint = canonical_sha256(
            self.model_dump(mode="json", exclude={"bundle_fingerprint"})
        )
        if self.bundle_fingerprint != expected_fingerprint:
            raise ValueError("dataset_bundle_fingerprint_mismatch")
        return self


class SftQualityReport(StrictModel):
    schema_version: Literal["1"] = "1"
    contract_version: Literal["sft-scripted-oracle-v1"] = SFT_CONTRACT_VERSION
    stage: Literal["development_candidate"] = "development_candidate"
    eligible_samples: Literal[18] = 18
    environment_counts: dict[EnvironmentName, int]
    held_out_excluded: dict[Literal["validation", "test"], int]
    generation_mode: Literal["scripted_oracle"] = "scripted_oracle"
    model_generated_samples: Literal[0] = 0
    preference_pairs: Literal[0] = 0
    duplicate_sample_ids: Literal[0] = 0
    duplicate_task_ids: Literal[0] = 0
    duplicate_scenario_families: Literal[0] = 0
    duplicate_sample_fingerprints: Literal[0] = 0
    held_out_task_overlap: Literal[0] = 0
    held_out_family_overlap: Literal[0] = 0
    leakage_findings: Literal[0] = 0
    unknown_files: Literal[0] = 0
    symlinks: Literal[0] = 0
    carriage_return_files: Literal[0] = 0
    non_utf8_files: Literal[0] = 0
    machine_path_findings: Literal[0] = 0
    secret_like_findings: Literal[0] = 0
    execution_calls: Literal[0] = 0
    source_bytes_unchanged: Literal[True] = True
    quality_gates: dict[Identity, bool]
    provenance: SftProvenance
    limitation: NonEmptyText

    @model_validator(mode="after")
    def require_all_fixed_gates(self) -> Self:
        expected_gates = {
            "complete_train_identity",
            "environment_balance",
            "held_out_exclusion",
            "raw_action_result_binding",
            "public_field_minimization",
            "provenance_and_license",
            "unique_identity",
            "path_and_encoding_safety",
            "zero_execution",
        }
        if set(self.quality_gates) != expected_gates or not all(
            self.quality_gates.values()
        ):
            raise ValueError("sft_quality_gate_contract")
        if self.environment_counts != {
            "workspace": 6,
            "incident": 6,
            "dataops": 6,
        } or self.held_out_excluded != {"validation": 6, "test": 6}:
            raise ValueError("sft_quality_count_contract")
        return self


class SftCandidateValidationResult(StrictModel):
    schema_version: Literal["1"] = "1"
    valid: Literal[True] = True
    source_commit: SourceCommit
    eligible_samples: Literal[18] = 18
    workspace_samples: Literal[6] = 6
    incident_samples: Literal[6] = 6
    dataops_samples: Literal[6] = 6
    held_out_excluded: Literal[12] = 12
    files: Literal[4] = 4
    execution_calls: Literal[0] = 0
    source_bytes_unchanged: Literal[True] = True
    dataset_bytes_unchanged: Literal[True] = True
