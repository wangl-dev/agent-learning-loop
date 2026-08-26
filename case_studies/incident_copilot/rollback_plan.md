# Simulated customer scenario

## Rollback and safe-stop boundaries

### Current offline output

The local runner never connects to a real service. If a new run is not wanted, choose a fresh
output directory or remove only the explicitly named disposable run directory after confirming it
contains no user data. Validation is read-only and should not require cleanup. The tracked
[`pilot-evidence/`](pilot-evidence/) directory is review evidence and must not be overwritten by a
run or edited to simulate rollback.

An invalid or drifted bundle is preserved for inspection unless its enclosing directory was only a
partial infrastructure failure. Exit 1 is not corruption: it means the strict artifact exists but a
pre-registered acceptance condition missed. Exit 2 means the run/validation contract itself failed.

### Future external adapter

No adapter exists and no production rollback has been performed. If a real integration were ever
authorized, its rollback design must at least:

1. disable the adapter and revoke its action route without disabling the operator's native path;
2. prevent new operations and invalidate outstanding approvals or idempotency scope as defined by
   the owning system;
3. preserve the immutable audit/evidence needed to understand any attempted or committed effect;
4. hand decision and control back to a named operator;
5. independently verify that no further side effect is occurring and reconcile partial state;
6. retain or delete data according to the external privacy/retention decision.

Which controls implement those steps, who owns them, and what restoration objective applies are
unknown. They are discovery and security decisions, not facts inferred from the synthetic
[acceptance](pilot-evidence/acceptance.json).
