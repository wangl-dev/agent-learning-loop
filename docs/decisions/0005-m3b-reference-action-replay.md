# ADR 0005: Reference-based action replay

- Status: accepted for M3B implementation
- Date: 2026-08-21

## Context

M3A can validate a durable event trajectory and resume one checkpoint, but its public artifacts
deliberately exclude action arguments and Tool Results. Re-executing the event journal would either
need those missing values or would have to ask Policy for a new decision. The first choice weakens
the content-minimization boundary; the second is a policy rerun, not action replay.

M3B asks a narrower question for the uninterrupted `workspace.fix-config` reference run: can two
known logical Actions be identified without persisting their arguments, executed once in a new
Workspace, and compared with their original step and final states?

## Decision

The three scripted tasks use packaged, versioned action catalogs. A catalog contains the full
strict Actions as project source and has a golden canonical SHA-256 fingerprint. Policy and replay
load this same source. A recorded run writes only catalog identity, action references, action
fingerprints, step/tool metadata, attempt counts, and Workspace digests to a separate
`actions.jsonl` v1 hash chain. It does not change the accepted M3A event v3, checkpoint v1, or
result v3 schemas.

Recording is opt-in and defaults off. It is allowed only for the fixed uninterrupted,
checkpoint-off durable source. The normal source has six records: source start, two action
started/finished pairs, and source finish. The lost write result creates two tool attempts in the
M3A event journal, but only one logical write in the action journal. Source finish binds the event
final hash, result-summary digest, final Workspace digest, complete Verifier digest, and action
count.

Replay resolves both recorded references against the validated packaged catalog. It never calls
Policy and does not reproduce failure injection, retry, idempotency, or checkpoint behavior. Each
Action executes once in a new, nonnested Workspace. The source is read-only and is compared before
and after replay by relative entry name, type, size, and file SHA-256.

A match requires both references and both per-step Workspace digests, equal final snapshot and
complete Verifier, a passing Verifier, one physical write and side effect, no duplicate, no Policy
call, and an unchanged source. The fixed task reports either `1/1` with rate `1.0` or `0/1` with
rate `0.0`. A valid execution mismatch keeps its sandbox and writes a result. Invalid source,
catalog, or path input fails before Environment, Tool, or Verifier construction and writes no
completed replay result.

## Consequences

The action artifact does not duplicate raw arguments, Tool Results, file bodies, private expected
state, host paths, or exceptions. The catalog itself contains project-authored synthetic Action
definitions and ships as package data; it is trusted code/data, not caller-controlled replay input.

The SHA-256 chains and fingerprints detect damage and cross-artifact mismatch but are not keyed
authentication. Someone able to replace all related files can construct a new self-consistent set.
This slice also says nothing about arbitrary model Actions, external side effects, resumed sources,
failure-history replay, batch match rates, or production exactly-once execution. Those boundaries
remain outside M3B.
