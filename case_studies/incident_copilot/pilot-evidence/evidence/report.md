# Eval bundle

- source commit: `a808ab5ee1b9420cfcc3a1f585e2b94491d7cdaa`
- suites: `system-correctness-v1`
- selected denominator: `10/30`
- selected environment: `incident`
- selected split: `all`
- selected tag: `all`
- selected pair: `all`

## Aggregate exact metrics

- verifier state success: `10/10 (1)`
- Runtime completion: `N/A`
- duplicate side effects: `N/A`
- physical executions: `N/A`
- physical writes: `N/A`
- retries: `N/A`
- idempotency hits: `N/A`
- model: N/A
- token cost: N/A
- latency: observed/non-comparable

## System correctness by environment

| environment | verifier passed |
|---|---:|
| incident | 10/10 |

## System correctness by split

| split | verifier passed |
|---|---:|
| test | 2/2 |
| train | 6/6 |
| validation | 2/2 |

## Reliability cells

No Runtime reliability cell is selected in this bundle.

## Paired comparisons

No paired comparison is selected in this bundle.

## Diagnostics

No recovery/replay diagnostic is selected in this bundle.

## Oracle deviations

No selected cell deviated from its pre-registered oracle.

## Limitations

Scripted system-correctness cells do not measure Agent or model capability.
Expected naive Runtime failures remain in the denominator and can satisfy the oracle.
The replay result is a fixed 1/1 vertical-slice diagnostic, not an aggregate rate.
Single-run latency is observed/non-comparable; no p50 or p95 is reported.
SHA-256 detects damage and inconsistent artifacts; it is not a signature.
The source commit was explicitly supplied by the caller; M5B will attribute the canonical run to a checked Git revision.
