# Agent Learning Loop

[![CI](https://github.com/wangl-dev/agent-learning-loop/actions/workflows/ci.yml/badge.svg)](https://github.com/wangl-dev/agent-learning-loop/actions/workflows/ci.yml)

Agent Learning Loop is an offline reliability harness for tool-using agents. It studies whether a
runtime can preserve a verifiable account when tool calls time out, fail transiently, lose results,
repeat, or resume.

```text
fixed failure schedule
  -> controlled environment and strict tools
  -> runtime journal, checkpoint, retry, and idempotency
  -> trajectory and verifier
  -> batch evaluation and failure analysis
```

## Evidence at a glance

The latest public [CI run](https://github.com/wangl-dev/agent-learning-loop/actions/runs/33159157569)
completed with Linux `563 passed`. This is repository-test evidence, not a benchmark score.

| Evidence | Exact result | Boundary |
|---|---:|---|
| Scripted system correctness | [30/30 fixed cells](reports/v0.1/README.md) | Not model quality or production reliability |
| Reliability mechanics | [41-cell canonical bundle](reports/v0.1/eval-bundle/report.md) | Not a statistical performance sample |
| Delivery framing | [10/10 simulated Incident safety](case_studies/incident_copilot/pilot-evidence/acceptance.json) | Not a customer, ROI, SLA, or deployment |
| SFT data boundary | [18 candidates](datasets/sft-development-v1/DATA_CARD.md); human review 6/6 sampled | Not training-ready or a training result |
| Local-model feasibility | [Qwen3 probe](docs/model-probe-setup.md): 0.6B `1/10`, 1.7B `4/10`; both `0/3` all-prefix tasks | Not end-to-end success, ranking, or training benefit |

The local probe supplies correct scripted history and checks one next action; predictions are never
executed. The `1/10` and `4/10` observations are single-run feasibility evidence, not a model
benchmark. Raw prompts, generations, weights, caches, and real-smoke bundles are not tracked.

- **30 seconds:** read the table and [failure analysis](reports/v0.1/FAILURE_ANALYSIS.md).
- **10 minutes:** trace the runtime and evidence chain in the [technical tour](docs/technical-tour.md).
- **30 minutes:** run one synthetic task, then validate committed evidence without execution.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m agent_learning_loop validate-corpus
python -m agent_learning_loop run-workspace `
  --task workspace.build-summary `
  --output-dir run-output/one-task
python -m agent_learning_loop validate-eval `
  --run-dir reports/v0.1/eval-bundle
```

The last command does not execute an Environment or tool. To reproduce rather than only validate:

```powershell
python -m agent_learning_loop run-eval `
  --suite all `
  --source-commit 9dca2508aff1a772cbdc9452f9ae1bb85925a2b9 `
  --output-dir run-output/reproduced-v0.1
python -m agent_learning_loop validate-eval --run-dir run-output/reproduced-v0.1
```

The pre-registered oracle is `41/41`. The 41-cell bundle source is `9dca2508aff1a772cbdc9452f9ae1bb85925a2b9`; its fingerprint is
`b038c84c83b121484ed527a67503f78039741e225cca6898f5a2e6974cb24833`. Its recovery/replay result
is a `1/1 vertical-slice`, not a rate. Its canonical aggregate keeps verifier-state success at
`38/40` and Runtime completion at `6/10`; these fields are intentionally not collapsed into one
success rate.

For the fixed `lost-result-idempotency` pair, the retry-only arm has physical executions `3 → 2`,
physical writes `2 → 1`, duplicate side effects `1 → 0`, and idempotency hits `0 → 1` after the
idempotency safeguard is enabled. This is one paired Runtime observation under one injected
schedule, not a production exactly-once claim; the [failure analysis](reports/v0.1/FAILURE_ANALYSIS.md)
keeps its raw evidence and limits visible.

## Evidence and reproduction

- [Evidence index](reports/v0.1/README.md), [report](reports/v0.1/eval-bundle/report.md), and [failure analysis](reports/v0.1/FAILURE_ANALYSIS.md).
- [Simulated Incident pilot](case_studies/incident_copilot/README.md) and [acceptance](case_studies/incident_copilot/pilot-evidence/acceptance.json).
- [SFT data card](datasets/sft-development-v1/DATA_CARD.md), [candidate report](datasets/sft-development-v1/candidate/report.md), and [human review](datasets/sft-development-v1/HUMAN_REVIEW.md).
- [Local Qwen3 setup](docs/model-probe-setup.md) and [probe decision](docs/decisions/0015-m7ca-local-model-probe-contract.md).

The historical GitHub pre-release `v0.1.0-evidence.1` is an M5 evidence snapshot, predating M6 and
M7 work. Use these links for current evidence.

## Current boundary

Everything is offline, scripted, synthetic, and deliberately narrow. The FDE work is simulated;
there is no real customer, deployment, operator, approval system, ROI, SLA, or production data.
The SFT material has 18 scripted-oracle demonstrations (six per environment). The
[data card](datasets/sft-development-v1/DATA_CARD.md) documents the M7A generation-time boundary;
M7B versions the same generated bytes, while the [generated candidate report](datasets/sft-development-v1/candidate/report.md)
remains not a tracked dataset. The guided human review is complete: 6/6 pre-registered samples passed,
covering 6/18 candidate samples; the remaining 12 were not individually reviewed. It remains not training-ready.

There is a provider-neutral local-model probe but no live policy execution or end-to-end model
benchmark. M7C-B/C/D baselines, task expansion, and training are backlog work. [ADR 0016](docs/decisions/0016-job-ready-portfolio-cut.md)
records this portfolio cut.

## Development checks

```powershell
python -m agent_learning_loop --version
python -m ruff check .
python -m mypy src tests
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -q
python -m build --no-isolation
```

## License

Copyright 2026 wangl-dev. Licensed under the [Apache License 2.0](LICENSE).
