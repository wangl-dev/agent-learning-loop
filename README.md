# Agent Learning Loop

> Status: **M0 foundation / pre-alpha (`0.1.0.dev0`)**. This repository currently provides
> packaging, a version CLI, automated checks, and project governance only. It does **not** yet
> implement an Agent, Environment, Runtime, tools, evaluation, model adapters, or training.

Agent Learning Loop is planned as a reproducible experiment platform for controlled tool-using
agents: tasks should be resettable, actions auditable, final state verifiable, and failures
replayable before trajectories are used as learning data.

The implementation follows `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.2 from the separate planning
repository. The proposal is versioned: each milestone is reviewed against fresh evidence, and
changes to scope, interfaces, tasks, or metrics must be recorded rather than silently moving the
goalposts.

## Why this project

Public job descriptions from several AI teams expose responsibilities around Agent harnesses,
runtimes, evaluation, trajectories, reliability, and post-training data. Those pages are public
role signals, not evidence of a universal internal hiring rubric or company endorsement of this
project.

The project direction is our strategy inference from those signals and from a practical resource
constraint: the primary development machine can support rigorous CPU-first systems and evaluation
work, while large distributed RL is outside the current evidence boundary. The intended project
therefore prioritizes a no-key, deterministic path before optional model or training work.

## M0 scope

This milestone establishes only:

- a Python 3.11+ package using a `src/` layout;
- `python -m agent_learning_loop --version`;
- one real subprocess-level CLI behavior test;
- Ruff, strict mypy, pytest, and package-build checks;
- an offline-with-respect-to-models CI path: no model download, API key, paid API, database, or
  external service is used by project checks;
- an Apache-2.0 license and a lightweight architecture decision record.

See [ADR 0001](docs/decisions/0001-m0-python-foundation.md) for the M0 foundation tradeoffs.

## Quick Start

From a clean checkout, create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On macOS or Linux, activation is `source .venv/bin/activate`.

Check the installed module entry point:

```console
$ python -m agent_learning_loop --version
agent-learning-loop 0.1.0.dev0
```

No model, GPU, API key, or network service is required after the development dependencies have
been installed.

## Development checks

Run the same gates used by CI:

```powershell
python -m agent_learning_loop --version
python -m ruff check .
python -m mypy src
python -m pytest
python -m build
```

The build command creates wheel and source distributions under the ignored `dist/` directory.

## Evidence-driven evolution

Milestones are intentionally sequential. M0 establishes the engineering base; later work is
accepted only after its own tests and review. Experiment failures, resource measurements, task
leakage, verifier weaknesses, and external feedback may justify keeping, modifying, deleting, or
deferring planned work. Contract-level changes require a versioned proposal update, and old
results are not overwritten to make new metrics look better.

## Current limitations

- There is no task schema, controlled environment, runtime state machine, trajectory, replay,
  verifier, evaluation CLI, or dataset export yet.
- CI configuration is present but cannot be described as remotely green until it runs on a
  published remote; local commands are the current evidence.
- The repository makes no production-readiness, customer-adoption, model-quality, benchmark, or
  training claim.
- The planned Incident Copilot is a future simulated case study, not a real customer deployment.

## License

Copyright 2026 wangl-dev.

Licensed under the [Apache License 2.0](LICENSE). Third-party code, tasks, and data added in future
milestones must retain their own provenance and license information.
