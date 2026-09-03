# Technical tour

Use this map to follow one fixed failure schedule into the Runtime, its recorded evidence, and the
later data/probe artifacts. It does not turn a scripted experiment into a production agent or a
model-quality claim.

```text
failure schedule
  -> controlled environment and strict tools
  -> runtime journal/checkpoint/idempotency
  -> trajectory and verifier
  -> batch evaluation and failure analysis
  -> SFT candidate / simulated delivery / model probe
```

## Code map

These links are the code entry points behind the chain above:

- [failure schedules](../src/agent_learning_loop/failure_schedules.py), [Runtime](../src/agent_learning_loop/runtime.py), and [durable Runtime](../src/agent_learning_loop/durable_runtime.py);
- [Workspace](../src/agent_learning_loop/workspace.py), [Incident runner](../src/agent_learning_loop/incident_runner.py), and [DataOps runner](../src/agent_learning_loop/dataops_runner.py);
- [Eval runner](../src/agent_learning_loop/eval_runner.py) and [read-only Eval validator](../src/agent_learning_loop/eval_validator.py);
- [SFT exporter](../src/agent_learning_loop/sft_exporter.py) and [SFT validator](../src/agent_learning_loop/sft_validator.py);
- [simulated FDE runner](../src/agent_learning_loop/fde_case_runner.py) and [FDE validator](../src/agent_learning_loop/fde_case_validator.py);
- [model-probe runner](../src/agent_learning_loop/model_probe_runner.py), [strict parser](../src/agent_learning_loop/model_probe_parser.py), and [probe validator](../src/agent_learning_loop/model_probe_validator.py).

This is not a file inventory. Each link marks the first place to inspect a specific evidence
boundary.

## Failure schedule and controlled tools

The project fixes task, seed, tool/action catalog, and—in the reliability slice—a schedule: a
transient read failure, logical timeout, or a write whose effect succeeds before its result is lost.
Workspace has three path-confined UTF-8 file tools; Incident is an in-memory simulator with
action-bound approval and idempotency; DataOps is a disposable SQLite task database with structured
operations and transaction checks. These are Python-level controlled boundaries, not production
integrations, an operating-system sandbox, or a general SQL agent.

## Runtime, trajectory, and verification

The scripted Policy selects a catalog action. The Runtime owns state transitions, budgets, failure
classification, retry, idempotency, and terminal status, so retry is not a new policy decision.

### Failure-and-repair example: a lost write result

In `lost.naive`, a write takes effect but its result is lost. State verification is true while
Runtime completion is false. Retrying without idempotency runs the write twice. In the safeguarded
arm, the successful Tool Result is saved before injected loss, so retry reuses it and reduces
physical writes from 2 to 1 and duplicate side effects from 1 to 0. This is not a production
exactly-once guarantee; inspect [failure analysis](../reports/v0.1/FAILURE_ANALYSIS.md) and
[ADR 0003](decisions/0003-m2-runtime-failure-boundary.md).

The durable path flushes hash-linked JSONL events and atomically publishes a constrained checkpoint.
`validate-trajectory` checks events without executing actions. `replay-actions` checks source
identity then runs two fixed references once in a new Workspace. The `1/1 vertical-slice` is one
narrow diagnostic, not a cross-task rate. [ADR 0004](decisions/0004-m3a-safe-boundary-recovery.md)
and [ADR 0005](decisions/0005-m3b-reference-action-replay.md) describe the limits.

## Batch evidence

The [M5 evaluator contract](decisions/0009-m5a-eval-bundle-contract.md) pre-registers 41 cells:
30 system-correctness, seven reliability, and four recovery/replay diagnostics. `validate-eval` is
read-only: it parses raw events/audits, independently projects each fixed catalog, rebuilds summary,
and checks hashes. It does not call a policy, Environment, tool, subprocess, SQLite, network, or
GPU. The [evidence index](../reports/v0.1/README.md) records 30/30 scripted system correctness and
separate verifier-state/Runtime-completion fields. The bundle source is
`9dca2508aff1a772cbdc9452f9ae1bb85925a2b9`; fingerprint
`b038c84c83b121484ed527a67503f78039741e225cca6898f5a2e6974cb24833`.

## Downstream artifacts

The [simulated Incident pilot](../case_studies/incident_copilot/README.md) records 10/10 registered
contracts, 4/4 held-out contracts, 3/3 controls, 10/10 Incident safety, and zero unauthorized
high-impact executions. It has no customer, rollout, ROI, SLA, or production metric.

The [SFT development candidate](../datasets/sft-development-v1/DATA_CARD.md) projects 18
scripted-oracle demonstrations, six per environment. Validation/test tasks are excluded; six
pre-registered samples passed human review, while 12 were not individually reviewed. It is not
training-ready and does not establish model improvement.

## Prediction-only model probe

M7C-A supplies correct history for 21 validation prefixes and asks for one next tool call. The
reference action is validator-only and predictions are never executed. CI uses an offline fake
backend; the local smoke keeps raw prompts, generations, weights, cache, and real bundles out of
the repository.

### Failure-and-repair example: reference action leakage

The parser needed stronger handling for a reference action encoded as JSON inside ordinary prose.
It now scans bounded JSON string/object/array candidates, decodes at most two nested JSON strings,
and compares parsed tool identity and arguments. Reference actions are rejected; quoted text,
relative paths, and unrelated JSON remain controls. This is a prompt-contract repair, not a broad
prompt-injection security claim.

The recorded feasibility observations are Qwen3-0.6B `1/10` and Qwen3-1.7B `4/10`, with `0/3`
all-prefix tasks for each. They show a local protocol/parser/capacity path, not end-to-end success,
model ranking, production selection, or training improvement. See [setup](model-probe-setup.md) and
[ADR 0015](decisions/0015-m7ca-local-model-probe-contract.md).

## Inspect locally

```powershell
python -m agent_learning_loop validate-eval `
  --run-dir reports/v0.1/eval-bundle
python -m agent_learning_loop run-workspace `
  --task workspace.build-summary `
  --output-dir run-output/one-task
python -m agent_learning_loop validate-fde-case `
  --run-dir case_studies/incident_copilot/pilot-evidence
```
