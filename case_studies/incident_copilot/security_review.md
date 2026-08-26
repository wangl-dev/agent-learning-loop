# Simulated customer scenario

## Security review of the offline pilot boundary

### Controls demonstrated by current evidence

The ten registered synthetic tasks exercise strict per-tool arguments, declared target/scope,
approval bound to run/task/tool/target/canonical action, operation idempotency, audit ordering,
terminal acknowledgement or escalation, and exact state/effect verification. The
[acceptance artifact](pilot-evidence/acceptance.json) records 10/10 Incident safety with zero
unauthorized high-impact executions. The [outer manifest](pilot-evidence/case-manifest.json) also
allows only 30 registered raw paths and 34 total nested evidence paths.

These checks can detect a missing/extra file, changed bytes without matching hashes, a broken
approval/execution link, duplicated physical mutation, foreign run/task context, inconsistent
terminal record, or a result that no longer matches its raw evidence. The read-only validator
recomputes those relationships and leaves the directory unchanged.

### What the pilot does not defend

- a compromised host, Python process, operating-system account, or malicious package dependency;
- an actor able to replace the code and jointly rewrite every artifact/hash;
- real user identity, RBAC, credential storage, approval delegation, expiry, or revocation;
- network interception, telemetry authenticity/freshness, tenant isolation, or supply-chain risk;
- production process/container isolation, service blast radius, secret management, or compliance
  certification;
- a model selecting safe actions outside the fixed scripted catalog.

The project therefore does not describe this as a production sandbox, zero-trust control, or
compliance result. SHA-256 provides consistency checking, not signed provenance.

### Hard stop conditions before external use

Do not connect write-capable systems if any target inventory, approval authority, least-privilege
credential, idempotency scope, telemetry freshness rule, independent post-action check, human
override, or emergency disable path is unresolved. Stop a future shadow/canary immediately on
cross-tenant data, unredacted secret/personal data, stale or ambiguous telemetry presented as
current, an unapproved write attempt, duplicate physical effect, missing audit link, or inability to
hand control back to an operator.

External owners must decide identity/RBAC, retention/privacy, incident policy, change authority,
risk acceptance, and response to dependency compromise. The repository cannot make those decisions.
