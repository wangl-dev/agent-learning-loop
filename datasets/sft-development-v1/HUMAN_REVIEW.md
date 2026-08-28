# M7B Fixed Human Review Packet

Status: completed

Review date: 2026-08-28

Method: The user completed a guided review in the commander conversation using each sample's complete task, constraints, public scope, and ordered turns summary.

This packet was prepared from the canonical public sample fields. It does not record an AI or
automated validator decision as human review. A person must inspect the six entries and record
their own findings before the status can change.

## Pre-registered selection

Samples were grouped by environment. Within each group, `sample_id` was sorted by
`(casefold, original)`; the first and last items were selected before their contents were read.
The repository regression recomputes this rule from `candidate/samples.jsonl`.

### dataops: `sft.dataops.correct-order-status.v1`

- Fingerprint: `d446636f7dd0fe0a9f872def1b1f1709a11b429e67dad8bcc4e27b73dec42fde`
- Source cell: `system.dataops.correct-order-status`
- Review result: passed

The JSON block is the review projection: public instruction, allowed tools, constraints, scope,
and every ordered action/result turn including arguments, status, error category, idempotency
flag, and payload.

```json
{
  "sample_id": "sft.dataops.correct-order-status.v1",
  "sample_fingerprint": "d446636f7dd0fe0a9f872def1b1f1709a11b429e67dad8bcc4e27b73dec42fde",
  "source_cell_id": "system.dataops.correct-order-status",
  "instruction": "Set order 1 to ready while preserving the neighboring tenant.",
  "allowed_tools": [
    "describe_table",
    "query_rows",
    "begin_transaction",
    "update_rows",
    "insert_row",
    "validate_constraints",
    "commit_transaction",
    "rollback_transaction"
  ],
  "constraints": [
    "one explicit transaction",
    "preserve neighbor tenant",
    "record identifiers and codes remain unique"
  ],
  "public_scope": [
    {
      "allow_insert": false,
      "max_mutated_rows": 1,
      "mutable_columns": [
        "value",
        "version"
      ],
      "predicate_columns": [
        "id",
        "tenant_id",
        "code",
        "value",
        "version"
      ],
      "readable_columns": [
        "id",
        "tenant_id",
        "code",
        "value",
        "version"
      ],
      "table": "records"
    }
  ],
  "turns": [
    {
      "arguments": {
        "transaction_id": "tx-correct-order"
      },
      "role": "assistant_action",
      "tool_name": "begin_transaction"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "transaction_state": "active"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "begin_transaction"
    },
    {
      "arguments": {
        "expected_match_count": 1,
        "operation_id": "op-correct-order",
        "table": "records",
        "transaction_id": "tx-correct-order",
        "values": {
          "value": "ready"
        },
        "where": {
          "id": 1,
          "tenant_id": "tenant-a"
        }
      },
      "role": "assistant_action",
      "tool_name": "update_rows"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "changed_row_count": 1,
        "matched_row_count": 1
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "update_rows"
    },
    {
      "arguments": {
        "transaction_id": "tx-correct-order"
      },
      "role": "assistant_action",
      "tool_name": "validate_constraints"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "constraints_valid": true
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "validate_constraints"
    },
    {
      "arguments": {
        "transaction_id": "tx-correct-order"
      },
      "role": "assistant_action",
      "tool_name": "commit_transaction"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "committed_row_count": 1,
        "terminal_state": "committed"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "commit_transaction"
    }
  ]
}
```

### dataops: `sft.dataops.sync-daily-summary.v1`

- Fingerprint: `d77c00e25808494d1502c80980d96d6de319fcc97fbe85297088fd33a5d49dd5`
- Source cell: `system.dataops.sync-daily-summary`
- Review result: passed

The JSON block is the review projection: public instruction, allowed tools, constraints, scope,
and every ordered action/result turn including arguments, status, error category, idempotency
flag, and payload.

