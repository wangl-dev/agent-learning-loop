# ADR 0006: Keep corpus governance separate from execution fixtures

- Status: accepted for M4A implementation
- Date: 2026-08-21

## Context

The first three Workspace fixtures were enough to test one vertical slice, but task identity lived
mostly in loader code and filenames. Expanding to ten tasks without another contract would make it
easy to move a task between splits, replace a catalog, or omit wheel data without a clear failure.
The public `Task` must also stay free of private expected state and benchmark administration fields.

## Decision

Each packaged Workspace task has three separate resources: an execution fixture, a scripted action
catalog, and a governance manifest. The manifest contains only identities, canonical SHA-256
fingerprints, the fixed split, budgets, safety constraints, verifier ID, scenario family, tags, and
structured provenance. It contains no file bodies, expected state, action arguments, observations,
or tool results.

One fail-closed validator enumerates all three resource directories before execution. It requires
the fixed ten-task `6/2/2` mapping, strict schemas, one-to-one references, matching fingerprints,
catalog tools within each Task allowlist, and no scenario family shared across splits. Source and
installed-wheel validation use the same package-resource entry point. The task and Policy loaders
consume only the validated result; local `--task-file` input is never added to the corpus.

Putting governance fields into `Task` was rejected because Policy does not need split, license, or
fingerprint data. Putting private expected state into manifests was rejected because validation
summaries and future evaluation tooling should not expose answer data. Silently updating a digest
was rejected because it would turn identity drift into an accepted rewrite.

## Consequences

Adding or changing a task now requires an intentional fixture, catalog, manifest, and tests. A
missing or mismatched wheel resource stops before Workspace, Policy, Tool, or Verifier execution.
The extra files and repeated validation add small local overhead, which is acceptable for ten
tasks. SHA-256 is an unkeyed consistency check, not authentication. The split is governance for
future evaluation; the current scripted ten-task pass is only a system-correctness baseline.
