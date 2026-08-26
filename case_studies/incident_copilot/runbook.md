# Simulated customer scenario

## Offline pilot runbook

### Install from a clean checkout

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
python -m agent_learning_loop --version
```

Use a directory that does not already exist:

```powershell
python -m agent_learning_loop run-fde-case `
  --case incident-copilot-v1 `
  --source-commit a808ab5ee1b9420cfcc3a1f585e2b94491d7cdaa `
  --output-dir run-output/incident-copilot-v1

python -m agent_learning_loop validate-fde-case `
  --run-dir run-output/incident-copilot-v1
```

Exit 0 means accepted. Exit 1 means a complete, structurally valid bundle contains acceptance
drift; inspect it rather than deleting the failed IDs. Exit 2 means arguments, identity, artifact,
or infrastructure validation failed; do not treat its numbers as acceptance evidence.

### Inspect evidence

```powershell
Get-Content run-output/incident-copilot-v1/acceptance.json
Get-Content run-output/incident-copilot-v1/case-manifest.json
Get-Content run-output/incident-copilot-v1/report.md
Get-Content run-output/incident-copilot-v1/evidence/report.md
```

For a known tracked reference, validate the committed 37-file directory directly:

```powershell
python -m agent_learning_loop validate-fde-case `
  --run-dir case_studies/incident_copilot/pilot-evidence
```

The [manifest](pilot-evidence/case-manifest.json) is the path/hash inventory; the
[acceptance](pilot-evidence/acceptance.json) is the exact five-part decision; individual task
results and audits live under `pilot-evidence/evidence/runs/system-correctness-v1/`. Do not copy only
the outer report and call it complete evidence.

### Future operator handoff — not rehearsed

- confirm the real incident, tenant, target, telemetry freshness, and current owner;
- show evidence and uncertainty before asking for approval;
- authenticate the approver and verify action scope/expiry;
- keep a human override and emergency disable available;
- independently check post-action state and physical-effect count;
- record acknowledgement/escalation ownership and unresolved risk;
- preserve/redact evidence under the approved retention policy.

This checklist has not been exercised with a customer or external system. Real adoption, baseline,
ROI, SLA, production latency/cost, and model performance are `N/A / not measured`.
