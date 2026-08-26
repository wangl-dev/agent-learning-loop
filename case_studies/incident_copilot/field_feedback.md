# Simulated customer scenario

## Synthetic task review, not customer feedback

This is a synthetic task review, not customer feedback. The backlog below comes from the ten fixed
tasks, their raw audit evidence, and the current offline limitations. It contains no satisfaction
score, user quote, meeting conclusion, or customer priority.

| Evidence path | Observation | Product gap | Candidate platform capability | Validation still missing |
|---|---|---|---|---|
| [`recover-auth-dependency-chain/result.json`](pilot-evidence/evidence/runs/system-correctness-v1/system.incident.recover-auth-dependency-chain/result.json) | Recovery depends on ordered observations across services | Telemetry authority and freshness are synthetic and instantaneous | Freshness-tagged evidence graph with explicit dependency/order rules | Real monitoring fields, delay distribution, stale/conflicting signal study |
| [`restart-stuck-order-worker/audit.jsonl`](pilot-evidence/evidence/runs/system-correctness-v1/system.incident.restart-stuck-order-worker/audit.jsonl) | A restart is accepted only with exact approval and one physical mutation | No authenticated external approver or revocation path | Approval adapter with identity, scope, expiry, revocation, and operation-id binding | Customer RBAC/policy review and end-to-end denial/revocation tests |
| [`reject-premature-checkout-ack/audit.jsonl`](pilot-evidence/evidence/runs/system-correctness-v1/system.incident.reject-premature-checkout-ack/audit.jsonl) | Premature acknowledgement is rejected until recovery evidence exists | Partial recovery and uncertainty have no shared operator presentation | Evidence timeline with explicit incomplete/ambiguous state and ack guard | Operator comprehension and correct handoff study with real workflow |
| [`escalate-ambiguous-api-errors/result.json`](pilot-evidence/evidence/runs/system-correctness-v1/system.incident.escalate-ambiguous-api-errors/result.json) | Ambiguous evidence leads to escalation rather than forced recovery | Escalation owner, queue, acknowledgement, and timeout are absent | Ownership/handoff state machine with explicit acceptance and expiry | Ticketing/on-call integration plus missed-handoff failure tests |
| [`dedupe-notification-restart/audit.jsonl`](pilot-evidence/evidence/runs/system-correctness-v1/system.incident.dedupe-notification-restart/audit.jsonl) | Duplicate operation returns the saved result without another mutation | Idempotency scope is run-local synthetic state | Connector-level durable operation ledger and reconciliation view | Crash/retry/concurrency tests against the owning real service |
| [`rollback-checkout-canary/audit.jsonl`](pilot-evidence/evidence/runs/system-correctness-v1/system.incident.rollback-checkout-canary/audit.jsonl) | Feature-flag change is bound to an exact approved value | No deployment owner, blast-radius signal, or external rollback route | Isolated change adapter with independent state readback and emergency disable | Real flag system permissions, canary boundary, rollback rehearsal |
| [`acceptance.json`](pilot-evidence/acceptance.json) | All fixed checks pass while business fields remain N/A | Engineering contract does not measure adoption or value | Separate evidence views for safety contract and field outcome | Agreed baseline, privacy-safe adoption/outcome collection, and owner-defined targets |

These items are not ranked. Prioritization requires real discovery, risk ownership, integration
cost, and evidence that the missing capability matters in an actual operating context.
