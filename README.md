# Agent Learning Loop

> Status: **M3A safe-boundary recovery / pre-alpha (`0.1.0.dev0`)**. The repository now pairs a
> durable event journal with one fixed post-Observation checkpoint/resume experiment. It does not
> claim arbitrary-crash recovery, action replay, or production exactly-once execution.

Agent Learning Loop studies a narrow question: under the same task, actions, seed, and injected
failure schedule, which Runtime safeguards improve recovery without causing duplicate side
effects? M1 established the fault-free path. M2 now adds the first controlled comparison:

```text
Task v1 + scripted Action → Runtime config and state machine → fixed failure schedule
                          → retry / run-local idempotency → state Verifier
                          → Runtime Event JSONL v2 + Runtime Result JSON v2
```

The implementation follows `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.5 in the separate planning
repository. Milestones are reviewed against fresh evidence; contract, task, or metric changes are
versioned instead of being silently absorbed.

## Quick Start

Create a Python 3.11+ environment and install the package with its development checks:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On macOS or Linux, activation is `source .venv/bin/activate`.

Run all three M1 tasks:

```powershell
python -m agent_learning_loop run-workspace --task all --output-dir run-output/m1
```

The command prints one short status line per task. Machine-readable files are kept under one
directory per task. For example:

```powershell
Get-Content run-output/m1/workspace_fix-config/events.jsonl
Get-Content run-output/m1/workspace_fix-config/result.json
Get-Content run-output/m1/workspace_fix-config/workspace/app.conf
```

Run a single fixed task by ID:

```powershell
python -m agent_learning_loop run-workspace `
  --task workspace.build-summary `
  --output-dir run-output/one-task
```

Run the same transient-read schedule first with the fail-fast baseline, then with bounded retry
and idempotency. The naive command is expected to return a nonzero exit code and still write a
machine-readable failure result:

```powershell
python -m agent_learning_loop run-runtime `
  --task workspace.build-summary `
  --mode naive `
  --failure-schedule workspace.transient-read.v1 `
  --output-dir run-output/m2-naive

python -m agent_learning_loop run-runtime `
  --task workspace.build-summary `
  --mode safeguarded `
  --failure-schedule workspace.transient-read.v1 `
  --output-dir run-output/m2-safeguarded
```

Inspect `result.json` for the terminal state, real mode switches, Error Record, budgets, attempt
counts, physical/side-effect executions, schedule fingerprint, and Verifier result. `events.jsonl`
contains state changes, attempts, injected failures, retry scheduling, and idempotency hits. M2
uses `attempt=0` for events outside a tool attempt. Attempt events carry the same attempt number in
their top-level field and payload; retry events name both the failed and next attempt. M2 still
writes the full JSONL only when the run ends; it is not crash-safe append-only storage.

Run the fixed M3A pair. Both first commands intentionally stop after step 2 with exit code 6. The
checkpoint-off resume is expected to return validation error 2 without changing its journal or
Workspace:

```powershell
python -m agent_learning_loop run-durable `
  --task workspace.fix-config `
  --mode safeguarded `
  --failure-schedule workspace.lost-write-result.v1 `
  --interruption-schedule workspace.post-write-boundary.v1 `
  --checkpointing off `
  --output-dir run-output/m3a-off

python -m agent_learning_loop resume-runtime --run-dir run-output/m3a-off
python -m agent_learning_loop validate-trajectory --run-dir run-output/m3a-off
```

With checkpointing on, run `resume-runtime` as a second Python process. It validates the saved
identity, journal prefix, counters, idempotency entry, and Workspace digest before constructing an
Environment, Policy, Tool, or Verifier. The second command should exit 0 and append segment 1 to
the same run:

```powershell
python -m agent_learning_loop run-durable `
  --task workspace.fix-config `
  --mode safeguarded `
  --failure-schedule workspace.lost-write-result.v1 `
  --interruption-schedule workspace.post-write-boundary.v1 `
  --checkpointing on `
  --output-dir run-output/m3a-on

python -m agent_learning_loop resume-runtime --run-dir run-output/m3a-on
python -m agent_learning_loop validate-trajectory --run-dir run-output/m3a-on
```

The uninterrupted reference omits `--interruption-schedule` and uses `--checkpointing off`. Its
final Workspace and Verifier result should match the resumed run:

```powershell
python -m agent_learning_loop run-durable `
  --task workspace.fix-config `
  --mode safeguarded `
  --failure-schedule workspace.lost-write-result.v1 `
  --checkpointing off `
  --output-dir run-output/m3a-reference
```

