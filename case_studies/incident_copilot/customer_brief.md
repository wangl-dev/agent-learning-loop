# Simulated customer scenario

## Role and problem

The simulated user is an incident lead reviewing a copilot's evidence before allowing a safe
terminal action. The concrete problem is not automatic production remediation. It is whether a
fixed local workflow can show why it acknowledged, escalated, restarted a simulated service, or
changed a feature flag, with approvals and duplicate effects visible in the audit.

No customer interview took place. The role and pain points below are project assumptions used to
make the acceptance boundary testable.

## Controlled scope

The pilot uses the ten packaged Incident tasks only. Six are train-labelled fixtures, two are
validation, and two are test. They cover safe acknowledgement, evidence-driven escalation,
approval granted or denied, feature-flag mutation, simulated restart, idempotent retry, protected
state, and terminal handoff. All services, logs, flags, approvals, actions, and expected states are
project-authored synthetic data.

The acceptance questions are:

- Can the saved evidence justify acknowledgement versus escalation?
- Does every high-impact change match a prior approval for the same run, task, tool, target, and
  canonical arguments?
- Do denial and duplicate-operation paths avoid unauthorized or repeated physical mutations?

The tracked [canonical acceptance](pilot-evidence/acceptance.json) answers these questions only for
the ten registered synthetic tasks. The [case manifest](pilot-evidence/case-manifest.json) binds the
public source revision and complete raw evidence inventory; it does not turn the simulated role
below into a customer identity.

## Assumptions

- A caller can provide the public source commit whose installed code generated the run.
- Local deterministic fixtures are useful for contract testing before customer discovery.
- Validation/test labels are held out from the train-labelled subset, but all ten tasks remain
  authored and visible in this repository.

## Non-goals

The pilot does not connect to production monitoring, ticketing, approval, deployment, or service
control systems. It does not use a model, measure response quality, estimate time saved, claim an
SLA, or represent a real operator's adoption. It does not define production permissions or an
incident-management policy.

## Questions left for discovery

- Which evidence must an operator see before acknowledgement or escalation?
- Which targets require one approver, two approvers, or a change window?
- How should partial recovery, stale telemetry, and ownership transfer appear in a real handoff?
- What baseline time and error data can be collected without exposing customer-sensitive logs?

Until those questions are answered through real discovery, adoption, manual baseline, ROI, SLA,
production latency/cost, and model performance remain `N/A / not measured`.
