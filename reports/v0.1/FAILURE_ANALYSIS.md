# Failure analysis for the v0.1 canonical evidence

The canonical run contains expected failures on purpose. Removing them would change the registered
denominator and hide the distinction between final state and execution protocol. The paths below
are relative to [`eval-bundle/`](eval-bundle/).

## A transient read fails fast without retry

`transient.naive` injects the packaged `workspace.transient-read.v1` failure before the first
`read_text` execution. Its normalized record reports terminal `FAILED`, error category
`tool_transient`, verifier state `false`, Runtime completion `false`, zero physical executions, and
zero writes. The raw Runtime result and event sequence are:

- `runs/runtime-reliability-v1/transient.naive/result.json`
- `runs/runtime-reliability-v1/transient.naive/events.jsonl`

The paired `transient.retry` cell keeps task, action catalog, seed, failure schedule, resource,
budgets, and idempotency setting fixed. Bounded retry is the only mechanism change. It completes
with verifier state `true`, three physical executions, one physical write, and one retry:

- `runs/runtime-reliability-v1/transient.retry/result.json`
- `runs/runtime-reliability-v1/transient.retry/events.jsonl`

The pair delta is completion `+1`, verifier `+1`, physical executions `+3`, physical writes `+1`,
retry `+1`, duplicate side effects `0`, and idempotency hits `0`. This is a deterministic regression
for one schedule. It does not estimate a retry success probability for arbitrary tools or faults.

## Logical timeout is a controlled schedule, not forced cancellation

`timeout.naive` reaches terminal `TIMED_OUT` after one physical execution and no write. Its paired
`timeout.retry` cell completes after three executions, one write, and one retry. Raw evidence:

- `runs/runtime-reliability-v1/timeout.naive/result.json`
- `runs/runtime-reliability-v1/timeout.naive/events.jsonl`
- `runs/runtime-reliability-v1/timeout.retry/result.json`
- `runs/runtime-reliability-v1/timeout.retry/events.jsonl`

The exact delta is completion `+1`, verifier `+1`, executions `+2`, writes `+1`, retries `+1`, with
no duplicate or idempotency-hit change. The timeout is injected by a project-authored logical
schedule and checked with a deterministic clock. It demonstrates deadline accounting and bounded
retry after calls return. It cannot interrupt an arbitrary blocked Python function, kill a process,
or prove production timeout isolation.

## Lost result separates correct state from Runtime completion

`lost.naive` is the clearest reason the report keeps two success columns. The `write_text` side
effect happens before the simulated result is lost. The final Workspace therefore satisfies its
state verifier (`true`), but the Runtime never receives success or reaches its normal verification
state, so completion is `false` and terminal is `FAILED`:

- `runs/runtime-reliability-v1/lost.naive/result.json`
- `runs/runtime-reliability-v1/lost.naive/events.jsonl`

Writing a single “task success” value would erase this mismatch. The canonical summary therefore
reports verifier state success as `38/40` and Runtime completion as `6/10`, using only records where
the respective field is Boolean.

The registered idempotency pair starts from `lost.retry`, not the unpaired context cell. Retry-only
completes, but it physically writes twice and records one duplicate side effect:

- `runs/runtime-reliability-v1/lost.retry/result.json`
- `runs/runtime-reliability-v1/lost.retry/events.jsonl`

With run-local idempotency enabled, `lost.idempotent` also completes and reaches the same verified
state, while physical executions fall from 3 to 2, writes from 2 to 1, duplicates from 1 to 0, and
idempotency hits rise from 0 to 1:

- `runs/runtime-reliability-v1/lost.idempotent/result.json`
- `runs/runtime-reliability-v1/lost.idempotent/events.jsonl`

This supports a narrow claim: for the fixed lost-result retry inside one process, the saved result
prevents the second physical write. It is not production exactly-once. The idempotency store is
run-local, and M3A only adds recovery at one committed post-Observation checkpoint. Crashes between
a side effect and that checkpoint, concurrent workers, external systems, and malicious artifact
replacement remain outside the evidence.

## What the 41/41 result means

Every cell matched its pre-registered oracle, including naive failures and checkpoint-off refusal.
It means the evaluator observed the outcomes that the published suite contract specified. The
30/30 system slice uses project-authored scripted catalogs on project-authored synthetic tasks; it
does not measure a learned Agent. The seven reliability cells are three fixed pairs plus one
context cell, and the M3B result is a single `1/1` vertical slice. There are no repeated random
samples, confidence intervals, model tokens, model cost, or comparable model latency from which to
claim statistical or model-quality improvement.
