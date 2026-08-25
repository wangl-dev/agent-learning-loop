# ADR 0009: Pre-register Eval cells and validate bundles without execution

- Status: accepted for M5A
- Date: 2026-08-25

## Context

M1 through M4C can execute three scripted environments and three controlled Runtime mechanisms,
but isolated command outputs are easy to compare with different task sets or denominators. A
summary can also look internally consistent after its raw evidence has been changed. M5A needs a
repeatable batch layer without changing the frozen runners or claiming model capability.

## Decision

Package three versioned suite manifests. The system suite has the exact 30 published corpus tasks.
The reliability suite has seven fixed cells and three complete single-mechanism pairs. The recovery
suite has checkpoint-off refusal, checkpoint-on second-process resume, an uninterrupted reference,
and the fixed M3B 1/1 action-replay diagnostic.

Each run records its caller-supplied source commit, selected and candidate denominators, packaged suite
fingerprints, normalized records, exact aggregation, deterministic Markdown, and hashes for every
raw artifact. State-verifier success and Runtime completion remain separate. Expected baseline
failures stay in the denominator and are judged against their pre-registered oracle. M5A does not
resolve that source commit from Git; canonical revision attribution belongs to M5B.

The validator is a separate read-only path. It reloads packaged identities, parses the complete raw
results, events, audits, and final Workspace with strict public models. For the three system
environments it starts from the packaged private setup and applies the fixed catalog to a pure
in-memory projection: every tool-result payload, action-linked audit record, and state transition
must match before final state and saved verifier fields are accepted. DataOps transaction IDs,
operation and row facts, primary-key/cardinality evidence, and every before/after digest are
independently recomputed. The validator then regenerates records and summaries and checks the exact
Markdown and artifact inventory. It may use read-only verifier logic, but must not construct a
Policy or Environment, execute tools or runners, start a process, open SQLite or another temporary
database, or use the network.

## Consequences

- A pair cannot be reported with one missing arm or a changed task, schedule, seed, resource, or
  budget identity.
- Numerators, denominators, rates, N/A fields, raw results, normalized records, and Markdown are
  cross-checked instead of trusting a saved summary.
- Physical executions and physical writes remain separate exact metrics; Markdown exposes system
  environment/split counts, aggregate effects, and every registered pair delta.
- Identical inputs produce identical bundle bytes, apart from choosing a different explicit source
  commit.
- SHA-256 provides damage and consistency detection, not authorship or tamper-proof signing.
- M5A emits run-local evidence bundles only. It does not generate or commit the canonical v0.1
  report, run a model, estimate token cost, or begin M5B training/data work.