```json
{
  "sample_id": "sft.dataops.sync-daily-summary.v1",
  "sample_fingerprint": "d77c00e25808494d1502c80980d96d6de319fcc97fbe85297088fd33a5d49dd5",
  "source_cell_id": "system.dataops.sync-daily-summary",
  "instruction": "Synchronize the daily summary to the reviewed value.",
  "allowed_tools": [
    "describe_table",
    "query_rows",
    "begin_transaction",
    "update_rows",
    "insert_row",
    "validate_constraints",
    "commit_transaction",
    "rollback_transaction"
  ],
  "constraints": [
    "one reviewed summary row",
    "validate before commit",
    "summary code remains unique"
  ],
  "public_scope": [
    {
      "allow_insert": false,
      "max_mutated_rows": 1,
      "mutable_columns": [
        "value"
      ],
      "predicate_columns": [
        "id",
        "tenant_id",
        "code",
        "value",
        "version"
      ],
      "readable_columns": [
        "id",
        "tenant_id",
        "code",
        "value",
        "version"
      ],
      "table": "records"
    }
  ],
  "turns": [
    {
      "arguments": {
        "columns": [
          "code",
          "value"
        ],
        "limit": 10,
        "table": "records",
        "where": {
          "tenant_id": "source"
        }
      },
      "role": "assistant_action",
      "tool_name": "query_rows"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "count": 2,
        "rows": [
          {
            "code": "source-a",
            "value": "20"
          },
          {
            "code": "source-b",
            "value": "22"
          }
        ]
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "query_rows"
    },
    {
      "arguments": {
        "transaction_id": "tx-summary"
      },
      "role": "assistant_action",
      "tool_name": "begin_transaction"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "transaction_state": "active"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "begin_transaction"
    },
    {
      "arguments": {
        "expected_match_count": 1,
        "operation_id": "op-summary",
        "table": "records",
        "transaction_id": "tx-summary",
        "values": {
          "value": "42"
        },
        "where": {
          "code": "daily-total"
        }
      },
      "role": "assistant_action",
      "tool_name": "update_rows"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "changed_row_count": 1,
        "matched_row_count": 1
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "update_rows"
    },
    {
      "arguments": {
        "transaction_id": "tx-summary"
      },
      "role": "assistant_action",
      "tool_name": "validate_constraints"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "constraints_valid": true
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "validate_constraints"
    },
    {
      "arguments": {
        "transaction_id": "tx-summary"
      },
      "role": "assistant_action",
      "tool_name": "commit_transaction"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "committed_row_count": 1,
        "terminal_state": "committed"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "commit_transaction"
    }
  ]
}
```

### incident: `sft.incident.acknowledge-auto-recovered-search.v1`

- Fingerprint: `e22132b94e2ba09a54ad995b85baf2ce33c8673892aaaa0e0ca615bebfb22c95`
- Source cell: `system.incident.acknowledge-auto-recovered-search`
- Review result: passed

The JSON block is the review projection: public instruction, allowed tools, constraints, scope,
and every ordered action/result turn including arguments, status, error category, idempotency
flag, and payload.

```json
{
  "sample_id": "sft.incident.acknowledge-auto-recovered-search.v1",
  "sample_fingerprint": "e22132b94e2ba09a54ad995b85baf2ce33c8673892aaaa0e0ca615bebfb22c95",
  "source_cell_id": "system.incident.acknowledge-auto-recovered-search",
  "instruction": "Confirm the already healthy search service and acknowledge the incident.",
  "allowed_tools": [
    "get_service_status",
    "read_service_logs",
    "acknowledge_incident"
  ],
  "constraints": [
    "No high-impact action."
  ],
  "public_scope": [],
  "turns": [
    {
      "arguments": {
        "service": "search"
      },
      "role": "assistant_action",
      "tool_name": "get_service_status"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "service": "search",
        "state": "healthy"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "get_service_status"
    },
    {
      "arguments": {},
      "role": "assistant_action",
      "tool_name": "acknowledge_incident"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "incident_status": "acknowledged"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "acknowledge_incident"
    }
  ]
}
```

### incident: `sft.incident.rollback-checkout-canary.v1`

- Fingerprint: `6a5ba8859e87e4a6eede1fa7afb724eddd0f9c9e764f479e11257463f44ca0dc`
- Source cell: `system.incident.rollback-checkout-canary`
- Review result: passed

The JSON block is the review projection: public instruction, allowed tools, constraints, scope,
and every ordered action/result turn including arguments, status, error category, idempotency
flag, and payload.

```json
{
  "sample_id": "sft.incident.rollback-checkout-canary.v1",
  "sample_fingerprint": "6a5ba8859e87e4a6eede1fa7afb724eddd0f9c9e764f479e11257463f44ca0dc",
  "source_cell_id": "system.incident.rollback-checkout-canary",
  "instruction": "Restore checkout by disabling the failing canary after approval.",
  "allowed_tools": [
    "get_service_status",
    "inspect_service_config",
    "request_approval",
    "set_feature_flag",
    "acknowledge_incident"
  ],
  "constraints": [
    "Disable only target canary."
  ],
  "public_scope": [],
  "turns": [
    {
      "arguments": {
        "arguments": {
          "enabled": false,
          "key": "checkout.canary"
        },
        "target": "checkout.canary",
        "tool_name": "set_feature_flag"
      },
      "role": "assistant_action",
      "tool_name": "request_approval"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "approval_id": "checkout-canary-off",
        "decision": "approved"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "request_approval"
    },
    {
      "arguments": {
        "approval_id": "checkout-canary-off",
        "enabled": false,
        "key": "checkout.canary",
        "operation_id": "checkout-canary-off-1"
      },
      "role": "assistant_action",
      "tool_name": "set_feature_flag"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "enabled": false,
        "key": "checkout.canary"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "set_feature_flag"
    },
    {
      "arguments": {},
      "role": "assistant_action",
      "tool_name": "acknowledge_incident"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "incident_status": "acknowledged"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "acknowledge_incident"
    }
  ]
}
```

