# Simulated customer scenario

## Integration contracts that exist today

The current interface is a local CLI and a strict artifact directory. It has no HTTP endpoint,
webhook, message queue, ticket API, or production credential.

### Run contract

```powershell
python -m agent_learning_loop run-fde-case `
  --case incident-copilot-v1 `
  --source-commit a808ab5ee1b9420cfcc3a1f585e2b94491d7cdaa `
  --output-dir run-output/incident-copilot-v1
```

- `--case` is exactly `incident-copilot-v1`.
- `--source-commit` is an explicit 40-character lowercase hexadecimal identity. The CLI records it;
  it does not query Git.
- `--output-dir` must not already exist. Partial output is cleaned after infrastructure failure;
  existing user data is not overwritten.
- Exit 0 means the strict bundle is accepted, exit 1 means a structurally valid acceptance drift,
  and exit 2 means arguments, identity, artifact, or infrastructure failure.
- The output is exactly the 37 files registered by the
  [case manifest](pilot-evidence/case-manifest.json): three outer files plus 34 nested evidence
  files, including 30 raw result/event/audit files.

### Read-only validation contract

```powershell
python -m agent_learning_loop validate-fde-case `
  --run-dir case_studies/incident_copilot/pilot-evidence
```

Validation calls the existing Eval validator, then reconstructs the five acceptance facts from
packaged identities and saved raw evidence. It must leave every source byte unchanged and make zero
execution calls. The [tracked acceptance](pilot-evidence/acceptance.json) is an example of the
strict machine-readable result, not an API response from an external system.

### Boundary for future adapters

Before a future adapter enters discovery and security review, its owner would need to define:

- stable incident, tenant, target, action, operation, and approval identity fields;
- timestamp source, allowable freshness, ordering, replay, and duplicate-delivery semantics;
- read versus write permissions, approval expiry/revocation, and least-privilege credentials;
- redaction and retention rules for logs, ticket content, user data, and audit evidence;
- deterministic categories for timeout, denial, stale evidence, partial success, conflict, and
  unavailable dependencies;
- human override, emergency disable, handoff acknowledgement, and independent post-action checks.

No endpoint names or payload examples are proposed because the real systems and their contracts are
unknown. An adapter must not reinterpret the synthetic eight-tool boundary as production authority.
