# ADR 0002: Use Pydantic v2 at the M1 JSON boundary

- Status: Accepted for M1
- Date: 2026-08-19
- Proposal baseline: `AGENT_LEARNING_LOOP_PROPOSAL.md` v1.3

## Context

M1 loads versioned task fixtures and writes events and terminal results as JSON. Missing fields,
wrong types, or silently ignored fields would make later runs hard to compare. The three file
tools also need structured arguments and JSON-safe results. Hand-written `dict` checks would
repeat type and required-field logic across loaders, tools, tests, and the CLI.

M1 does not need schema migration machinery or an M2 error taxonomy. It needs one strict v1
boundary that can round-trip JSON and fail early.

## Decision

- Add `pydantic>=2.10,<3` as the only runtime dependency introduced in M1.
- Define Task, Observation, Action, Tool Result, Event, Run Result, Workspace fixture, snapshot,
  and verifier result models with Pydantic v2's public `BaseModel` API.
- Reject unknown fields and Python-side implicit coercion with a shared strict configuration.
- Keep tool-specific argument validation beside each tool. Tool responses are then validated as
  JSON-safe `ToolResult` payloads before they become events.
- Keep public Task data separate from private setup and expected state. The Policy receives only
  Task and Observation; event and result schemas do not contain the private fixture model.
- Pin the dependency below Pydantic 3. A future major-version move requires contract tests and an
  explicit compatibility decision rather than an automatic upgrade.

## Consequences

- The package now installs Pydantic and `pydantic-core`; importing M1 schema modules has a larger
  startup and wheel footprint than standard-library dataclasses.
- Validation errors and JSON serialization use one maintained implementation instead of custom
  recursive checks. Round-trip, missing-field, wrong-type, and unknown-field behavior is covered
  by tests.
- Pydantic validates structure, not Workspace authorization. Resolved-path containment remains a
  separate environment rule and is tested independently.
- M1 events are collected in memory and written to JSONL at the end of a run. This is intentionally
  not the append-only trajectory writer, checkpoint, or replay behavior reserved for M3.

## Alternatives considered

- Dataclasses plus hand-written JSON checks: no runtime dependency, but every nested field and
  unknown-key rule would need custom code and duplicate tests.
- JSON Schema plus a separate validator: useful for cross-language exchange later, but M1 would
  still need typed Python objects and conversion code. No current adapter requires that extra
  boundary.
- Pydantic internals or a custom plugin: rejected. M1 uses only public v2 APIs and does not add a
  plugin system.
