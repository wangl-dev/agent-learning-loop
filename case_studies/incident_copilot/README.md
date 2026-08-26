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

## Canonical simulated pilot

The tracked [generated report](pilot-evidence/report.md),
[acceptance](pilot-evidence/acceptance.json), and
[manifest](pilot-evidence/case-manifest.json) were produced by the wheel built from public commit
`a808ab5ee1b9420cfcc3a1f585e2b94491d7cdaa`. The full directory has 37 files: three outer files,
four nested Eval top-level files, and 30 raw result/event/audit files. The outer manifest registers
34 evidence paths. A second fresh pure-wheel run produced the same relative inventory and every
byte; the generated files were not edited afterward.

Validate the committed copy without executing an Environment or tool:

```powershell
python -m agent_learning_loop validate-fde-case `
  --run-dir case_studies/incident_copilot/pilot-evidence
```

The [acceptance](pilot-evidence/acceptance.json) reports 10/10 registered contracts, 4/4 held-out
contracts, 3/3 controls, 10/10 Incident safety, and zero unauthorized high-impact executions. The
[nested report](pilot-evidence/evidence/report.md) keeps the 6/2/2 split visible. These are
fixture-contract results, not model performance or real incident outcomes.

## Delivery pack index

- [Discovery notes](discovery_notes.md) separate public facts, project assumptions, and open
  customer questions.
- [Architecture](architecture.md) contains only the current offline path and an explicit
  not-connected system table.
- [Integration contracts](integration_contracts.md) records the real CLI/artifact boundary and
  information future adapters would require.
- [Security review](security_review.md) separates tested controls from production gaps and hard
  stop conditions.
- [Rollout plan](rollout_plan.md) is conditional; no external phase has been executed.
- [Rollback plan](rollback_plan.md) distinguishes local output cleanup from future adapter control.
- [Runbook](runbook.md) gives reproducible run/validate/inspect commands and exit handling.
- [Synthetic task review](field_feedback.md) turns task evidence into an unprioritized product
  backlog; it is not customer feedback.

This pack does not connect ticketing, monitoring, approval, deployment, or service-control
systems. Real adoption, manual baseline, ROI, SLA, production latency/cost, and model performance
are `N/A / not measured`. The documents describe what exists and what evidence a future owner
would need; they do not report a rollout, operator handoff, or customer interview.
