# Simulated customer scenario

## Discovery notes for an evidence-first incident copilot

No customer meeting or interview occurred. These notes separate facts visible in public artifacts
from assumptions that would have to be tested before any external integration.

### Known from the public project

- The packaged case selects ten synthetic Incident cells. The
  [nested report](pilot-evidence/evidence/report.md) shows the fixed 6 train / 2 validation / 2 test
  split and an Incident result of 10/10.
- The [acceptance artifact](pilot-evidence/acceptance.json) records 10/10 registered contracts, 4/4
  held-out contracts, 3/3 control groups, 10/10 safety checks, and zero unauthorized high-impact
  executions.
- The [case manifest](pilot-evidence/case-manifest.json) binds public source `a808ab5...`, the
  packaged case fingerprint, 30 raw paths, and 34 nested evidence paths. The complete pilot has 37
  files.
- All tasks, services, logs, approvals, targets, and expected states are project-authored synthetic
  data. Ticketing, monitoring, approval, deployment, and service control are not connected.

### Assumptions to test

The simulated role is an incident lead who needs evidence before an acknowledgement, escalation,
restart, or feature-flag change. The project assumes that exact approval identity, a visible audit
sequence, duplicate-effect protection, and a clear handoff state would matter to that role. It also
assumes a read-only shadow phase would be safer than beginning with write access. None of these
assumptions has been confirmed by a real operator.

### Questions for real discovery

- Which telemetry is authoritative, how stale may it be, and who owns freshness disputes?
- What evidence must be visible before acknowledgement or escalation?
- Which action classes require one approver, multiple approvers, a maintenance window, or complete
  prohibition?
- How are partial recovery, ownership transfer, human override, and conflicting tools recorded?
- Which ticket and service identifiers can be stored, and which must be redacted or referenced
  indirectly?
- What event proves that a handoff is accepted by an operator rather than merely emitted by a tool?

### Minimum future data and privacy boundary

If discovery authorizes a prototype, collect only the fields needed to test the questions above:
pseudonymous incident/correlation ID, timestamp and freshness source, action class, target class,
approval decision plus non-secret identity reference, terminal decision, human override, and a
coarse outcome label. Do not copy raw customer logs, credentials, tokens, personal data, message
bodies, or full ticket history into this repository. Retention, access, deletion, redaction, and
legal ownership need an external owner decision before collection. Real adoption, manual baseline,
ROI, SLA, production latency/cost, and model performance remain `N/A / not measured`.
