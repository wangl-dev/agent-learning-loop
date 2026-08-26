# v0.1 canonical evidence candidate

This directory freezes one complete run of the published M5A evaluator. Here, "canonical" means
the run selected as this repository's v0.1 baseline. It does not mean an industry benchmark,
statistical significance, or a model-quality result.

The bundle was generated without editing its output files. A second `--suite all` run in a new
system-temporary directory produced the same 167 relative files and the same 421,449 bytes. The
repo-level test repeats that execution and compares every file byte for byte.

## Provenance

| Field | Value |
|---|---|
| Source commit | `a00da937e299c99031f7f4711da5dd3eeef50e22` |
| Generator package | `0.1.0.dev0` |
| Generator proposal contract | `1.10` |
| Selection | `all`, 41 of 41 registered cells |
| Raw artifacts in manifest | 163 |
| Complete bundle inventory | 167 files, 421,449 bytes |
| Bundle fingerprint | `aefc0385680f827bbf45887a1ef335cb93f2826e16539e570f2f56c3028a8856` |

Suite fingerprints:

- `system-correctness-v1`: `624dfb19c2b9575056dd9d24a92e3dcb4852617eb538ee3541fb28cae933488e`
- `runtime-reliability-v1`: `a8c5e2389ce1bbe31ae7895ecbfe211be3460aee40563c4c13efb0523d89ac2e`
- `recovery-replay-v1`: `4fb499de8c42ac2d78aaa962c6e6fda2419e7df5fda45c64287d5b78d23b9a97`

The manifest intentionally keeps the generator's real package and proposal versions. M5B did not
change the evaluator, suites, oracle, fingerprints, or package version to make the report look
like a later release.

## Fast read-only validation

After installing the repository, validate the committed evidence without executing a task,
Environment, tool, runner, subprocess, SQLite database, or network call:

```powershell
python -m agent_learning_loop validate-eval `
  --run-dir reports/v0.1/eval-bundle
```

The expected result is `valid`, source commit `a00da937...`, selected cells `41`, source bytes
unchanged, and execution calls `0`.

## Full reproduction

Run the same fixed selection into a directory that does not already exist:

```powershell
python -m agent_learning_loop run-eval `
  --suite all `
  --source-commit a00da937e299c99031f7f4711da5dd3eeef50e22 `
  --output-dir run-output/reproduced-v0.1

python -m agent_learning_loop validate-eval `
  --run-dir run-output/reproduced-v0.1
```

The repository regression performs the complete inventory and byte comparison:

```powershell
python -m pytest -q tests/test_canonical_eval_report.py
```

## Result map

All 41/41 cells matched their pre-registered oracle. That total consists of 30 scripted system cells,
seven Runtime reliability cells, and four fixed recovery/replay diagnostics. Expected naive
failures remain in the 41-cell denominator.

| View | Exact result | Meaning |
|---|---:|---|
| System correctness | 30/30 | Project-authored scripted actions satisfied state verifiers |
| Workspace / Incident / DataOps | 10/10 each | Each synthetic environment's fixed corpus completed |
| System split | 18/18 train, 6/6 validation, 6/6 test | Fixed corpus identity, not model generalization |
| Reliability oracle | 7/7 | Each Runtime result, including expected failures, matched its oracle |
| Recovery/replay diagnostics | 4/4 | Fixed diagnostic contracts passed |
| Verifier state success | 38/40 | Boolean state outcomes only; one diagnostic is N/A |
| Runtime completion | 6/10 | Boolean Runtime outcomes only; system cells and action replay are N/A |
| Physical executions | 22/11 | Sum across seven reliability and four recovery records |
| Physical writes | 10/11 | Writes are separate from reads and idempotency hits |

The aggregate state and completion denominators differ by design. The system suite does not claim a
generic Runtime completion field, checkpoint-off has no final state-verifier result, and action
replay is not a Runtime completion trial.

### Pre-registered pairs

| Pair | Baseline | Mechanism | Exact delta |
|---|---|---|---|
| Transient retry | `transient.naive`: completion/state false, executions/writes 0/0 | `transient.retry`: true/true, 3/1 | completion +1, verifier +1, executions +3, writes +1, retries +1 |
| Logical-timeout retry | `timeout.naive`: false/false, 1/0 | `timeout.retry`: true/true, 3/1 | completion +1, verifier +1, executions +2, writes +1, retries +1 |
| Lost-result idempotency | `lost.retry`: true/true, executions/writes 3/2, duplicate 1 | `lost.idempotent`: true/true, 2/1, duplicate 0 | executions -1, writes -1, duplicate -1, idempotency hit +1 |

Only the named mechanism changes inside each pair. Seven fixed cells and three pairs are regression
evidence, not a sample large enough for statistical extrapolation.

### Recovery and replay diagnostics

| Cell | Expected observation |
|---|---|
| `recovery.checkpoint-off` | Controlled interruption; resume correctly refused; completion false |
| `recovery.checkpoint-on` | A second Python process resumes successfully; final verifier true |
| `recovery.reference` | Uninterrupted reference succeeds and matches the resumed final state |
| `recovery.action-replay` | Fixed source-to-new-Workspace action replay matches `1/1` |

The final row is one vertical-slice diagnostic, not an overall replay match rate.

## Where the evidence lives

- [`eval-bundle/eval-manifest.json`](eval-bundle/eval-manifest.json) binds selection, source commit,
  suite fingerprints, raw inventory hashes, and the bundle fingerprint.
- [`eval-bundle/records.jsonl`](eval-bundle/records.jsonl) holds one normalized record per cell.
- [`eval-bundle/summary.json`](eval-bundle/summary.json) contains exact numerators, denominators,
  reliability cells, pair deltas, and diagnostics.
- [`eval-bundle/report.md`](eval-bundle/report.md) is the deterministic renderer output.
- [`FAILURE_ANALYSIS.md`](FAILURE_ANALYSIS.md) follows three negative results back to specific raw
  files and explains what they do and do not establish.

The raw evidence comes only from project-authored Apache-2.0 synthetic tasks. The bundle contains
no database file, private expected fixture, durable checkpoint outside its registered recovery
artifact, credential, traceback, environment-variable dump, or local absolute path. SHA-256 checks
detect damaged or inconsistent files; they are not a signature against an actor who can replace
the whole repository.

Model, token cost, and model latency remain `N/A`. The run does not support claims about model
intelligence, production reliability, strong sandboxing, real databases or incidents, customer
adoption, exactly-once execution, or statistical significance.
