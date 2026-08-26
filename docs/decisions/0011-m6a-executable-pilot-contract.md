# ADR 0011: Put an executable acceptance contract before delivery packaging

- Status: accepted for M6A implementation
- Date: 2026-08-26
- Scope: simulated `incident-copilot-v1` pilot only

## Context

The repository already has ten strict Incident fixtures and a 30-cell system Eval that can select
those ten cells. A case study written only as prose could drift away from that evidence, while a
second Incident runner or verifier would create two competing safety contracts. M6A needs a
reviewable delivery-shaped entry point without claiming a real customer or production rollout.

## Decision

Define one packaged, fingerprinted simulated case. It fixes the ten existing Incident cell/task,
split, fixture, catalog, and manifest identities and partitions them into three acceptance
controls. `run-fde-case` makes exactly one existing Incident-filtered `run_eval` call. The outer
bundle adds only a run manifest, machine-readable acceptance, and a deterministic report around
the unchanged nested Eval evidence.

`validate-fde-case` remains read-only. It calls the normal Eval validator first, then derives the
10/4/3 denominators, full Incident safety, and unauthorized high-impact count from saved records,
results, audits, and packaged approval identities. It independently rebuilds the outer report,
inventory digest, and pilot fingerprint and checks that source bytes did not change.

M6A and M6B are separate. M6A establishes whether the evidence contract is executable and
tamper-evident. M6B may later add discovery, deployment, handoff, or adoption materials only after
this contract is reviewed; it cannot retroactively turn synthetic evidence into a customer fact.

## Consequences

The normal happy path has fixed denominators and deterministic bytes. A structurally valid miss is
preserved as acceptance drift, while malformed evidence is exit 2 and an incomplete new output is
cleaned up. Source commit attribution remains an explicit caller input rather than an inferred Git
fact. Model performance, latency, ROI, SLA, adoption, and manual baseline time stay `N/A`.

The case is intentionally narrow. It does not add a new Environment, customer connector,
production permission model, canonical pilot artifact, or package release.

## Rejected alternatives

- A prose-only case study was rejected because its claims would not be mechanically tied to raw
  evidence and fixed denominators.
- A second Incident executor or verifier was rejected because it would duplicate and potentially
  weaken the frozen Incident/Eval contracts.
- Treating all ten cells as one average was rejected because a held-out or safety failure could be
  hidden by other passes.
- Creating canonical pilot output in M6A was rejected because implementation review must precede
  publication and attribution.
