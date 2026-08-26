# ADR 0010: Freeze complete canonical evidence from the published evaluator

- Status: accepted for M5B candidate
- Date: 2026-08-26

## Context

The M5A evaluator and its M5A.1 byte-portability correction were public before selecting a v0.1
report. The evidence source is fixed to commit
`a00da937e299c99031f7f4711da5dd3eeef50e22`. That ordering separates the measuring program from
the evidence later chosen for publication. A copied summary table would still be weak evidence:
it would not preserve the registered denominator, raw results, suite identity, or renderer input,
and a reader could not check whether expected failures were removed.

The M5A manifest records generator package `0.1.0.dev0` and proposal contract `1.10`. Changing the
evaluator, oracle, suite fingerprints, or version strings in M5B would make the report come from a
different measuring program and break the attribution boundary.

## Decision

Commit the complete, unedited 41-cell output directory under `reports/v0.1/eval-bundle`. Fix its
source commit to the published M5A.1 evaluator-plus-portability commit. Keep all four top-level
models and the 163 manifest-listed raw artifacts rather than extracting a preferred table or
screenshot.

Before editing any tracked business file, generate that directory with the normal `run-eval`
command and accept it with the normal read-only validator. Run the same selection in a second,
new system-temporary directory and require the full 167-file relative inventory and all 421,449
file bytes to match. The repo-level regression repeats the all-suite run and exact comparison; CI
also runs a clearly named validation step against the committed directory without overwriting it.

Human-facing documents read metrics from the canonical models and link negative results to raw
relative paths. They must keep verifier state success separate from Runtime completion, executions
separate from writes, all three fixed pair deltas visible, and action replay labeled as a `1/1`
vertical-slice diagnostic.

## Consequences

- The selected evidence can be validated without rerunning an Environment or trusting a copied
  README number.
- Byte reproducibility detects nondeterministic output, missing raw evidence, and accidental edits.
- The committed directory is larger than a summary-only report but remains small: 167 files and
  421,449 bytes, with no file near the 1 MiB limit.
- The source commit, package version, proposal version, suite fingerprints, and expected negative
  results remain those of the published evaluator. M5B does not improve its own result by changing
  the measurement contract.
- SHA-256 and byte comparison are consistency checks, not signatures, trusted timestamps, or proof
  against an actor able to rewrite repository history and every related file.
- A deterministic scripted 30/30, seven fixed reliability cells, and four diagnostics do not show
  model intelligence, statistical significance, production safety, customer adoption, exactly-once
  execution, or a general replay rate.
- This is a v0.1 evidence candidate, not a package version bump, Git tag, GitHub Release, or release
  asset. Those external publishing actions require a later, explicit authorization.
