"""A deterministic, in-memory Incident simulator with no external side effects."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from agent_learning_loop.canonical import canonical_sha256
from agent_learning_loop.incident_schemas import (
    AuditCategory,
    AuditDecision,
    IncidentAction,
    IncidentApprovalRule,
    IncidentAuditRecord,
    IncidentSnapshot,
    IncidentTaskFixture,
    IncidentToolName,
    IncidentToolResult,
)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)\s*([:=])\s*([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~-]+)")


def _redact_log_line(line: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", line)
    return _BEARER_TOKEN.sub(r"\1 [REDACTED]", redacted)


@dataclass(frozen=True)
class _Operation:
    fingerprint: str
    result: IncidentToolResult


@dataclass(frozen=True)
class _Grant:
    rule: IncidentApprovalRule
    fingerprint: str
    run_id: str
    task_id: str


class IncidentEnvironment:
    """Execute the eight fixed Incident tools against one resettable fixture."""

    def __init__(self, fixture: IncidentTaskFixture, *, run_id: str) -> None:
        self.fixture = fixture
        self.run_id = run_id
        private = fixture.private
        self._services = dict(private.services)
        self._feature_flags = dict(private.feature_flags)
        self._incident_status = private.incident_status
        self._restart_counts = {name: 0 for name in self._services}
        self._feature_flag_mutations = {name: 0 for name in self._feature_flags}
        self._audit: list[IncidentAuditRecord] = []
        self._operations: dict[str, _Operation] = {}
        self._grants: dict[str, _Grant] = {}

    @property
    def audit(self) -> tuple[IncidentAuditRecord, ...]:
        return tuple(self._audit)

    def snapshot(self) -> IncidentSnapshot:
        return IncidentSnapshot(
            services=self._services,
            feature_flags=self._feature_flags,
            incident_status=self._incident_status,
            restart_counts=self._restart_counts,
            feature_flag_mutations=self._feature_flag_mutations,
        )

    def execute(self, action: IncidentAction) -> IncidentToolResult:
        if action.tool_name not in self.fixture.task.allowed_tools:
            return self._reject(action, "tool_not_allowed", target="task")
        dispatch = {
            "get_service_status": self._get_service_status,
            "read_service_logs": self._read_service_logs,
            "inspect_service_config": self._inspect_service_config,
            "request_approval": self._request_approval,
            "set_feature_flag": self._set_feature_flag,
            "restart_simulated_service": self._restart_service,
            "acknowledge_incident": self._acknowledge,
            "escalate_incident": self._escalate,
        }
        return dispatch[action.tool_name](action)

    def _target(self, action: IncidentAction) -> str:
        value = action.arguments.get(
            "service", action.arguments.get("target", action.arguments.get("key", "task"))
        )
        return value if isinstance(value, str) and value else "task"

    def _record(
        self,
        *,
        category: AuditCategory,
        action: IncidentAction,
        decision: AuditDecision,
        target: str,
        approval_id: str | None = None,
        operation_id: str | None = None,
        fingerprint: str | None = None,
        idempotency_hit: bool = False,
        physical_mutation: bool = False,
        tool_name: IncidentToolName | None = None,
    ) -> None:
        self._audit.append(
            IncidentAuditRecord(
                sequence=len(self._audit),
                run_id=self.run_id,
                task_id=self.fixture.task.task_id,
                category=category,
                target=target,
                tool_name=tool_name if tool_name is not None else action.tool_name,
                approval_id=approval_id,
                operation_id=operation_id,
                decision=decision,
                action_fingerprint=fingerprint,
                idempotency_hit=idempotency_hit,
                physical_mutation=physical_mutation,
            )
        )

    def _reject(
        self,
        action: IncidentAction,
        category: str,
        *,
        target: str,
        approval_id: str | None = None,
        operation_id: str | None = None,
        fingerprint: str | None = None,
    ) -> IncidentToolResult:
        self._record(
            category="execution",
            action=action,
            decision="rejected",
            target=target,
            approval_id=approval_id,
            operation_id=operation_id,
            fingerprint=fingerprint,
        )
        return IncidentToolResult(status="rejected", error_category=category)

    def _get_service_status(self, action: IncidentAction) -> IncidentToolResult:
        target = self._target(action)
        if target not in self._services:
            return self._reject(action, "unknown_service", target=target)
        self._record(category="observation", action=action, decision="executed", target=target)
        return IncidentToolResult(
            status="ok", payload={"service": target, "state": self._services[target]}
        )

    def _read_service_logs(self, action: IncidentAction) -> IncidentToolResult:
        target = self._target(action)
        if target not in self._services:
            return self._reject(action, "unknown_service", target=target)
        self._record(category="observation", action=action, decision="executed", target=target)
        return IncidentToolResult(
            status="ok",
            payload={
                "service": target,
                "lines": [
                    _redact_log_line(line)
                    for line in self.fixture.private.logs.get(target, [])
                ],
            },
        )

    def _inspect_service_config(self, action: IncidentAction) -> IncidentToolResult:
        target = self._target(action)
        if target not in self._feature_flags:
            return self._reject(action, "unknown_config", target=target)
        self._record(category="observation", action=action, decision="executed", target=target)
        return IncidentToolResult(
            status="ok", payload={"key": target, "enabled": self._feature_flags[target]}
        )

    def _request_approval(self, action: IncidentAction) -> IncidentToolResult:
        if self._incident_status in {"acknowledged", "escalated"}:
            return self._reject(action, "incident_terminal", target="incident")
        tool = action.arguments.get("tool_name")
        target = action.arguments.get("target")
        arguments = action.arguments.get("arguments")
        if (
            not isinstance(tool, str)
            or not isinstance(target, str)
            or not isinstance(arguments, dict)
        ):
            return self._reject(action, "invalid_approval_request", target="task")
        requested_action = {"tool_name": tool, "target": target, "arguments": arguments}
        fingerprint = canonical_sha256(requested_action)
        matching = next(
            (
                rule
                for rule in self.fixture.private.approvals
                if rule.tool_name == tool and rule.target == target
                and canonical_sha256(rule.canonical_action()) == fingerprint
            ),
            None,
        )
        if matching is None:
            self._record(
                category="approval",
                action=action,
                decision="denied",
                target=target,
                fingerprint=fingerprint,
                tool_name=cast(IncidentToolName, tool),
            )
            return IncidentToolResult(status="rejected", error_category="approval_denied")
        self._record(
            category="approval",
            action=action,
            decision=matching.decision,
            target=target,
            approval_id=matching.approval_id,
            fingerprint=fingerprint,
            tool_name=matching.tool_name,
        )
        if matching.decision == "denied":
            return IncidentToolResult(status="rejected", error_category="approval_denied")
        self._grants[matching.approval_id] = _Grant(
            rule=matching,
            fingerprint=fingerprint,
            run_id=self.run_id,
            task_id=self.fixture.task.task_id,
        )
        return IncidentToolResult(
            status="ok", payload={"approval_id": matching.approval_id, "decision": "approved"}
        )

    def _high_impact(
        self,
        action: IncidentAction,
        *,
        target: str,
        mutation: Callable[[], IncidentToolResult],
    ) -> IncidentToolResult:
        approval_id = action.arguments.get("approval_id")
        operation_id = action.arguments.get("operation_id")
        if not isinstance(approval_id, str) or not approval_id:
            return self._reject(action, "approval_required", target=target)
        if not isinstance(operation_id, str) or not operation_id:
            return self._reject(
                action, "operation_required", target=target, approval_id=approval_id
            )
        approved_request = {
            "tool_name": action.tool_name,
            "target": target,
            "arguments": {
                key: value
                for key, value in action.arguments.items()
                if key not in {"approval_id", "operation_id"}
            },
        }
        approval_fingerprint = canonical_sha256(approved_request)
        fingerprint = canonical_sha256({**approved_request, "approval_id": approval_id})
        previous = self._operations.get(operation_id)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                return self._reject(
                    action,
                    "idempotency_conflict",
                    target=target,
                    approval_id=approval_id,
                    operation_id=operation_id,
                    fingerprint=approval_fingerprint,
                )
            self._record(
                category="execution",
                action=action,
                decision="executed",
                target=target,
                approval_id=approval_id,
                operation_id=operation_id,
                fingerprint=approval_fingerprint,
                idempotency_hit=True,
            )
            return previous.result.model_copy(update={"idempotency_hit": True})
        if self._incident_status in {"acknowledged", "escalated"}:
            return self._reject(
                action,
                "incident_terminal",
                target=target,
                approval_id=approval_id,
                operation_id=operation_id,
                fingerprint=approval_fingerprint,
            )
        grant = self._grants.get(approval_id)
        if (
            grant is None
            or grant.rule.tool_name != action.tool_name
            or grant.rule.target != target
            or grant.fingerprint != approval_fingerprint
            or grant.run_id != self.run_id
            or grant.task_id != self.fixture.task.task_id
        ):
            return self._reject(
                action,
                "approval_mismatch",
                target=target,
                approval_id=approval_id,
                operation_id=operation_id,
                fingerprint=approval_fingerprint,
            )
        result = mutation()
        self._record(
            category="execution",
            action=action,
            decision="executed",
            target=target,
            approval_id=approval_id,
            operation_id=operation_id,
            fingerprint=approval_fingerprint,
            physical_mutation=True,
        )
        self._operations[operation_id] = _Operation(fingerprint=fingerprint, result=result)
        return result

    def _restart_service(self, action: IncidentAction) -> IncidentToolResult:
        target = self._target(action)
        if target not in self._services:
            return self._reject(action, "unknown_service", target=target)
        return self._high_impact(action, target=target, mutation=self._perform_restart(target))

    def _perform_restart(self, target: str) -> Callable[[], IncidentToolResult]:
        def apply() -> IncidentToolResult:
            self._services[target] = "healthy"
            self._restart_counts[target] += 1
            if self._incident_status == "open":
                self._incident_status = "mitigated"
            return IncidentToolResult(status="ok", payload={"service": target, "state": "healthy"})

        return apply

    def _set_feature_flag(self, action: IncidentAction) -> IncidentToolResult:
        target = self._target(action)
        enabled = action.arguments.get("enabled")
        if target not in self._feature_flags or not isinstance(enabled, bool):
            return self._reject(action, "invalid_config_mutation", target=target)

        def apply() -> IncidentToolResult:
            self._feature_flags[target] = enabled
            self._feature_flag_mutations[target] += 1
            service_name = target.split(".", maxsplit=1)[0]
            if service_name in self._services:
                self._services[service_name] = "healthy"
            if self._incident_status == "open":
                self._incident_status = "mitigated"
            return IncidentToolResult(status="ok", payload={"key": target, "enabled": enabled})

        return self._high_impact(action, target=target, mutation=apply)

    def _acknowledge(self, action: IncidentAction) -> IncidentToolResult:
        if self._incident_status in {"acknowledged", "escalated"}:
            return self._reject(action, "incident_terminal", target="incident")
        if all(state == "healthy" for state in self._services.values()):
            self._incident_status = "acknowledged"
            self._record(
                category="acknowledgement",
                action=action,
                decision="acknowledged",
                target="incident",
            )
            return IncidentToolResult(status="ok", payload={"incident_status": "acknowledged"})
        return self._reject(action, "incident_not_mitigated", target="incident")

    def _escalate(self, action: IncidentAction) -> IncidentToolResult:
        if self._incident_status in {"acknowledged", "escalated"}:
            return self._reject(action, "incident_terminal", target="incident")
        reason = action.arguments.get("reason_category")
        if not isinstance(reason, str) or not reason:
            return self._reject(action, "invalid_escalation", target="incident")
        self._incident_status = "escalated"
        self._record(category="escalation", action=action, decision="escalated", target="incident")
        return IncidentToolResult(
            status="ok", payload={"incident_status": "escalated", "reason_category": reason}
        )
