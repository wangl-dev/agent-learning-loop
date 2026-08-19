# Agent Learning Loop

> Status: **M1 Workspace vertical slice / pre-alpha (`0.1.0.dev0`)**. The repository can run three
> deterministic file tasks from validated fixtures to state-based verification. It is a
> fault-free system-correctness path, not a model benchmark or evidence of Runtime reliability.

Agent Learning Loop studies a narrow question: under the same task, actions, seed, and injected
failure schedule, which Runtime safeguards improve recovery without causing duplicate side
effects? The current M1 does not test that question yet. It establishes the smallest real path
needed before M2 can add a naive Runtime and controlled failures:

```text
Task fixture → scripted Policy → validated Tool → controlled Workspace
             → state Verifier → Event JSONL + Run Result JSON
```

The implementation follows `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.3 in the separate planning
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

Tests cover strict schema behavior, four Protocol contracts, legal file operations, path attacks,
state verifier failures, all three end-to-end tasks, deterministic reruns, private expected-state
separation, and a real subprocess CLI. The build command creates ignored wheel and source
distributions under `dist/`.

## Current limitations

- There is no Runtime state machine, budget, timeout, injected failure, retry, idempotency,
  checkpoint, resume, replay, or paired ablation. Those are later milestones.
- JSONL is written once at the end of the run. It is not yet an append-only trajectory store.
- The Policy is scripted; there is no model adapter or model-quality claim.
- The three tasks are a system fixture set, not an Eval benchmark or train/dev/test split.
- CI configuration exists, but there is no remote Actions run because this repository has no
  remote yet.
- Incident, DataOps, post-training export, training, and the simulated FDE case are not part of M1.

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
