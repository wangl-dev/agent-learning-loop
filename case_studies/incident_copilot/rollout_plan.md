# Simulated customer scenario

## Conditional rollout plan

Only the first row exists. The later rows are planning gates, not completed deployment history.

| Phase | Status | Entry gate | Observe | Stop condition | Owner decision |
|---|---|---|---|---|---|
| Current offline contract | implemented locally | Public wheel, fixed case identity, normal validator, exact 37-file evidence | Acceptance drift, artifact integrity, safety-check failure | Any invalid bundle, missing raw evidence, or unauthorized high-impact execution | Project maintainer decides whether the offline candidate is reviewable |
| Future read-only shadow | not executed / requires real integration | Discovery, privacy/security approval, read-only identity and telemetry freshness contract, human owner | Evidence usefulness, stale/ambiguous signal handling, operator override and handoff gaps | Any write attempt, cross-tenant exposure, secret/personal-data leak, or unsafe recommendation | Customer security, incident, and system owners decide whether shadowing may continue |
| Future narrow canary | not executed / requires real integration | Shadow evidence accepted; least-privilege action scope; authenticated approval; emergency disable; rollback rehearsed | Approval match, physical-effect count, independent post-action state, audit completeness | Unapproved/duplicate effect, target escape, stale approval, missing audit, or failed handback | Authorized change owner decides whether one narrowly scoped action class may continue |
| Broader rollout | not executed / requires real integration | Repeated canary review, defined support/ownership, measured baseline, privacy retention and risk sign-off | Real adoption and error data, operator intervention, reliability, latency/cost against agreed targets | Any agreed safety, privacy, reliability, cost, or ownership threshold is breached | Customer governance owners decide whether scope expands, pauses, or rolls back |

No date, traffic percentage, SLA, success rate, adoption, cost, or latency target is filled in because
none has been agreed or measured. The current [canonical report](pilot-evidence/report.md) supports
only the offline row: 10/10 registered, 4/4 held-out, 3/3 controls, 10/10 safety, and zero
unauthorized high-impact executions for synthetic tasks.
