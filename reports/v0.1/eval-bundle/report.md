# Eval bundle

- source commit: `a00da937e299c99031f7f4711da5dd3eeef50e22`
- suites: `system-correctness-v1, runtime-reliability-v1, recovery-replay-v1`
- selected denominator: `41/41`
- selected environment: `all`
- selected split: `all`
- selected tag: `all`
- selected pair: `all`

## Aggregate exact metrics

- verifier state success: `38/40 (0.95)`
- Runtime completion: `6/10 (0.6)`
- duplicate side effects: `1/11 (0.0909091)`
- physical executions: `22/11 (2)`
- physical writes: `10/11 (0.909091)`
- retries: `4/7 (0.571429)`
- idempotency hits: `1/7 (0.142857)`
- model: N/A
- token cost: N/A
- latency: observed/non-comparable

## System correctness by environment

| environment | verifier passed |
|---|---:|
| dataops | 10/10 |
| incident | 10/10 |
| workspace | 10/10 |

## System correctness by split

| split | verifier passed |
|---|---:|
| test | 6/6 |
| train | 18/18 |
| validation | 6/6 |

## Reliability cells

| cell | terminal | state | completion | executions | writes | duplicates | retries | hits | error |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| transient.naive | FAILED | false | false | 0 | 0 | 0 | 0 | 0 | tool_transient |
| transient.retry | SUCCEEDED | true | true | 3 | 1 | 0 | 1 | 0 | N/A |
| timeout.naive | TIMED_OUT | false | false | 1 | 0 | 0 | 0 | 0 | timeout |
| timeout.retry | SUCCEEDED | true | true | 3 | 1 | 0 | 1 | 0 | N/A |
| lost.naive | FAILED | true | false | 2 | 1 | 0 | 0 | 0 | tool_transient |
| lost.retry | SUCCEEDED | true | true | 3 | 2 | 1 | 1 | 0 | N/A |
| lost.idempotent | SUCCEEDED | true | true | 2 | 1 | 0 | 1 | 1 | N/A |

## Paired comparisons

| pair | completion Δ | verifier Δ | physical execution Δ | physical write Δ | duplicate Δ | retry Δ | idempotency-hit Δ |
|---|---:|---:|---:|---:|---:|---:|---:|
| transient-retry | 1 | 1 | 3 | 1 | 0 | 1 | 0 |
| timeout-retry | 1 | 1 | 2 | 1 | 0 | 1 | 0 |
| lost-result-idempotency | 0 | 0 | -1 | -1 | -1 | 0 | 1 |

## Diagnostics

- `recovery.checkpoint-off`: passed — fixed recovery diagnostic
- `recovery.checkpoint-on`: passed — fixed recovery diagnostic
- `recovery.reference`: passed — fixed recovery diagnostic
- `recovery.action-replay`: passed — 1/1 vertical-slice diagnostic

## Oracle deviations

No selected cell deviated from its pre-registered oracle.

## Limitations

Scripted system-correctness cells do not measure Agent or model capability.
Expected naive Runtime failures remain in the denominator and can satisfy the oracle.
The replay result is a fixed 1/1 vertical-slice diagnostic, not an aggregate rate.
Single-run latency is observed/non-comparable; no p50 or p95 is reported.
SHA-256 detects damage and inconsistent artifacts; it is not a signature.
The source commit was explicitly supplied by the caller; M5B will attribute the canonical run to a checked Git revision.