No model, GPU, API key, database, container, or network service is used after Python dependencies
are installed.

## What M1 actually checks

The three project-authored synthetic fixtures cover different final-state rules:

| Task ID | Action | Verifier boundary |
|---|---|---|
| `workspace.fix-config` | Read and correct `app.conf` | Correct content; unrelated note unchanged |
| `workspace.build-summary` | Read two inputs and write a fixed summary | Exact artifact; inputs unchanged |
| `workspace.update-status` | List, read, and update one status file | Target state; audit log unchanged; no backup |

The scripted Policy contains finite actions keyed by task ID. It never receives the fixture's
private setup or expected-state object. Its passing result means the modules fit together and the
fixtures are solvable; it does not measure Agent intelligence.

Task, Observation, Action, Tool Result, Event, and Run Result use strict Pydantic v2 schemas.
Unknown critical fields, missing fields, and wrong types fail validation. Environment, Tool,
Policy, and Verifier are small `typing.Protocol` contracts checked with fake implementations. See
[ADR 0002](docs/decisions/0002-pydantic-v2-schema-boundary.md) for the dependency and version
boundary.

## What M2 adds

The Runtime is the execution controller, not the Policy. It enforces legal state changes, checks
budgets before another decision or tool attempt, classifies errors without matching human-readable
messages, and prevents work after a terminal state. A step is one new Policy decision; a retry
reuses the same Action and consumes another tool-call attempt, not another step.

| Mode | Retry | Run-local idempotency | Intended comparison |
|---|---:|---:|---|
| `naive` | off | off | Fail-fast baseline |
| `retry_only` | on, at most two attempts | off | Shows the duplicate-write failure |
| `safeguarded` | on, at most two attempts | on | Reuses a saved successful result |

The three packaged schedules are project-authored and deterministic:

| Schedule | Task and injection | Paired observation |
|---|---|---|
| `workspace.transient-read.v1` | First `read_text`, before execution | naive fails; safeguarded retries once |
| `workspace.logical-timeout.v1` | First `read_text`, before execution | naive times out; safeguarded retries inside its deadline |
| `workspace.lost-write-result.v1` | First `write_text`, after success | state changes before Runtime receives the result |

The Runtime accepts only these three packaged canonical schedules. Their task, seed, target tool,
occurrence, phase, failure kind, error category, retryability, provenance, and schema version are
locked by reviewed SHA-256 fingerprints. A conflicting caller-supplied fingerprint is rejected
instead of overwritten. A run that otherwise reaches normal verification must also prove that its
fixed failure was injected exactly once.

The lost-result case separates final state from execution correctness. In naive mode, `app.conf`
can already contain the required text and the Verifier can pass, but the Runtime still reports
`FAILED` because it never received a successful result or reached `VERIFYING`. With `retry_only`,
the physical write runs twice and the duplicate-side-effect counter is one. With `safeguarded`,
the first successful Tool Result is stored before the simulated loss; retry hits the same
idempotency key, so physical and side-effect execution remain one.

M2's timeout is a controlled logical failure plus monotonic deadline checks. The Runtime rechecks
the deadline after Policy, tool, Observation, and Verifier calls return and before terminal
success. It refuses a retry backoff that would consume the remaining deadline. Tests inject a fake
clock and fake sleeper, so these checks do not wait in real time. This does not interrupt an
arbitrary blocking Python call; threads, processes, async cancellation, and production sandbox
claims remain outside this milestone. [ADR 0003](docs/decisions/0003-m2-runtime-failure-boundary.md)
records the error, schedule, clock, lost-result, idempotency, and schema-v2 decisions.

## What M3A adds

`run-durable` is a deliberately narrow v3 path for `workspace.fix-config`, safeguarded mode, and
the lost-write-result schedule. It appends and flushes each JSONL record as it occurs. Every record
contains a continuous sequence number, segment, previous hash, and its own canonical SHA-256 hash.

