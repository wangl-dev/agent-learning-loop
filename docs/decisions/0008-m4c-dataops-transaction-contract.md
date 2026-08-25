# ADR 0008: Keep DataOps v1 transaction semantics explicit and separate

## Context

Workspace operates on controlled files. Incident operates on deterministic in-memory service
state with approval-bound high-impact tools. Neither contract proves the database-specific rules
M4C needs: a write must belong to a transaction, its row count must be checked before mutation,
and commit and rollback have different durable-effect meanings.

SQLite is useful here because it supplies real transaction and unique-constraint behavior in the
Python standard library. Accepting caller SQL or a database path would turn a bounded synthetic
environment into an unsafe general database interface and would make offline runs depend on
machine state.

## Decision

DataOps v1 has independent strict task, fixture, action, tool-result, audit, snapshot, verifier,
catalog, corpus, and run-result models. The runner creates one temporary SQLite file for one task,
closes it after the run, and never copies it to public artifacts or package data. Tools receive
structured table, column, equality-filter, value, transaction, and operation fields. Identifiers
must be allowlisted before the environment quotes them; values are passed as bound parameters.
There is no public raw SQL or database-path field.

The environment permits one logical transaction. An update first selects primary keys and checks
the actual match count against both `expected_match_count` and the task maximum. Only then can it
issue the update. A successful mutation returns the transaction to `active`; therefore commit is
legal only after a successful validation later than the final mutation. Rollback restores the
initial digest and always reports zero committed rows. Attempted effects count successful
transaction-local row mutations, while committed effects count the rows retained by commit.

Operation IDs bind the canonical request. An exact repeat is an idempotency hit with no additional
physical mutation. Reusing the ID for a different request is a conflict. Ordered audit records bind
the runner-provided run/task context, transaction, operation, tool, table, columns, request
fingerprint, row counts, primary-key digest, before/after digests, validation, idempotency, and the
single terminal outcome. They contain no SQL text, complete rows, private expected state, or
database path.

The full verifier does not treat that audit as its own source of truth. It replays the fixed action
catalog from the trusted private fixture in a separate temporary SQLite database, then compares the
initial snapshot, final snapshot, and every audit field with that projection. This anchors the
first and last digest and prevents a coordinated replacement of digest chains, transaction IDs,
mutation columns, row counts, or primary-key evidence. The full verifier exposes only the trusted
split, terminal state, and attempted/committed count summary from that comparison;
`DataOpsRunResult` must match all four values.

The full verifier freezes 14 unique check names. Its pass flag equals all subchecks and its score
is exactly 1.0 or 0.0. Final results require the complete set and matching run/task context;
audit-only verification may use its named subset. The fixed corpus has ten tasks and a `6/2/2`
split. Its validator requires unique seeds and scenario families; exact filename/task/fixture/
catalog/manifest/action-reference identity; one transaction; scoped actions; and exact operation
coverage. Fixture rows and expected rows must satisfy exact column, type, null, primary-key, unique,
and row-level foreign-key contracts. A nullable foreign key may be `None`; each non-null value must
resolve inside the matching initial or expected target table. Protected filters must reference
typed schema columns and select real initial rows. `validate-corpus --environment all` calls the
`30/18-6-6` aggregate without running any task.

## Consequences

The implementation duplicates a small amount of schema, runner, and corpus code. That keeps
transaction failures attributable and avoids changing the published Workspace, Incident, M2, or
M3 contracts. DataOps demonstrates local SQLite safety semantics only. It does not establish a
general Runtime abstraction, production database isolation, arbitrary SQL support, model quality,
or performance at real-data scale.
