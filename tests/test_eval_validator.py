from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agent_learning_loop.dataops_corpus import validate_dataops_corpus
from agent_learning_loop.eval_bundle import canonical_json_text, compute_bundle_fingerprint
from agent_learning_loop.eval_records import _DataOpsProjection
from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_schemas import EvalBundleManifest
from agent_learning_loop.eval_validator import EvalBundleValidationError, validate_eval_bundle

SOURCE_COMMIT = "6" * 40


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def rehash_raw_artifacts(root: Path) -> None:
    manifest_path = root / "eval-manifest.json"
    manifest = EvalBundleManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    artifacts = [
        item.model_copy(
            update={"sha256": hashlib.sha256((root / item.path).read_bytes()).hexdigest()}
        )
        for item in manifest.artifacts
    ]
    draft = manifest.model_copy(update={"artifacts": artifacts, "bundle_fingerprint": "0" * 64})
    changed = draft.model_copy(update={"bundle_fingerprint": compute_bundle_fingerprint(draft)})
    manifest_path.write_text(canonical_json_text(changed), encoding="utf-8")


@pytest.fixture
def reliability_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    assert run_eval("runtime-reliability", SOURCE_COMMIT, root).exit_code == 0
    return root


@pytest.fixture(scope="module")
def recovery_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("recovery-bundle") / "source"
    assert run_eval("recovery-replay", SOURCE_COMMIT, root).exit_code == 0
    return root


@pytest.fixture(scope="module")
def system_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("system-bundle") / "source"
    assert run_eval("system-correctness", SOURCE_COMMIT, root).exit_code == 0
    return root


@pytest.mark.parametrize(
    ("relative_path", "mutation"),
    [
        (
            "runs/system-correctness-v1/system.workspace.build-summary/"
            "workspace/output/summary.txt",
            "wrong_workspace_final",
        ),
        (
            "runs/system-correctness-v1/system.incident.rollback-checkout-canary/events.jsonl",
            "destroyed_events",
        ),
        (
            "runs/system-correctness-v1/system.incident.rollback-checkout-canary/audit.jsonl",
            "foreign_audit_context",
        ),
        (
            "runs/system-correctness-v1/system.dataops.correct-order-status/events.jsonl",
            "destroyed_events",
        ),
        (
            "runs/system-correctness-v1/system.dataops.correct-order-status/audit.jsonl",
            "destroyed_audit",
        ),
        (
            "runs/system-correctness-v1/system.dataops.correct-order-status/audit.jsonl",
            "null_transaction_ids",
        ),
        (
            "runs/system-correctness-v1/system.dataops.correct-order-status/audit.jsonl",
            "forged_primary_key_digest",
        ),
        (
            "runs/system-correctness-v1/system.dataops.correct-order-status/audit.jsonl",
            "forged_digest_chain_pair",
        ),
        (
            "runs/system-correctness-v1/system.dataops.correct-order-status/audit.jsonl",
            "forged_cardinality_flag",
        ),
        (
            "runs/system-correctness-v1/"
            "system.incident.acknowledge-auto-recovered-search/events.jsonl",
            "forged_incident_observation",
        ),
        (
            "runs/system-correctness-v1/system.workspace.build-summary/events.jsonl",
            "forged_workspace_observation",
        ),
    ],
)
def test_system_raw_semantic_tamper_fails_after_hashes_are_recomputed(
    system_bundle: Path,
    tmp_path: Path,
    relative_path: str,
    mutation: str,
) -> None:
    root = tmp_path / mutation
    shutil.copytree(system_bundle, root)
    target = root / relative_path
    if mutation == "wrong_workspace_final":
        target.write_text("semantically wrong\n", encoding="utf-8")
    elif mutation == "foreign_audit_context":
        lines = target.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[0])
        payload["run_id"] = "eval-foreign-context"
        lines[0] = json.dumps(payload, separators=(",", ":"))
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation in {
        "null_transaction_ids",
        "forged_primary_key_digest",
        "forged_digest_chain_pair",
        "forged_cardinality_flag",
    }:
        payloads = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
        if mutation == "null_transaction_ids":
            for payload in payloads:
                payload["transaction_id"] = None
        elif mutation == "forged_primary_key_digest":
            mutation_record = next(
                payload
                for payload in payloads
                if payload["tool_name"] == "update_rows"
                and payload["decision"] == "executed"
                and not payload["idempotency_hit"]
            )
            mutation_record["primary_key_digest"] = "0" * 64
        elif mutation == "forged_digest_chain_pair":
            pair_index = next(
                index
                for index in range(len(payloads) - 1)
                if payloads[index]["after_digest"] == payloads[index + 1]["before_digest"]
            )
            payloads[pair_index]["after_digest"] = "0" * 64
            payloads[pair_index + 1]["before_digest"] = "0" * 64
        else:
            mutation_record = next(
                payload
                for payload in payloads
                if payload["tool_name"] == "update_rows"
                and payload["decision"] == "executed"
                and not payload["idempotency_hit"]
            )
            mutation_record["cardinality_checked_before_write"] = False
        target.write_text(
            "".join(json.dumps(payload, separators=(",", ":")) + "\n" for payload in payloads),
            encoding="utf-8",
        )
    elif mutation == "forged_incident_observation":
        payloads = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
        action_index = next(
            index
            for index, payload in enumerate(payloads)
            if payload.get("tool_name") == "get_service_status"
        )
        assert payloads[action_index + 1]["payload"]["state"] == "healthy"
        payloads[action_index + 1]["payload"]["state"] = "stuck"
        target.write_text(
            "".join(json.dumps(payload, separators=(",", ":")) + "\n" for payload in payloads),
            encoding="utf-8",
        )
    elif mutation == "forged_workspace_observation":
        payloads = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
        action_event_index = next(
            index
            for index, payload in enumerate(payloads)
            if payload.get("event_kind") == "action_selected"
            and payload["payload"].get("tool_name") == "read_text"
        )
        completed = payloads[action_event_index + 1]
        assert completed["event_kind"] == "tool_completed"
        completed["payload"]["payload"]["content"] = "forged observation"
        target.write_text(
            "".join(json.dumps(payload, separators=(",", ":")) + "\n" for payload in payloads),
            encoding="utf-8",
        )
    else:
        target.write_text('{"semantically":"destroyed"}\n', encoding="utf-8")
    rehash_raw_artifacts(root)

    with pytest.raises(EvalBundleValidationError):
        validate_eval_bundle(root)


