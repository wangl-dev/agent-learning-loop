# Agent Learning Loop

> Status: **M4A Workspace corpus / pre-alpha (`0.1.0.dev0`)**. Ten project-authored synthetic
> tasks now have strict manifests, a fixed `6/2/2` split, and content fingerprints checked before
> execution. This is corpus and system-correctness work, not a model benchmark.

Agent Learning Loop studies a narrow question: under the same task, actions, seed, and injected
failure schedule, which Runtime safeguards improve recovery without causing duplicate side
effects? M1 established the fault-free path. M2 now adds the first controlled comparison:

```text
Task v1 + scripted Action → Runtime config and state machine → fixed failure schedule
                          → retry / run-local idempotency → state Verifier
                          → Runtime Event JSONL v2 + Runtime Result JSON v2
```

The implementation follows `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.7 in the separate planning
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

Validate the packaged corpus before running it:

```powershell
python -m agent_learning_loop validate-corpus
```

The command reports only schema, environment, total task count, and split counts. It does not print
fixture file bodies or private expected state. Run all ten Workspace tasks:

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

For the fixed M3B smoke test, explicitly record action references on an uninterrupted source,
then replay them into a different, empty directory:

```powershell
python -m agent_learning_loop run-durable `
  --task workspace.fix-config `
  --mode safeguarded `
  --failure-schedule workspace.lost-write-result.v1 `
  --checkpointing off `
  --record-actions on `
  --output-dir run-output/m3b-source

python -m agent_learning_loop replay-actions `
  --source-run-dir run-output/m3b-source `
  --output-dir run-output/m3b-replay
```

The second command prints `1/1 vertical-slice smoke` on a complete match. Its result should show
action and step counts `2/2`, matching final state and Verifier, Policy calls `0`, and physical
write/side-effect/duplicate counts `1/1/0`. `--record-actions` defaults to `off`; it is rejected for
an interrupted or checkpoint-on source.

No model, GPU, API key, database, container, or network service is used after Python dependencies
are installed.

## Workspace corpus and the M1 execution path

M1 introduced the first three fault-free fixtures. M4A keeps their behavior and brings the same
execution path to ten tasks:

| Split | Task ID | State contract |
|---|---|---|
| train | `workspace.build-summary` | Build an exact summary; preserve both inputs |
| train | `workspace.merge-changelog` | Merge ordered fragments; preserve source fragments |
| train | `workspace.repair-service-map` | Repair one mapping; preserve unrelated entries and note |
| train | `workspace.create-owner-record` | Create a missing record; preserve its sources |
| train | `workspace.build-deploy-manifest` | Build an exact multi-input manifest; create no draft |
| train | `workspace.reconcile-inventory` | Reconcile controlled counts; preserve audit inputs |
| validation | `workspace.update-status` | Update status; preserve audit log and create no backup |
| validation | `workspace.normalize-checklist` | Normalize checklist text; preserve comment and archive |
| test | `workspace.fix-config` | Correct one config field; preserve port and note |
| test | `workspace.update-route` | Update one route; preserve fallback, ordering, and note |

The scripted Policy selects from project-authored, versioned action catalogs keyed by task ID. It
never receives the fixture's private setup or expected-state object. Its passing result means the
modules fit together and the fixtures are solvable; it does not measure Agent intelligence.

Each task has a separate manifest that binds its task, fixture, catalog, split, seed, budgets,
safety constraints, verifier, scenario family, tags, provenance, and Apache-2.0 license. Fixture
and catalog identities are SHA-256 digests of sorted, whitespace-free UTF-8 canonical JSON.
`validate-corpus` checks strict schemas, the fixed task/split mapping, one-to-one resource coverage,
identities, fingerprints, tool allowlists, and scenario-family split isolation before execution.
These unkeyed digests catch silent replacement or mismatched package data; they are not signatures.
[ADR 0006](docs/decisions/0006-m4a-corpus-governance.md) records why governance metadata stays
outside the Policy-visible Task and private fixture.

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

## What M3B adds

The fixed source can append a separate `actions.jsonl` v1 without changing the accepted M3A event,
checkpoint, or result schemas. Each logical action record contains a catalog reference and
canonical fingerprints, not raw arguments, file content, Observation, or Tool Result. The
lost-result write still has two Runtime attempts, but it remains one logical action.

`replay-actions` validates the complete M3A trajectory, source identity, action hash chain,
catalog golden fingerprint, step/tool/attempt relationships, final Workspace, and Verifier binding
before it constructs an execution component. It then resolves the two references against the
packaged catalog and executes each Action once in a new Workspace. It neither asks Policy for a
new decision nor reproduces the source retry, injected failure, idempotency hit, or checkpoint.

After every Action, replay compares the Workspace digest recorded by the source. It also compares
the final snapshot and complete Verifier digest, and checks that the source directory's file list,
sizes, and bytes did not change. A valid execution mismatch is kept as a structured result with
match rate `0.0`; malformed or overlapping input is rejected before execution. [ADR 0005](docs/decisions/0005-m3b-reference-action-replay.md)
records the reference, isolation, privacy, and match boundaries.

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
python -m agent_learning_loop validate-corpus
python -m agent_learning_loop run-workspace --task all --output-dir run-output/corpus
python -m build --no-isolation
```

Tests cover strict corpus and v1/v2/v3 schemas, manifest identity/fingerprint/split/resource failures,
legal and illegal Runtime transitions, deadline return boundaries, three budgets, canonical failure
fingerprints and injection count, retry/non-retry rules,
idempotency hit/conflict behavior, step/attempt event association, paired scenarios, deterministic
reruns, durable hash-chain tampering, safe-boundary resume, action-catalog and action-journal
tampering, reference-only replay, source immutability, private expected-state separation, M1 path
attacks, and subprocess exit codes. The build
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
- M3B replays only two project-authored references for one uninterrupted, checkpoint-off source.
  It does not accept arbitrary action JSON, rerun Policy, reproduce attempt/failure history, replay
  resumed sources, or establish model determinism. The reported `1/1` is not a cross-task rate.
- Action and journal SHA-256 fingerprints expose no raw arguments, but they are still unkeyed
  consistency checks, not signatures, secret protection, or proof against full-artifact rewriting.
- The ten-task `6/2/2` corpus is fixed, synthetic, and small. The scripted Policy may run every task
  to test the system pipeline, but that does not estimate model quality or prevent a future model
  development process from misusing the split.
- Existing `run-runtime` remains the M2 v2 path and still writes its JSONL at normal run end.
- The Policy is scripted; there is no model adapter or model-quality claim.
- The three paired cases are regression evidence, not an M5 batch experiment, statistical report,
  p50/p95 benchmark, or model evaluation.
- GitHub Actions checks pushes and pull requests; it is a project gate, not deployment evidence.
- Incident, DataOps, post-training export, training, and the simulated FDE case are not part of M4A.

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
