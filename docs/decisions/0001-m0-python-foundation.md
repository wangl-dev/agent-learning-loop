# ADR 0001: Keep the M0 Python foundation small and strict

- Status: Accepted for M0
- Date: 2026-08-17
- Proposal baseline: `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.2

## Context

M0 must prove that the project can be installed, invoked, checked, tested, and built on
Python 3.11+ without a model, API key, database, or external service. It must also leave a
clean base for later milestones without pretending that Agent behavior already exists.

## Decision

- Use a `src/` package layout so tests and local commands exercise the installed package
  instead of importing accidentally from the repository root.
- Use setuptools through standard `pyproject.toml` metadata. It is widely available, supports
  editable installs and wheel/sdist builds, and avoids adding a packaging-specific workflow.
- Keep runtime dependencies empty in M0. The version-only CLI uses standard-library
  `argparse`; a larger CLI framework would not yet replace enough custom behavior to justify
  its dependency and update surface.
- Use mypy in strict mode for static type checking. Its Python integration and local command
  are simple enough for this package, while a second type checker would duplicate the gate.
- Use Ruff for lint/import checks and pytest for behavior tests. CI runs the same user-facing
  commands documented in the README.
- Mark the package `0.1.0.dev0` and Pre-Alpha. M0 is a foundation, not a v0.1 release.

## Consequences

- Packaging and imports are checked early, with slightly more directory structure than a
  flat package.
- The package has no runtime dependency cost at M0.
- Future milestones may add dependencies only when an implemented, tested behavior justifies
  them. A change to core project contracts or milestone scope remains an L2 proposal decision.
- The version currently appears in both package code and project metadata; future release
  automation may centralize it after there is evidence that release maintenance warrants the
  added mechanism.

## Alternatives considered

- `uv` as the only installer: rejected because the proposal requires the standard
  `venv` + `pip` path to remain usable.
- Typer for the version command: deferred because `argparse` completely covers M0.
- Pyright instead of mypy: viable, but mypy keeps the gate within the Python toolchain and is
  sufficient for the current package.
- Hatchling or another build backend: viable, but adds a second packaging convention without
  an M0 benefit.
