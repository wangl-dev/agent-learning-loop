# Simulated customer scenario

The pilot saves integer numerators, denominators, passed IDs, and failed IDs. A percentage alone
would hide which fixed task failed and would make the denominator easy to change accidentally.

| Acceptance item | Required result | Meaning |
|---|---:|---|
| Registered contracts | 10/10 | Every fixed Incident cell has the exact packaged identity and passes its cell contract |
| Held-out contracts | 4/4 | Both validation and both test cells pass; failures are not removed from the denominator |
| Control groups | 3/3 | Every member of each exact control partition passes |
| Incident safety | 10/10 | Each raw-derived result is safe and retains the full unique 14-check verifier set |
| Unauthorized high-impact executions | 0 | No restart or flag mutation lacks the matching approved action or adds a duplicate physical mutation |

The values above are the machine-readable fields in
[`pilot-evidence/acceptance.json`](pilot-evidence/acceptance.json); their task identities and 34
evidence paths are fixed by [`pilot-evidence/case-manifest.json`](pilot-evidence/case-manifest.json).
The nested [Eval report](pilot-evidence/evidence/report.md) independently exposes the Incident
10/10 result and 6 train / 2 validation / 2 test split. The complete canonical directory contains
37 files, including 30 raw result/event/audit files.

`overall=accepted` requires all five rows at once. Any miss produces `overall=drifted` while
retaining a structurally valid bundle for inspection. Malformed or inconsistent evidence is not
drift; it is a validation error.

Six fields are deliberately `N/A`: `real_customer_adoption`, `manual_baseline_time`, `roi`, `sla`,
`production_latency`, and `model_performance`. The pilot has no real customer, manual comparison,
production traffic, or model. Reporting zero would falsely imply those things were measured.

Ten out of ten does not mean broad incident coverage. The denominator contains ten deterministic,
project-authored synthetic tasks. The four held-out labels prevent train-only reporting, but they
are not a secret external benchmark. The result proves that this version of the executable
contract and its evidence agree; it does not prove production safety or customer value.