def test_honest_system_bundle_preserves_three_environment_tool_coverage(
    system_bundle: Path,
) -> None:
    result = validate_eval_bundle(system_bundle)
    assert result.selected_cells == 30

    run_root = system_bundle / "runs/system-correctness-v1"
    tool_sets: dict[str, set[str]] = {"workspace": set(), "incident": set(), "dataops": set()}
    incident_audit: list[dict[str, object]] = []
    dataops_audit: list[dict[str, object]] = []
    for result_path in sorted(run_root.glob("*/result.json")):
        task_id = result_path.parent.name.removeprefix("system.")
        environment = task_id.split(".", maxsplit=1)[0]
        event_lines = (result_path.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
        for payload in map(json.loads, event_lines):
            tool_name = payload.get("tool_name")
            if environment == "workspace" and payload.get("event_kind") == "action_selected":
                tool_name = payload["payload"].get("tool_name")
            if isinstance(tool_name, str):
                tool_sets[environment].add(tool_name)
        if environment in {"incident", "dataops"}:
            audit = [
                json.loads(line)
                for line in (result_path.parent / "audit.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            if environment == "incident":
                incident_audit.extend(audit)
            else:
                dataops_audit.extend(audit)

    assert tool_sets == {
        "workspace": {"list_files", "read_text", "write_text"},
        "incident": {
            "get_service_status",
            "read_service_logs",
            "request_approval",
            "set_feature_flag",
            "restart_simulated_service",
            "acknowledge_incident",
            "escalate_incident",
        },
        "dataops": {
            "query_rows",
            "begin_transaction",
            "update_rows",
            "insert_row",
            "validate_constraints",
            "commit_transaction",
            "rollback_transaction",
        },
    }
    assert {record["category"] for record in incident_audit} >= {
        "observation",
        "approval",
        "execution",
        "acknowledgement",
        "escalation",
    }
    assert any(record["physical_mutation"] for record in incident_audit)
    assert {record["terminal_outcome"] for record in dataops_audit} >= {
        "committed",
        "rolled_back",
    }
    assert any(
        record["cardinality_checked_before_write"] and record["decision"] == "executed"
        for record in dataops_audit
    )
    assert any(record["error_category"] == "cardinality_mismatch" for record in dataops_audit)


def test_dataops_projection_honestly_replays_one_operation_without_a_second_effect() -> None:
    corpus = validate_dataops_corpus()
    fixture = next(
        item for item in corpus.fixtures if item.task.task_id == "dataops.correct-order-status"
    )
    catalog = next(item for item in corpus.catalogs if item.task_id == fixture.task.task_id)
    begin = next(
        entry.action for entry in catalog.actions if entry.action.tool_name == "begin_transaction"
    )
    update = next(
        entry.action for entry in catalog.actions if entry.action.tool_name == "update_rows"
    )
    projection = _DataOpsProjection(fixture, run_id="eval-projection-honest")
    projection.step(begin)
    first_result, first_audit = projection.step(update)
    after_first = projection.snapshot()
    replay_result, replay_audit = projection.step(update)

    assert first_result.status == "ok"
    assert first_audit.changed_row_count == 1
    assert replay_result == first_result.model_copy(update={"idempotency_hit": True})
    assert replay_audit.decision == "idempotent"
    assert replay_audit.idempotency_hit is True
    assert replay_audit.changed_row_count == 0
    assert replay_audit.before_digest == replay_audit.after_digest
    assert projection.snapshot() == after_first


@pytest.mark.parametrize(
    "mutation",
    [
        "delete_record",
        "duplicate_record",
        "task",
        "pair_arm",
        "record_split",
        "record_tag",
        "record_schedule",
        "record_fingerprint",
        "record_seed",
        "record_config",
        "record_pair_id",
        "terminal",
        "usage",
        "raw_context",
        "raw_schedule",
        "raw_config",
        "raw_verifier",
        "raw_limitation",
        "raw_event_alias",
        "raw_hash",
        "numerator",
        "denominator",
        "rate",
        "na_to_zero",
        "markdown",
        "manifest_limitation",
        "path_escape",
        "extra_result",
        "joint_summaries",
    ],
)
def test_read_only_validator_rejects_raw_record_summary_report_and_path_tamper(
    reliability_bundle: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / mutation
    shutil.copytree(reliability_bundle, root)
    records_path = root / "records.jsonl"
    lines = records_path.read_text(encoding="utf-8").splitlines()
    manifest_path = root / "eval-manifest.json"
    summary_path = root / "summary.json"
    if mutation == "delete_record":
        del lines[0]
        records_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "duplicate_record":
        records_path.write_text("\n".join([*lines, lines[0]]) + "\n", encoding="utf-8")
    elif mutation in {
        "task",
        "pair_arm",
        "record_split",
        "record_tag",
        "record_schedule",
        "record_fingerprint",
        "record_seed",
        "record_config",
        "record_pair_id",
    }:
        payload = json.loads(lines[0])
        changes = {
            "task": ("task_id", "workspace.fix-config"),
            "pair_arm": ("arm", "mechanism"),
            "record_split": ("split", "train"),
            "record_tag": ("tags", ["tampered"]),
            "record_schedule": ("schedule_id", "workspace.other-schedule.v1"),
            "record_fingerprint": ("resource_fingerprint", "0" * 64),
            "record_seed": ("seed", 999),
            "record_config": ("config_fingerprint", "0" * 64),
            "record_pair_id": ("pair_id", "other-pair"),
        }
        field, value = changes[mutation]
        payload[field] = value
        lines[0] = json.dumps(payload, separators=(",", ":"))
        records_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation in {
        "terminal",
        "usage",
        "raw_context",
        "raw_schedule",
        "raw_config",
        "raw_verifier",
        "raw_limitation",
        "raw_event_alias",
    }:
        raw = next(root.glob("runs/runtime-reliability-v1/*/result.json"))
        payload = json.loads(raw.read_text(encoding="utf-8"))
        if mutation == "terminal":
            payload["terminal_state"] = "SUCCEEDED"
        elif mutation == "usage":
            payload["usage"]["duplicate_side_effects"] = 999
        elif mutation == "raw_context":
            payload["run_id"] = "eval-other-context"
        elif mutation == "raw_schedule":
            payload["schedule_id"] = "workspace.other-schedule.v1"
        elif mutation == "raw_config":
            payload["config"]["max_steps"] = 999
        elif mutation == "raw_verifier":
            payload["verifier"]["score"] = 0.5
        elif mutation == "raw_limitation":
            payload["limitation"] = "rewritten limitation"
        else:
            payload["events_file"] = "other-events.jsonl"
        raw.write_text(json.dumps(payload), encoding="utf-8")
        rehash_raw_artifacts(root)
    elif mutation == "raw_hash":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["artifacts"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation in {"numerator", "denominator", "rate", "na_to_zero"}:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        field = "verifier_state_success"
        if mutation == "numerator":
            payload[field]["numerator"] -= 1
        elif mutation == "denominator":
            payload[field]["denominator"] -= 1
        elif mutation == "rate":
            payload[field]["rate"] = 0.123
        else:
            payload["model"] = 0
        summary_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "markdown":
        (root / "report.md").write_text("changed denominator\n", encoding="utf-8")
    elif mutation == "manifest_limitation":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["limitation"] = "rewritten limitation"
        payload["bundle_fingerprint"] = compute_bundle_fingerprint(payload)
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "path_escape":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["artifacts"][0]["path"] = "../outside.json"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "extra_result":
        (root / "runs/runtime-reliability-v1/orphan.json").write_text("{}", encoding="utf-8")
    else:
        records = [json.loads(line) for line in lines]
        records[0]["verifier_state_success"] = not records[0]["verifier_state_success"]
        records_path.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in records),
            encoding="utf-8",
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["verifier_state_success"]["numerator"] -= 1
        summary["verifier_state_success"]["rate"] = (
            summary["verifier_state_success"]["numerator"]
            / summary["verifier_state_success"]["denominator"]
        )
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        (root / "report.md").write_text("coordinated rewrite\n", encoding="utf-8")

    with pytest.raises(EvalBundleValidationError):
        validate_eval_bundle(root)


@pytest.mark.parametrize("bundle_fixture", ["reliability_bundle", "system_bundle"])
def test_validator_is_zero_execution_and_preserves_every_source_byte(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    bundle_fixture: str,
) -> None:
    bundle = request.getfixturevalue(bundle_fixture)
    assert isinstance(bundle, Path)
    before = directory_bytes(bundle)

    def fail_execution(*args: object, **kwargs: object) -> None:
        raise AssertionError("read-only validator called an execution component")

    for target in (
        "agent_learning_loop.runtime.execute_runtime_task",
        "agent_learning_loop.vertical_slice.execute_task",
        "agent_learning_loop.incident_runner.run_incident_task",
        "agent_learning_loop.dataops_runner.run_dataops_task",
        "agent_learning_loop.durable_runtime.execute_durable_task",
        "agent_learning_loop.action_replay.replay_actions",
        "agent_learning_loop.policy.ScriptedPolicy",
        "agent_learning_loop.workspace.WorkspaceEnvironment",
        "agent_learning_loop.incident_environment.IncidentEnvironment",
        "agent_learning_loop.dataops_environment.DataOpsEnvironment",
        "agent_learning_loop.workspace_tools.ReadTextTool",
        "agent_learning_loop.workspace_tools.WriteTextTool",
        "socket.socket",
        "subprocess.run",
    ):
        monkeypatch.setattr(target, fail_execution)

    result = validate_eval_bundle(bundle)

    assert result.execution_calls == 0
    assert result.source_bytes_unchanged is True
    assert directory_bytes(bundle) == before


@pytest.mark.parametrize(
    ("relative_path", "field", "value"),
    [
        (
            "runs/recovery-replay-v1/recovery.checkpoint-off/diagnostic.json",
            "second_process_exit_code",
            0,
        ),
        (
            "runs/recovery-replay-v1/recovery.checkpoint-on/diagnostic.json",
            "reference_match",
            False,
        ),
        (
            "runs/recovery-replay-v1/recovery.action-replay/diagnostic.json",
            "policy_calls",
            1,
        ),
        (
            "runs/recovery-replay-v1/recovery.checkpoint-off/diagnostic.json",
            "physical_executions",
            1,
        ),
    ],
)
def test_recovery_evidence_tamper_fails_after_raw_hashes_are_recomputed(
    recovery_bundle: Path,
    tmp_path: Path,
    relative_path: str,
    field: str,
    value: object,
) -> None:
    root = tmp_path / field
    shutil.copytree(recovery_bundle, root)
    target = root / relative_path
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload[field] = value
    target.write_text(json.dumps(payload), encoding="utf-8")
    rehash_raw_artifacts(root)

    with pytest.raises(EvalBundleValidationError):
        validate_eval_bundle(root)
