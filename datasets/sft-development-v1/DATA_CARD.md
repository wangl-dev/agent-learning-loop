# SFT Development Candidate v1 Data Card

- Stage: `development_candidate`
- Canonical files: [`candidate/`](candidate/)
- Human review: [complete — 6 of 6 pre-registered samples passed](HUMAN_REVIEW.md)
- Generator source: `8a4016a9c154238cd7e5df5d1a3ed8fd194dd10d`
- Provenance: `project-authored-synthetic`
- License: Apache-2.0

This is a fixed development snapshot of scripted demonstrations. It is not a model benchmark,
training result, production dataset, or claim that the samples are ready for training. Automated
checks are complete. Guided human review is complete for 6 of 18 candidate samples.

## Generation and reproduction

The canonical directory was generated before any M7B tracked edits. The public source commit was
exported without `.git`, built as the pure Python wheel
`agent_learning_loop-0.1.0.dev0-py3-none-any.whl`, and installed by itself with runtime
dependencies in a new environment. That installation ran:

```powershell
python -m agent_learning_loop run-eval `
  --suite system-correctness `
  --source-commit 8a4016a9c154238cd7e5df5d1a3ed8fd194dd10d `
  --output-dir <new-source-eval>
python -m agent_learning_loop export-sft-candidates `
  --eval-bundle <new-source-eval> `
  --output-dir datasets/sft-development-v1/candidate
python -m agent_learning_loop validate-sft-candidates `
  --bundle datasets/sft-development-v1/candidate `
  --eval-bundle <new-source-eval>