The one packaged interruption fires after the step-2 write result has been observed. With the
checkpoint switch on, the Runtime first writes a field-minimized checkpoint to a temporary file,
flushes it, atomically replaces `checkpoint.json`, appends and flushes `checkpoint_committed`, and
only then appends the interruption record. Resume continues with the same run ID and accumulated
step, tool-call, failure, retry, idempotency, and elapsed counters. In the accepted success path,
the physical write and side effect each remain one and the duplicate counter remains zero.
The checkpoint ID is a canonical SHA-256 digest of every checkpoint field except the ID itself.
Creation, validation, and resume use the same calculation, so a changed counter, elapsed value,
idempotency entry, Workspace digest, or journal prefix no longer remains attached to the old ID.
This unkeyed digest detects damage and cross-artifact mismatch; it is not a signature or proof that
someone with write access did not replace all related files and hashes.

`validate-trajectory` is event replay, not action replay. It reads schemas, sequence/hash links,
state transitions, exact segment-0/interruption/segment-1 ordering, checkpoint relationships, and
a journal-bound digest of the full Verifier and usage summary. It never reruns an action and
reports action replay match rate as `N/A`. The durable path also reuses M2's injectable monotonic
clock checks: an expired boundary or backoff that would consume the remaining deadline cannot
publish an accepted partial checkpoint or a successful result.
[ADR 0004](docs/decisions/0004-m3a-safe-boundary-recovery.md) records the write ordering, identity
checks, and crash window.

## Workspace boundary

M1 exposes only three UTF-8 text tools: list files, read a file, and write a file. Each user path
must be relative, cannot contain `..`, and must still resolve under the run's Workspace root.
Absolute paths and symlinks resolving outside that root are rejected before a read result or
outside mutation is produced.

This is a Python-level controlled boundary, not an operating-system sandbox. M1 has no shell,
delete, move, network, database, or arbitrary code tool. A determined process with direct Python
access is outside this threat model.

## Development checks

Run the same project gates configured in CI:

```powershell
python -m agent_learning_loop --version
python -m ruff check .
python -m mypy src tests
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q
python -m build --no-isolation
```

Tests cover strict v1/v2/v3 schemas, legal and illegal Runtime transitions, deadline return boundaries,
three budgets, canonical failure fingerprints and injection count, retry/non-retry rules,
idempotency hit/conflict behavior, step/attempt event association, paired scenarios, deterministic
reruns, durable hash-chain tampering, safe-boundary resume, private expected-state separation, M1
path attacks, and subprocess exit codes. The build
command creates ignored wheel and source distributions under `dist/`.

## Current limitations

- M3A recovers only from its committed step-2 post-Observation checkpoint. A kill before the
  checkpoint, between side effect and checkpoint, during the checkpoint protocol, or after resume
  begins is not promised to recover exactly once. Directory-level fsync, distributed locking,
  cross-machine recovery, and arbitrary `kill -9`/power-loss guarantees are outside this slice.
- The v3 field allowlists minimize persisted data for the fixed experiment. They are not general
  secret detection, log DLP, encryption, or permission hardening.
- Journal and checkpoint SHA-256 digests are unkeyed consistency checks, not signatures,
  authentication, or protection from an actor who can rewrite every related artifact.
- Event replay validates recorded structure; it does not execute actions or calculate an action
  replay match rate. Action replay belongs to M3B and is not implemented here.
- Existing `run-runtime` remains the M2 v2 path and still writes its JSONL at normal run end.
- The Policy is scripted; there is no model adapter or model-quality claim.
- The three paired cases are regression evidence, not an M5 batch experiment, statistical report,
  p50/p95 benchmark, or train/dev/test split.
- CI configuration exists, but there is no remote Actions run because this repository has no
  remote yet.
- Incident, DataOps, post-training export, training, and the simulated FDE case are not part of M2.

## Project background and evidence boundary

Public job descriptions from several AI teams mention Agent harnesses, runtimes, evaluation,
trajectories, reliability, and post-training data. Those pages are public role signals, not a
universal internal hiring rubric or evidence that any company uses this project. Focusing on a
CPU-first reliability experiment is a project strategy shaped by those signals and the available
single-laptop hardware, not a claim about employer endorsement.

M0's packaging choices remain recorded in
[ADR 0001](docs/decisions/0001-m0-python-foundation.md). The repository uses Apache-2.0; future
third-party code, tasks, or data must retain their own provenance and license information.

## License

Copyright 2026 wangl-dev.

Licensed under the [Apache License 2.0](LICENSE).
