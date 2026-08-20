# ADR 0003: Keep M2 failures fixed and Runtime safeguards run-local

- Status: Accepted for M2
- Date: 2026-08-20
- Proposal baseline: `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.4

## Context

M1 can complete three Workspace tasks when every tool call succeeds. That does not show whether a
Runtime handles a transient read failure, a logical timeout, or a write whose success result is
lost. Random fault generation would make a small comparison hard to reproduce, while adding a new
append-style tool just to make duplicate writes visible would change the task instead of isolating
the Runtime mechanism.

M1 Event and Run Result are schema v1 artifacts. Adding terminal Runtime states, budgets, attempts,
errors, retries, schedule identity, and idempotency counters to those classes without a version
change would silently alter an accepted contract.

## Decision

- Keep M1 v1 models and `run-workspace` unchanged. Route new Runtime Event and Result artifacts as
  strict schema v2, with tests that still parse v1 event/result JSON.
- Use one central legal-transition table for the synchronous M2 states. A terminal state has no
  outgoing transition; checkpoint, approval, cancellation, and replay states do not exist yet.
- Package exactly three project-authored JSON schedules. Each fixes task, seed, tool occurrence,
  before/after-success injection phase, failure kind, error category, retryability, and provenance.
  Reviewed SHA-256 fingerprints define their complete canonical identity. Reject mutated schedules
  and conflicting caller fingerprints; a normally completed run must inject its schedule exactly
  once.
- Separate step, tool-call attempt, physical execution, successful side-effect execution, retry,
  duplicate side effect, and idempotency hit counters. Budget checks happen before extra work.
- Classify errors with a nine-value machine enum. Public records use short fixed explanations;
  raw exception strings, tracebacks, local paths, and private expected state are not serialized.
- Inject a clock and sleeper. Tests advance fake time for backoff and deadline checks; the default
  uses the standard-library monotonic clock. Recheck the deadline after synchronous Policy, tool,
  Observation, and Verifier boundaries, and do not start backoff that would reach the deadline. M2
  does not use threads, processes, or async and does not claim it can preempt an arbitrary blocking
  call.
- Use attempt zero for run and state events. Attempt-bound events repeat the same attempt in their
  payload, while retry scheduling names the failed and next attempts. A `DECIDING` state event is
  associated with the upcoming Policy decision step.
- Model result loss after `write_text` by saving a successful Tool Result before returning a
  controlled transient error. `retry_only` repeats the physical write; `safeguarded` uses an
  in-memory, single-run idempotency store keyed by tool plus canonical arguments.
- Reject the same idempotency key with different arguments. Do not add a database, persistent
  store, distributed lock, or cross-process semantics.

## Consequences

- The same Task, scripted Action sequence, seed, and schedule fingerprint can be compared across
  `naive`, `retry_only`, and `safeguarded`. A difference is attributable to the mode switches,
  not to a newly sampled failure.
- `write_text` overwrites identical content, so final Workspace state alone cannot reveal the
  second execution. The Runtime's physical and side-effect counters are controlled experiment
  evidence, not a production transaction log.
- A lost-result naive run can have a passing state Verifier and a failed Runtime terminal. Success
  requires reaching `VERIFYING` normally and passing the Verifier.
- The run-local cache cannot survive a crash or resume. Persisted checkpoints and replay remain M3
  work and are not represented by placeholder switches.

## Alternatives considered

- Random failure injection: rejected for M2 because seed handling alone would not make injection
  identity as easy to audit as three fixed schedules.
- An append or increment tool: rejected because it would change the accepted M1 tool/task surface
  merely to make duplicate effects more visible.
- Thread/process/async timeout: rejected because M2 studies controlled synchronous boundaries and
  cannot honestly claim safe cancellation of arbitrary Python work.
- SQLite or a global idempotency service: rejected because persistence and distributed ownership
  are outside the single-run experiment and would pre-implement later milestones.
