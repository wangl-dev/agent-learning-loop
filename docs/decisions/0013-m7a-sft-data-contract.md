# ADR 0013: Bind SFT candidates to public Eval evidence before training

- Status: accepted for M7A implementation
- Date: 2026-08-27
- Scope: train-only scripted-oracle SFT development candidates

## Context

The published system-correctness suite has 30 synthetic tasks across Workspace, Incident, and
DataOps. Each environment persists a different raw event shape. Their fixtures also contain setup,
answer state, protected-state rules, approval rules, and data used only by the verifier. Copying a
fixture or a whole Eval run into JSONL would mix public observations with answers and held-out
content.

The Runtime reliability suite has paired mechanism arms, but those arms compare retry and
idempotency settings. They are not two model responses to the same prompt and therefore are not
valid DPO chosen/rejected pairs.

## Decision

Export only the 18 pre-registered train tasks, six per environment. Count the six validation and
six test tasks as excluded without serializing their instructions, actions, tool results, or
fixture content. Use three strict normalizers to emit one provider-neutral sequence: an
`assistant_action` followed by the matching raw `tool_result` for every scripted step.

Build task context through an explicit public-field allowlist. Keep the packaged manifest,
fixture, and catalog fingerprints as resource identities, but do not copy fixture-only state,
expected answers, verifier fields, audit records, catalog action references, or run IDs. A
synthetic approval ID returned by the Incident tool is an observation and may be retained; the
fixture's approval rule is not exported.

Require a normal, complete 30-cell source Eval. The exporter calls the existing read-only Eval
validator before deriving samples. The dataset validator receives both directories, regenerates
the expected 18 samples from packaged public resources and raw events, and compares the exact
four-file bundle. Neither path executes an Environment, Policy, tool, runner, subprocess, SQLite,
socket, network service, model, or GPU.

M7A writes only `development_candidate` bundles to caller-selected new directories. It does not
record an exporter commit, commit a dataset, bind a provider chat template, call a tokenizer, or
start training. Runtime reliability arms and recovery diagnostics are excluded, and preference
pair count remains zero.

## Consequences

- The training denominator is explicit: 18 eligible train tasks and 12 held-out exclusions.
- Workspace lifecycle events and Incident/DataOps action/result streams share one strict sample
  contract without changing any frozen environment or Eval schema.
- Tool observations remain traceable to source artifact paths and SHA-256 values while setup and
  answer state stay outside the sample.
- Re-signing a changed sample or manifest does not make it valid; validation rebuilds the expected
  bytes from the supplied, normally validated source Eval.
- Two exports from the same source produce the same four paths and bytes. SHA-256 detects
  inconsistent content; it is not an authorship signature or protection from a party that can
  replace every related artifact and the validator.
- The candidate is too small to support a model-quality, generalization, or production claim.

## Rejected alternatives

- Exporting all 30 tasks was rejected because validation/test content must remain held out.
- Copying complete fixtures or result files was rejected because they include answer and verifier
  material that a future policy must not see.
- Treating Runtime arms as DPO pairs was rejected because mechanism ablations are not preference
  labels.
- Binding one provider's chat template or tokenizer was rejected because M7A defines semantic
  turns before model selection.
- Trusting dataset self-reported hashes was rejected because a jointly rewritten bundle can remain
  internally consistent.
- Publishing a tracked canonical dataset was rejected because M7A is a contract milestone, not the
  authorized M7B data-generation or training stage.