### workspace: `sft.workspace.build-deploy-manifest.v1`

- Fingerprint: `d79c4d03d086a226dab916fcd412fd75bcf1308abba5c8bfd3b2c0965560bdd7`
- Source cell: `system.workspace.build-deploy-manifest`
- Review result: passed

The JSON block is the review projection: public instruction, allowed tools, constraints, scope,
and every ordered action/result turn including arguments, status, error category, idempotency
flag, and payload.

```json
{
  "sample_id": "sft.workspace.build-deploy-manifest.v1",
  "sample_fingerprint": "d79c4d03d086a226dab916fcd412fd75bcf1308abba5c8bfd3b2c0965560bdd7",
  "source_cell_id": "system.workspace.build-deploy-manifest",
  "instruction": "Read the service, image, and replica inputs and write output/deploy.manifest in deterministic key order without creating a draft.",
  "allowed_tools": [
    "read_text",
    "write_text"
  ],
  "constraints": [
    "Only mutate declared Workspace paths.",
    "Do not create draft deployment files."
  ],
  "public_scope": [],
  "turns": [
    {
      "arguments": {
        "path": "deploy/service.txt"
      },
      "role": "assistant_action",
      "tool_name": "read_text"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "content": "catalog-api\n"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "read_text"
    },
    {
      "arguments": {
        "path": "deploy/image.txt"
      },
      "role": "assistant_action",
      "tool_name": "read_text"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "content": "registry.local/catalog:v3\n"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "read_text"
    },
    {
      "arguments": {
        "path": "deploy/replicas.txt"
      },
      "role": "assistant_action",
      "tool_name": "read_text"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "content": "3\n"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "read_text"
    },
    {
      "arguments": {
        "content": "service=catalog-api\nimage=registry.local/catalog:v3\nreplicas=3\n",
        "path": "output/deploy.manifest"
      },
      "role": "assistant_action",
      "tool_name": "write_text"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "bytes_written": 63,
        "path": "output/deploy.manifest"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "write_text"
    }
  ]
}
```

### workspace: `sft.workspace.repair-service-map.v1`

- Fingerprint: `c27983d829de9b57817894462953623d1e661b5710c2d32f1b7b14d32845943a`
- Source cell: `system.workspace.repair-service-map`
- Review result: passed

The JSON block is the review projection: public instruction, allowed tools, constraints, scope,
and every ordered action/result turn including arguments, status, error category, idempotency
flag, and payload.

```json
{
  "sample_id": "sft.workspace.repair-service-map.v1",
  "sample_fingerprint": "c27983d829de9b57817894462953623d1e661b5710c2d32f1b7b14d32845943a",
  "source_cell_id": "system.workspace.repair-service-map",
  "instruction": "Repair the worker endpoint in config/services.map while preserving the api and metrics mappings and leaving the operator note unchanged.",
  "allowed_tools": [
    "read_text",
    "write_text"
  ],
  "constraints": [
    "Only mutate declared Workspace paths.",
    "Preserve unrelated service mappings and notes."
  ],
  "public_scope": [],
  "turns": [
    {
      "arguments": {
        "path": "config/services.map"
      },
      "role": "assistant_action",
      "tool_name": "read_text"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "content": "api=api.internal:8080\nworker=worker.invalid:9000\nmetrics=metrics.internal:9090\n"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "read_text"
    },
    {
      "arguments": {
        "content": "api=api.internal:8080\nworker=worker.internal:9000\nmetrics=metrics.internal:9090\n",
        "path": "config/services.map"
      },
      "role": "assistant_action",
      "tool_name": "write_text"
    },
    {
      "error_category": null,
      "idempotency_hit": false,
      "payload": {
        "bytes_written": 80,
        "path": "config/services.map"
      },
      "role": "tool_result",
      "status": "ok",
      "tool_name": "write_text"
    }
  ]
}
```

## Reviewer checklist

For each of the six samples, record whether:

- the task is understandable;
- the ordered steps match the instruction;
- each argument and returned payload are mutually consistent;
- any private expected state, verifier output, audit record, held-out content, machine path, or
  secret appears;
- any content is misleading or unsuitable as a demonstration.

## Issues

none reported

## Final conclusion

6/6 passed

This conclusion covers only the six pre-registered samples. It does not mean the other 12 candidate samples were individually human-reviewed, that the 18-sample candidate is training-ready, that it establishes model improvement, or that the data is risk-free.
