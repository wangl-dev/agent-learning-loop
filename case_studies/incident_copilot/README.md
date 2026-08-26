# Simulated customer scenario

This executable pilot asks a narrow delivery question: can the existing ten synthetic Incident
tasks be packaged as one acceptance case without turning a scripted result into a customer or
production claim?

Run the fixed case from an installed checkout. The source commit is supplied by the caller; the
command does not inspect Git:

```powershell
python -m agent_learning_loop run-fde-case `
  --case incident-copilot-v1 `
  --source-commit <40-lowercase-hex> `
  --output-dir run-output/incident-copilot-v1

python -m agent_learning_loop validate-fde-case `
  --run-dir run-output/incident-copilot-v1
```

The new directory contains three outer files and the unchanged nested Eval bundle:

```text
incident-copilot-v1/
├─ case-manifest.json
├─ acceptance.json
├─ report.md
└─ evidence/
   ├─ eval-manifest.json
   ├─ records.jsonl
   ├─ summary.json
   ├─ report.md
   └─ runs/system-correctness-v1/system.incident.*/...
```

The ten cells are partitioned into three fixed controls: triage and terminal selection,
approval-bound change, and guarded recovery and handoff. The validator first runs the normal
read-only Eval validator, then rebuilds the 10-cell, 4 held-out, 3-control acceptance and checks
the raw Incident audit. It does not rerun an Environment or tool.

The evidence points back to the nested raw result/audit paths rather than a screenshot. A normal
scripted run should report 10/10 registered contracts, 4/4 held-out contracts, 3/3 controls,
10/10 Incident safety, and zero unauthorized high-impact executions. These are fixture-contract
results, not model performance or real incident outcomes.

M6A stops at this executable contract and the minimal brief and metric definitions. It does not
create a canonical pilot, deployment plan, discovery transcript, rollout claim, or operator
handoff. Those delivery materials are an M6B decision after this contract is reviewed.
