# ADR 0007: Keep Incident v1 beside the frozen Workspace Runtime

## Context

M4A published Workspace task, action, Runtime, journal, and replay contracts. Incident needs a
different state machine: a simulated service can be restarted or have a feature flag changed only
after task-bound approval, and repeated operations must not repeat a side effect.

## Decision

Incident uses its own strict Pydantic v1 fixture, action, audit, result, catalog, corpus, runner,
and verifier models. It reuses only small stable primitives: strict-model behavior, canonical
SHA-256, `VerifierCheck`/`VerifierResult`, and corpus governance patterns. The eight allowlisted
tools operate only on deterministic in-memory state and each tool has its own strict argument
schema. `restart_simulated_service` and `set_feature_flag` need a private, predeclared canonical
action fingerprint plus a non-empty operation ID. Approval cannot be created from caller-supplied
parameters. Their audit executions must carry matching run/task/tool/target/approval/operation and
fingerprint references; duplicate operations are distinguished from physical mutations.
The runner supplies the trusted run/task context to the verifier, and the verifier carries that
context into the result. Incident verifier payloads require non-empty, uniquely named checks and
derive a fixed 1.0/0.0 verdict from those checks. Audit-only verification keeps its named audit
subset, but a final run result requires the complete fixed full-check name set. Private
expectations require exact restart and flag-mutation counts, so state, counters, and physical
audit evidence must agree. A passing terminal state also requires exactly one matching successful
acknowledgement or escalation record.
Fixed key/token/password assignments and Bearer-token shapes are masked at the Incident log
observation boundary. This is a small synthetic-corpus guard, not a general DLP claim.

## Consequences

Workspace/M2/M3 JSON readers and Runtime unions remain unchanged. This duplicates some narrow
loader and runner code, but makes regressions attributable and lets the verifier prove approval
ordering, side-effect bounds, scope, terminal state, and audit continuity. It is not a common
multi-environment Runtime yet. Reconsider shared abstractions after DataOps supplies a second
independent environment contract.
