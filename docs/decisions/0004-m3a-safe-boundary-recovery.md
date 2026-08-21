# ADR 0004: Recover only from a committed post-Observation boundary

- Status: accepted for M3A
- Date: 2026-08-20

## Context

M2 keeps events and idempotency state in memory and writes its v2 artifacts only after the run
ends. In the lost-write-result case, the safeguarded mode prevents a duplicate write while the
same process is alive, but it cannot continue after that process exits.

M3A asks one narrower question. Under the fixed `workspace.fix-config` task, safeguarded config,
lost-write-result schedule, and step-2 post-Observation interruption, can a second process finish
the same run without repeating the write or resetting accumulated usage?

## Decision

The M3A CLI uses an append-only v3 JSONL journal. Sequence numbers are continuous across the full
run. Segment 0 is the first process and segment 1 begins with `run_resumed`. Each record hashes its
canonical JSON fields except its own hash and carries the previous record hash. The writer flushes
and calls `fsync` after every appended record.

The checkpoint is allowed only after the write Tool Result has passed through Observation at step
2. It contains fingerprints and counters, not action arguments, file bodies, private expected
state, or raw exceptions. Its persisted state includes the run/task/fixture/config/schedule
identities, state and resume target, cumulative usage and elapsed offset, failure occurrences,
the safe idempotency result metadata, Workspace digest, and journal prefix identity.
Its ID is the canonical SHA-256 digest of every checkpoint field except the ID itself. The writer,
validator, and resume path share that calculation. This binds all recovery state across the
checkpoint and journal, but the unkeyed digest is only a corruption/cross-artifact consistency
check. It is not a signature or authenticity guarantee against someone who can rewrite all files.

The checkpoint protocol writes `.checkpoint.json.tmp`, flushes and calls `fsync`, then publishes
`checkpoint.json` with `os.replace`. The Runtime next appends `checkpoint_committed`, flushes that
record, appends `interruption_injected`, and exits deliberately. If checkpoint writing fails, the
interruption is not reported as a recoverable committed run and no terminal result is written.

Resume is fail-closed. It validates the journal, checkpoint, packaged identities and fingerprints,
state, counters, idempotency entry, remaining elapsed budget, and current Workspace digest before
constructing an Environment, Policy, Tool, or Verifier. It does not reset the Workspace. The
uninterrupted reference uses the same task, actions, failure schedule, seed, and budgets but no
interruption.

The validator performs event replay only. It requires the fixed segment 0 checkpoint/interruption
boundary and a unique segment 1 `run_resumed`, then checks the checkpoint ID/step/target and a
journal-bound digest of the complete terminal Verifier and usage summary. It does not reconstruct
action arguments or execute tools. Consequently action replay match rate is `N/A` in v3 results.

The durable path uses the Runtime's injectable monotonic clock. It rechecks the accumulated
deadline after Policy, tool, Observation, and Verifier return and before checkpoint, interruption,
and terminal success boundaries. A backoff greater than or equal to the remaining time is rejected
without sleeping or publishing a resumable checkpoint.

## Crash window and consequences

This protocol demonstrates recovery only after the formal checkpoint and its journal commit are
present. It does not make the side effect and checkpoint one transaction. A process killed after
the write but before that commit can leave changed Workspace state without a resumable checkpoint.
M3A also does not promise recovery from a kill during checkpoint replacement, after resume starts,
or at an arbitrary tool boundary. It does not provide directory-level `fsync`, cross-machine
coordination, concurrent writers, a distributed lock, or production exactly-once semantics.

The field allowlists are a fixed-schema data-minimization boundary, not a general secret scanner,
log DLP system, encrypted journal, or access-control layer. Action replay and its match-rate metric
remain M3B work.
