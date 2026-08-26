# ADR 0012: Generate the delivery pack from published executable evidence

- Status: accepted for M6B implementation
- Date: 2026-08-26
- Scope: simulated `incident-copilot-v1` delivery pack only

## Context

M6A published an executable case definition, runner, validator, exact path contract, and Linux CI at
commit `a808ab5ee1b9420cfcc3a1f585e2b94491d7cdaa`. A delivery pack written from an uncommitted working
tree could describe behavior that no public revision reproduces. A copied summary or screenshot
would also hide the raw result/event/audit evidence used to derive acceptance.

No customer, external operator, ticket system, monitoring source, approval service, deployment
system, or service-control connector exists. Documentation must not turn an offline scripted case
into a deployment story.

## Decision

Build a wheel from a clean export of the public M6A commit, install only that wheel and its runtime
dependencies in a new environment, and use its public CLI to generate the tracked canonical pilot.
Keep the complete 37-file directory: three outer files, four nested Eval top-level files, and 30 raw
result/event/audit files. Do not hand-edit generated JSON, JSONL, or Markdown.

Run the same pure wheel into a second fresh system-temporary directory and require identical
relative path inventory, lengths, SHA-256 values, and file bytes. Add the narrow
`case_studies/incident_copilot/pilot-evidence/** -text` rule so Git preserves the generator's bytes
instead of applying a text line-ending filter.

Write the delivery documents only after provenance, normal validation, and byte reproduction pass.
The architecture shows the real offline chain—caller/CLI, packaged case, existing
Incident-filtered Eval, raw artifacts, Eval validator, and FDE validator/acceptance. External
systems are listed separately as `not connected`.

## Consequences

The canonical pilot can be reviewed from a public source revision and reproduced without relying on
the later delivery-document working tree. CI can validate the committed copy read-only and compare a
fresh run byte for byte. SHA-256 and exact paths reveal inconsistent or extra evidence, but they are
not a signature against an actor able to replace code and rewrite the full bundle.

The eight documents remain a simulated delivery pack. Rollout and rollback beyond local output are
conditional plans. Field feedback is a synthetic task review. Adoption, manual baseline, ROI, SLA,
production latency/cost, and model performance remain `N/A / not measured`. Package version,
Incident/Eval contracts, the M5 evidence release, and the existing tag/Pre-release do not change.

## Rejected alternatives

- Generating from the same unpublished delivery-pack code was rejected because public-source
  attribution would be circular.
- Keeping only acceptance or a screenshot was rejected because it removes the raw evidence and
  exact inventory needed for independent review.
- Handwriting or re-signing generated artifacts was rejected because it would not prove the public
  runner produced them.
- Drawing placeholder external connectors in the success path was rejected because none is
  implemented or authorized.
- Inventing customer adoption, business metrics, or production outcomes was rejected because those
  facts were not measured.
- Creating another tag, Release, or asset was rejected because M6B is a candidate milestone and has
  no external publication authorization.
