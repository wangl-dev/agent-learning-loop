"""Strict, Incident-only v1 contracts.

These models deliberately do not extend the frozen Workspace task or Runtime
unions.  Incident execution is a separate deterministic simulation.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from agent_learning_loop.schemas import StrictModel, VerifierResult

IncidentToolName = Literal[
    "get_service_status",
    "read_service_logs",
    "inspect_service_config",
    "request_approval",
    "set_feature_flag",
    "restart_simulated_service",
    "acknowledge_incident",
    "escalate_incident",
]
ServiceState = Literal["healthy", "degraded", "stuck", "down"]
IncidentStatus = Literal["open", "mitigated", "resolved", "acknowledged", "escalated"]
AuditCategory = Literal["observation", "approval", "execution", "acknowledgement", "escalation"]
AuditDecision = Literal[
    "requested", "approved", "denied", "executed", "rejected", "acknowledged", "escalated"
]
INCIDENT_AUDIT_CHECK_NAMES = (
    "audit_sequence",
    "audit_context",
    "approval_records",
    "high_impact_references",
    "approved_before_execution",
    "denied_not_executed",
    "idempotent_side_effects",
    "ack_after_recovery",
)
INCIDENT_FULL_CHECK_NAMES = (
    *INCIDENT_AUDIT_CHECK_NAMES,
    "terminal_status",
    "target_state",
    "protected_state",
    "side_effect_bounds",
    "audit_side_effect_evidence",
    "terminal_audit_record",
)


class IncidentTask(StrictModel):
    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1, pattern=r"^incident\.[a-z0-9]+(?:[._-][a-z0-9]+)*$")
    environment_kind: Literal["incident"] = "incident"
    instruction: str = Field(min_length=1)
    allowed_tools: list[IncidentToolName] = Field(min_length=1)
    fixture_id: str = Field(min_length=1, pattern=r"^incident\.[a-z0-9]+(?:[._-][a-z0-9]+)*\.v1$")
    provenance: Literal["project-authored-synthetic"] = "project-authored-synthetic"


class IncidentApprovalRule(StrictModel):
    approval_id: str = Field(min_length=1)
    decision: Literal["approved", "denied"]
    tool_name: Literal["set_feature_flag", "restart_simulated_service"]
    target: str = Field(min_length=1)
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_exact_tool_parameters(self) -> Self:
        if self.tool_name == "set_feature_flag" and self.enabled is None:
            raise ValueError("feature_flag_approval_requires_value")
        if self.tool_name == "restart_simulated_service" and self.enabled is not None:
            raise ValueError("restart_approval_rejects_flag_value")
        return self

    def canonical_action(self) -> dict[str, JsonValue]:
        arguments: dict[str, JsonValue]
        if self.tool_name == "set_feature_flag":
            arguments = {"key": self.target, "enabled": self.enabled}
        else:
            arguments = {"service": self.target}
        return {
            "tool_name": self.tool_name,
            "target": self.target,
            "arguments": arguments,
        }


class IncidentExpectedState(StrictModel):
    terminal_status: Literal["acknowledged", "escalated"]
    service_states: dict[str, ServiceState] = Field(min_length=1)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    protected_services: list[str] = Field(default_factory=list)
    protected_feature_flags: list[str] = Field(default_factory=list)
    exact_restart_counts: dict[str, int] = Field(default_factory=dict)
    exact_feature_flag_mutations: dict[str, int] = Field(default_factory=dict)
    max_restart_counts: dict[str, int] = Field(default_factory=dict)
    max_feature_flag_mutations: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_nonnegative_consistent_counts(self) -> Self:
        count_maps = (
            self.exact_restart_counts,
            self.exact_feature_flag_mutations,
            self.max_restart_counts,
            self.max_feature_flag_mutations,
        )
        if any(count < 0 for counts in count_maps for count in counts.values()):
            raise ValueError("negative_side_effect_count")
        if any(
            name in self.max_restart_counts and count > self.max_restart_counts[name]
            for name, count in self.exact_restart_counts.items()
        ):
            raise ValueError("exact_restart_exceeds_maximum")
        if any(
            name in self.max_feature_flag_mutations
            and count > self.max_feature_flag_mutations[name]
            for name, count in self.exact_feature_flag_mutations.items()
        ):
            raise ValueError("exact_flag_mutation_exceeds_maximum")
        return self


class IncidentPrivateFixture(StrictModel):
    services: dict[str, ServiceState] = Field(min_length=1)
    logs: dict[str, list[str]] = Field(default_factory=dict)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    incident_status: IncidentStatus = "open"
    approvals: list[IncidentApprovalRule] = Field(default_factory=list)
    expected: IncidentExpectedState

    @model_validator(mode="after")
    def require_consistent_private_identity(self) -> Self:
        approval_ids = [rule.approval_id for rule in self.approvals]
        if len(approval_ids) != len(set(approval_ids)):
            raise ValueError("duplicate_approval_id")
        canonical_rules = [
            (rule.tool_name, rule.target, rule.enabled) for rule in self.approvals
        ]
        if len(canonical_rules) != len(set(canonical_rules)):
            raise ValueError("duplicate_canonical_approval")
        for rule in self.approvals:
            if (
                rule.tool_name == "restart_simulated_service"
                and rule.target not in self.services
            ):
                raise ValueError("approval_target_missing")
            if rule.tool_name == "set_feature_flag" and rule.target not in self.feature_flags:
                raise ValueError("approval_target_missing")
        service_references = (
            set(self.expected.service_states)
            | set(self.expected.protected_services)
            | set(self.expected.max_restart_counts)
        )
        flag_references = (
            set(self.expected.feature_flags)
            | set(self.expected.protected_feature_flags)
            | set(self.expected.max_feature_flag_mutations)
        )
        if not service_references <= set(self.services):
            raise ValueError("expected_service_missing")
        if not flag_references <= set(self.feature_flags):
            raise ValueError("expected_feature_flag_missing")
        if set(self.expected.exact_restart_counts) != set(self.services):
            raise ValueError("exact_restart_count_coverage")
        if set(self.expected.exact_feature_flag_mutations) != set(self.feature_flags):
            raise ValueError("exact_feature_flag_mutation_coverage")
        return self


class IncidentTaskFixture(StrictModel):
    schema_version: Literal["1"] = "1"
    task: IncidentTask
    private: IncidentPrivateFixture


class GetServiceStatusArguments(StrictModel):
    service: str = Field(min_length=1)


class ReadServiceLogsArguments(StrictModel):
    service: str = Field(min_length=1)


class InspectServiceConfigArguments(StrictModel):
    key: str = Field(min_length=1)


class RestartApprovalArguments(StrictModel):
    service: str = Field(min_length=1)


class FeatureFlagApprovalArguments(StrictModel):
    key: str = Field(min_length=1)
    enabled: bool


class RequestApprovalArguments(StrictModel):
    tool_name: Literal["set_feature_flag", "restart_simulated_service"]
    target: str = Field(min_length=1)
    arguments: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_bound_action(self) -> Self:
        if self.tool_name == "set_feature_flag":
            parsed_flag = FeatureFlagApprovalArguments.model_validate(self.arguments)
            if parsed_flag.key != self.target:
                raise ValueError("approval_target_mismatch")
        else:
            parsed_restart = RestartApprovalArguments.model_validate(self.arguments)
            if parsed_restart.service != self.target:
                raise ValueError("approval_target_mismatch")
        return self


class SetFeatureFlagArguments(StrictModel):
    key: str = Field(min_length=1)
    enabled: bool
    approval_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)


class RestartSimulatedServiceArguments(StrictModel):
    service: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)


class AcknowledgeIncidentArguments(StrictModel):
    pass


class EscalateIncidentArguments(StrictModel):
    reason_category: Literal[
        "approval_denied",
        "ambiguous_evidence",
        "insufficient_evidence",
    ]


TOOL_ARGUMENT_MODELS: dict[IncidentToolName, type[StrictModel]] = {
    "get_service_status": GetServiceStatusArguments,
    "read_service_logs": ReadServiceLogsArguments,
    "inspect_service_config": InspectServiceConfigArguments,
    "request_approval": RequestApprovalArguments,
    "set_feature_flag": SetFeatureFlagArguments,
    "restart_simulated_service": RestartSimulatedServiceArguments,
    "acknowledge_incident": AcknowledgeIncidentArguments,
    "escalate_incident": EscalateIncidentArguments,
}


class IncidentAction(StrictModel):
    schema_version: Literal["1"] = "1"
    tool_name: IncidentToolName
    arguments: dict[str, JsonValue]

    @model_validator(mode="after")
    def require_tool_specific_arguments(self) -> Self:
        TOOL_ARGUMENT_MODELS[self.tool_name].model_validate(self.arguments)
        return self


class IncidentToolResult(StrictModel):
    schema_version: Literal["1"] = "1"
    status: Literal["ok", "rejected", "error"]
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    error_category: str | None = None
    idempotency_hit: bool = False


class IncidentSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    services: dict[str, ServiceState]
    feature_flags: dict[str, bool]
    incident_status: IncidentStatus
    restart_counts: dict[str, int]
    feature_flag_mutations: dict[str, int]


class IncidentAuditRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    sequence: int = Field(ge=0)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    category: AuditCategory
    target: str = Field(min_length=1)
    tool_name: IncidentToolName | None = None
    approval_id: str | None = None
    operation_id: str | None = None
    decision: AuditDecision
    action_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    idempotency_hit: bool = False
    physical_mutation: bool = False


class IncidentVerifierResult(VerifierResult):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> Self:
        if not self.checks:
            raise ValueError("incident_verifier_checks_empty")
        check_names = [check.name for check in self.checks]
        if len(check_names) != len(set(check_names)):
            raise ValueError("incident_verifier_check_name_duplicate")
        checks_passed = all(check.passed for check in self.checks)
        if self.passed != checks_passed:
            raise ValueError("incident_verifier_passed_checks_mismatch")
        expected_score = 1.0 if self.passed else 0.0
        if self.score != expected_score:
            raise ValueError("incident_verifier_score_mismatch")
        return self


class IncidentRunResult(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    outcome: Literal["passed", "failed"]
    verifier: IncidentVerifierResult
    events_file: Literal["events.jsonl"] = "events.jsonl"
    audit_file: Literal["audit.jsonl"] = "audit.jsonl"

    @model_validator(mode="after")
    def require_outcome_to_match_verifier(self) -> IncidentRunResult:
        if (self.outcome == "passed") != self.verifier.passed:
            raise ValueError("outcome_verifier_mismatch")
        if self.run_id != self.verifier.run_id or self.task_id != self.verifier.task_id:
            raise ValueError("verifier_context_mismatch")
        check_names = [check.name for check in self.verifier.checks]
        if len(check_names) != len(INCIDENT_FULL_CHECK_NAMES) or set(check_names) != set(
            INCIDENT_FULL_CHECK_NAMES
        ):
            raise ValueError("incident_full_verifier_check_set_mismatch")
        return self