```

A second fresh system-correctness Eval and candidate were generated in different new directories.
Their relative POSIX inventory, lengths, SHA-256 values, and actual bytes matched all four canonical
files. The normal source-bound validator reported `valid=true`, `execution_calls=0`, and unchanged
source and dataset bytes for both runs.

## Generated-report tracking note

The frozen [generated report](candidate/report.md) says this bundle is `not a tracked dataset`.
That sentence records an M7A generation-time boundary: the M7A exporter only writes a temporary
development candidate to a caller-selected new directory and does not publish it by itself. M7B
now versions the exact same bytes in Git as a reproducible review target; it does not rewrite the
M7A output or claim that the exporter published a dataset. The schema facts remain
`stage=development_candidate` and `exporter_commit=null`. The 18 samples remain not training-ready;
the completed six-sample review does not change that stage or claim.

## Fixed identity and size

- Samples: 18 train items; Workspace 6, Incident 6, DataOps 6.
- Held out: 12 identities counted but not serialized; validation 6 and test 6.
- Generation: `scripted_oracle`; model-generated samples 0; preference pairs 0.
- Source Eval selected cells: 30.
- Source Eval bundle fingerprint:
  `84936d6aff0e5932791bf4a976448e65ec845c787a85c4c73d0b79651830fe9c`.
- Source Eval manifest SHA-256:
  `98c6b2c4590013b9cccf6e7314b27889bc72fac1f122080992244ad643128db3`.
- Dataset bundle fingerprint:
  `0ef96672a2137a38a564640033ce90361eddf935a374e2d22197cb6b5180e06f`.
- System suite fingerprint:
  `624dfb19c2b9575056dd9d24a92e3dcb4852617eb538ee3541fb28cae933488e`.
- Encoding: UTF-8/LF; carriage-return bytes 0.

| Canonical artifact | Bytes | SHA-256 |
|---|---:|---|
| `dataset-manifest.json` | 3,400 | `6581d2be581df520a52be5a8436cde498485cb6ec92470d0602bc5aec7be7cd0` |
| `quality-report.json` | 1,198 | `0d1436d3d7fd7252b4ee97c7029313feae0efc04bd07317f3aba9011211fe6d8` |
| `report.md` | 1,353 | `fa71e6c4cad00def61b03b6ea3b03a9946bbc3201c18eace875b6c0c1646c4ef` |
| `samples.jsonl` | 55,016 | `7ad352bd3e78b6347b1a2e61ce81fdfefa5fb859ab6f8f7368f82cb0df2f5d82` |
| **Total** | **60,967** | four-file byte equality is the reproduction boundary |

## Sample identities

The fingerprint in each row covers that sample's complete public contract.

| Sample ID | Sample fingerprint |
|---|---|
| `sft.dataops.correct-order-status.v1` | `d446636f7dd0fe0a9f872def1b1f1709a11b429e67dad8bcc4e27b73dec42fde` |
| `sft.dataops.insert-missing-product-mapping.v1` | `3c5fa772fda98658f4adde93c88299146604023e4a9853d0ea092256929e1f72` |
| `sft.dataops.normalize-legacy-regions.v1` | `ee8366a24702c84b9b3d3b547f4acefb219bebb464ff16c53cf05ae6794c8321` |
| `sft.dataops.reject-transactionless-update.v1` | `16c3be8319f387d0da0798ae8690d8d6452fbacdaf460c24c1695f4edb7cabdf` |
| `sft.dataops.rollback-ambiguous-customer-match.v1` | `2fa25fe963af8e6912155065853899fcf8f0de3ba748f0e644f9e18550d9f1ab` |
| `sft.dataops.sync-daily-summary.v1` | `d77c00e25808494d1502c80980d96d6de319fcc97fbe85297088fd33a5d49dd5` |
| `sft.incident.acknowledge-auto-recovered-search.v1` | `e22132b94e2ba09a54ad995b85baf2ce33c8673892aaaa0e0ca615bebfb22c95` |
| `sft.incident.dedupe-notification-restart.v1` | `24b1d8fbf20ee69854a79ef565c5cd1688e3da45881c66c3847e4f4b98d36209` |
| `sft.incident.enable-catalog-cache-fallback.v1` | `ddd6a0229f913de123dbfd2fcbf0f15b661247cd6ffb4c8dfd134a4420a0cde9` |
| `sft.incident.escalate-denied-payment-restart.v1` | `e0a5a315b29660c6f55fd236a47e5a4de787c0f76efeaffe4a7331e415e16030` |
| `sft.incident.restart-stuck-order-worker.v1` | `d96670a4ce7dfc54db167da06d5dd06d600e031f9ac6b52f07e27641ce9b80c3` |
| `sft.incident.rollback-checkout-canary.v1` | `6a5ba8859e87e4a6eede1fa7afb724eddd0f9c9e764f479e11257463f44ca0dc` |
| `sft.workspace.build-deploy-manifest.v1` | `d79c4d03d086a226dab916fcd412fd75bcf1308abba5c8bfd3b2c0965560bdd7` |
| `sft.workspace.build-summary.v1` | `9102c1e53f641e12faae4a4d869dd1221c52eaf158d0a0b67e7cb14f143735b6` |
| `sft.workspace.create-owner-record.v1` | `4c939e59c64f4186f460e986c50811094776662de27fd65b4441bddadd478779` |
| `sft.workspace.merge-changelog.v1` | `f61ec41b7812e0cae5eaa5ae4511bfbec1c3d61ccf203d4cdc2b020c02e580d5` |
| `sft.workspace.reconcile-inventory.v1` | `ae2e1f1b82fd1a34c75f23c0c8a4b6af653c70f07fe80a92904ecf024e1f47e2` |
| `sft.workspace.repair-service-map.v1` | `c27983d829de9b57817894462953623d1e661b5710c2d32f1b7b14d32845943a` |

## Public fields and exclusions

Each sample contains its schema and contract versions; sample/task/environment/split identity;
scenario family, seed, tags, synthetic provenance and license; public source commit, suite, cell and
artifact references; packaged resource fingerprints; public instruction, allowed tools,
constraints and DataOps scope; ordered `assistant_action` and matching raw `tool_result` turns; and
quality flags. Tool turns include only the strict public arguments and observed status, error,
idempotency, and payload fields allowed by the M7A contract.

The exporter excludes fixture setup and expected state, protected-state and approval rules,
verifier checks/results, Incident/DataOps audit, run IDs, action-catalog references, machine paths,
secrets, held-out task content, Runtime reliability arms, and recovery/replay cells. Validation and
test items appear only in the aggregate excluded counts.

No third-party, customer, or personal data is present. The tasks, fixtures, and scripted actions
were authored for this repository and are licensed under Apache-2.0.

## Automated quality gates

The repository checks strict schema and fingerprints, complete raw-derived identity, task/family
deduplication, train-only split selection, held-out exclusion, public-field minimization,
private/verifier/audit/path/secret scanning, exact action/result binding, source-bound rejection of
jointly re-signed changes, deterministic rendering, UTF-8/LF encoding, exact four-file inventory,
zero execution during validation, and source/dataset byte preservation. SHA-256 detects accidental
or inconsistent changes; it is not an authorship signature.

## Intended use and limits

Intended use is narrow: development of an SFT adapter, format or tokenization experiments, and
pre-training data audits after the human gate closes. Out of scope are model benchmarking,
training-ready claims, DPO, real customer data, production performance, and security or compliance
certification.

The candidate has only 18 scripted-oracle demonstrations from a small set of synthetic task
families. Tool and state shapes come from three project simulators, not real systems. It contains no
model-output distribution and little natural-language diversity.

The guided review result is 6 of 6 pre-registered samples passed. This covers 6 of 18 candidate samples. The remaining 12 candidate samples were not individually human-reviewed. This result does not make the candidate training-ready, does not establish model improvement, and does not make the data risk-free. See [HUMAN_REVIEW.md](HUMAN_REVIEW.md) for the exact projections, method, date, and recorded scope.
