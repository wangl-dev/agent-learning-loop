# M7A scripted-oracle SFT development candidate

- Stage: `development_candidate`
- Source trajectory commit: `8a4016a9c154238cd7e5df5d1a3ed8fd194dd10d`
- Source Eval bundle fingerprint: `84936d6aff0e5932791bf4a976448e65ec845c787a85c4c73d0b79651830fe9c`
- Eligible samples: `18` (`workspace=6`, `incident=6`, `dataops=6`)
- Held-out excluded: `12` (`validation=6`, `test=6`)
- Generation mode: `scripted_oracle`; model-generated samples: `0`
- Preference or DPO pairs: `0`
- Provenance/license: `project-authored-synthetic` / `Apache-2.0`

## Quality and leakage gates

- Duplicate sample/task/family/fingerprint findings: `0/0/0/0`
- Train-to-held-out task/family overlap: `0/0`
- Leakage, machine-path, secret-like, CR, non-UTF-8, symlink findings: `0`
- Environment, Policy, tool, runner, subprocess, socket, and network calls: `0`
- Source Eval bytes changed by export: `false`

## Boundary

This bundle is a deterministic development candidate, not a tracked dataset or a training result. It contains public task context plus raw action/tool observations. Fixture-only setup and answer state, verifier output, audit records, run identifiers, held-out content, reliability/recovery cells, and preference labels are excluded.
SHA-256 binds reviewed bytes and identities; it is not a signature against an actor who can rewrite every related artifact.
