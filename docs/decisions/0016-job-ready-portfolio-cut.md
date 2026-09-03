# ADR 0016: Put the evidence trail before additional roadmap work

- Status: accepted
- Date: 2026-09-03

## Context

The repository contains a fixed Runtime/Eval experiment, synthetic environments, durable recovery,
action replay, a simulated Incident delivery case, an SFT development candidate, and a local
next-action probe. The root README had accumulated implementation history in one long page.

## Decision

Freeze the functional and evidence surface for this portfolio cut. The root README is a short
entry point for problem, method, evidence links, reproduction, and limits; the causal explanation
and failure examples live in `docs/technical-tour.md`. A narrow regression test prevents later
edits from silently removing links or turning scripted/simulated results into model, customer, or
training claims.

The historical `v0.1.0-evidence.1` pre-release remains an M5 evidence snapshot, not a release
containing later M6/M7 work.

## Consequences

This changes no `src`, CI, package metadata, task suite, canonical artifact, or recorded outcome.
It does not declare M7C-B/C/D complete or authorize model training, benchmark claims, GitHub
metadata changes, or production scope.
