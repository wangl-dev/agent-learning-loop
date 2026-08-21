from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_learning_loop.action_catalog import load_action_catalog
from agent_learning_loop.action_journal import read_and_validate_action_journal
from agent_learning_loop.action_replay import (
    ActionReplayResult,
    ActionReplayValidationError,
    replay_actions,
)
from agent_learning_loop.cli import main as cli_main
from agent_learning_loop.durable_runtime import execute_durable_task
from agent_learning_loop.durable_schemas import (
    CheckpointingMode,
    verifier_summary_digest,
)
from agent_learning_loop.failure_schedules import load_failure_schedule
from agent_learning_loop.journal import record_hash
from agent_learning_loop.policy import ScriptedPolicy
from agent_learning_loop.protocols import WorkspaceOperationsProtocol
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode
from agent_learning_loop.schemas import Action, ToolResult, VerifierResult
from agent_learning_loop.tasks import load_task
from agent_learning_loop.workspace_tools import WriteTextTool


def durable_config() -> RuntimeConfig:
    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    return RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
    )


def create_source(run_directory: Path) -> None:
    execute_durable_task(
        load_task("workspace.fix-config"),
        run_directory,
        run_id="action-source",
        config=durable_config(),
        failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
        record_actions=True,
    )


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def rewrite_action_journal(path: Path, index: int, updates: dict[str, object]) -> None:
    payloads = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    payloads[index].update(updates)
    previous_hash = ""
    for payload in payloads:
        payload["previous_record_hash"] = previous_hash
        payload.pop("record_hash")
        payload["record_hash"] = record_hash(payload)
        previous_hash = payload["record_hash"]
    path.write_text(
        "\n".join(json.dumps(payload, separators=(",", ":")) for payload in payloads)
        + "\n",
        encoding="utf-8",
    )


def test_source_records_two_logical_actions_and_replay_matches_without_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "replay"
    create_source(source)
    records = read_and_validate_action_journal(source / "actions.jsonl")
    source_before = directory_bytes(source)

    def fail_policy(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay called Policy")

    monkeypatch.setattr(ScriptedPolicy, "decide", fail_policy)
    result = replay_actions(source, output)

    assert len(records) == 6
    assert [record.event_kind for record in records] == [
        "source_started",
        "action_started",
        "action_finished",
        "action_started",
        "action_finished",
        "source_finished",
    ]
    finished = [record for record in records if record.event_kind == "action_finished"]
    assert [record.attempt_count for record in finished] == [1, 2]
    assert result.actions_resolved == 2
    assert result.actions_total == 2
    assert result.step_digests_matched == 2
    assert result.step_digests_total == 2
    assert result.final_snapshot_match is True
    assert result.verifier_match is True
    assert result.verifier.passed is True
    assert result.task_match is True
    assert result.source_unchanged is True
    assert result.policy_calls == 0
    assert result.usage.tool_calls == 2
    assert result.usage.physical_executions == 2
    assert result.usage.physical_write_executions == 1
    assert result.usage.side_effect_executions == 1
    assert result.usage.duplicate_side_effects == 0
    assert result.action_replay_match_rate == 1.0
    assert result.vertical_slice_matches == 1
    assert result.vertical_slice_total == 1
    assert directory_bytes(source) == source_before
    assert (output / "replay-result.json").exists()
    public_metadata = (source / "actions.jsonl").read_text(
        encoding="utf-8"
    ) + (output / "replay-result.json").read_text(encoding="utf-8")
    for forbidden in (
        '"arguments"',
        '"content"',
        '"tool_result"',
        "mode=debug",
        "mode=production",
        "keep this note",
        str(source.resolve()),
        str(output.resolve()),
    ):
        assert forbidden not in public_metadata


def test_recording_default_off_and_invalid_interrupted_source_are_rejected(
    tmp_path: Path,
) -> None:
    source = tmp_path / "default-off"
    execute_durable_task(
        load_task("workspace.fix-config"),
        source,
        run_id="default-off",
        config=durable_config(),
        failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
    )
    assert not (source / "actions.jsonl").exists()

    with pytest.raises(ValueError, match="recording"):
        execute_durable_task(
            load_task("workspace.fix-config"),
            tmp_path / "invalid-recording",
            run_id="invalid-recording",
            config=durable_config(),
            failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
            checkpointing=CheckpointingMode.OFF,
            interruption_schedule=__import__(
                "agent_learning_loop.interruption_schedules", fromlist=["x"]
            ).load_interruption_schedule("workspace.post-write-boundary.v1"),
            record_actions=True,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "event",
        "result",
        "workspace",
        "action_journal",
        "action_fingerprint",
        "binding",
        "catalog",
    ],
)
def test_source_or_catalog_tamper_fails_before_component_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    source = tmp_path / mutation
    create_source(source)
    if mutation == "event":
        path = source / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[0])
        payload["payload"]["seed"] = 999
        lines[0] = json.dumps(payload, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "result":
        path = source / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["usage"]["physical_write_executions"] = 999
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "workspace":
        (source / "workspace" / "notes.txt").write_text(
            "tampered\n", encoding="utf-8"
        )
    elif mutation == "action_journal":
        path = source / "actions.jsonl"
        rewrite_action_journal(
            path,
            1,
            {"action_ref": "workspace.fix-config.step-9.v1"},
        )
    elif mutation == "action_fingerprint":
        rewrite_action_journal(
            source / "actions.jsonl",
            1,
            {"action_fingerprint": "0" * 64},
        )
    elif mutation == "binding":
        rewrite_action_journal(
            source / "actions.jsonl",
            5,
            {"source_event_final_hash": "0" * 64},
        )
    else:
        catalog = load_action_catalog("workspace.fix-config")
        changed_entry = catalog.actions[0].model_copy(
            update={"action_ref": "workspace.fix-config.step-9.v1"}
        )
        changed_catalog = catalog.model_copy(
            update={"actions": [changed_entry, *catalog.actions[1:]]}
        )
        monkeypatch.setattr(
            "agent_learning_loop.action_replay.load_action_catalog",
            lambda task_id: changed_catalog,
        )

    component_calls: list[str] = []

    def fail_component(*args: object, **kwargs: object) -> None:
        component_calls.append("called")
        raise AssertionError("replay constructed a component before validation")

    for name in (
        "WorkspaceEnvironment",
        "ReadTextTool",
        "WriteTextTool",
        "WorkspaceStateVerifier",
    ):
        monkeypatch.setattr(f"agent_learning_loop.action_replay.{name}", fail_component)

    with pytest.raises(ActionReplayValidationError):
        replay_actions(source, tmp_path / f"{mutation}-output")
    assert component_calls == []


@pytest.mark.parametrize("relationship", ["same", "source_child", "source_parent", "nonempty"])
def test_replay_rejects_same_nested_or_nonempty_output_before_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relationship: str
) -> None:
    source = tmp_path / "source"
    create_source(source)
    if relationship == "same":
        output = source
    elif relationship == "source_child":
        output = source / "replay"
    elif relationship == "source_parent":
        output = tmp_path
    else:
        output = tmp_path / "nonempty"
        output.mkdir()
        (output / "user.txt").write_text("keep\n", encoding="utf-8")
    calls: list[str] = []

    def fail_component(*args: object, **kwargs: object) -> None:
        calls.append("called")
        raise AssertionError("component constructed for invalid paths")

    monkeypatch.setattr("agent_learning_loop.action_replay.WorkspaceEnvironment", fail_component)

    with pytest.raises(ActionReplayValidationError):
        replay_actions(source, output)
    assert calls == []


def test_valid_execution_mismatch_writes_honest_structured_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "mismatch"
    create_source(source)
    source_before = directory_bytes(source)

    class MismatchWriteTool(WriteTextTool):
        def execute(
            self, environment: WorkspaceOperationsProtocol, action: Action
        ) -> ToolResult:
            result = super().execute(environment, action)
            environment.write_text("app.conf", "mode=staging\nport=8080\n")
            return result

    monkeypatch.setattr(
        "agent_learning_loop.action_replay.WriteTextTool", MismatchWriteTool
    )

    result = replay_actions(source, output)

    assert result.action_replay_match_rate == 0.0
    assert result.vertical_slice_matches == 0
    assert result.step_digests_matched == 1
    assert result.final_snapshot_match is False
    assert result.verifier_match is False
    assert result.verifier.passed is False
    assert result.source_unchanged is True
    assert directory_bytes(source) == source_before
    restored = ActionReplayResult.model_validate_json(
        (output / "replay-result.json").read_text(encoding="utf-8")
    )
    assert restored == result
    cli_output = tmp_path / "mismatch-cli"
    assert (
        cli_main(
            [
                "replay-actions",
                "--source-run-dir",
                str(source),
                "--output-dir",
                str(cli_output),
            ]
        )
        == 1
    )
    assert (cli_output / "replay-result.json").exists()


def create_match_payload(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "semantic-source"
    create_source(source)
    result = replay_actions(source, tmp_path / "semantic-replay")
    return result.model_dump(mode="json")


def test_replay_result_schema_is_strict(tmp_path: Path) -> None:
    payload = create_match_payload(tmp_path)
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        ActionReplayResult.model_validate(payload)


def test_success_rate_rejects_each_broken_success_condition(
    tmp_path: Path,
) -> None:
    matched = create_match_payload(tmp_path)
    broken: list[tuple[str, dict[str, object]]] = []

    def add(name: str, payload: dict[str, object]) -> None:
        broken.append((name, payload))

    changed = copy.deepcopy(matched)
    changed["actions_resolved"] = 1
    add("actions", changed)

    changed = copy.deepcopy(matched)
    changed["step_digests_matched"] = 1
    add("steps", changed)

    changed = copy.deepcopy(matched)
    changed["replay_final_workspace_digest"] = "0" * 64
    changed["final_snapshot_match"] = False
    add("final snapshot", changed)

    changed = copy.deepcopy(matched)
    changed["source_verifier_digest"] = "0" * 64
    changed["verifier_match"] = False
    add("Verifier match", changed)

    changed = copy.deepcopy(matched)
    changed["task_match"] = False
    add("task", changed)

    changed = copy.deepcopy(matched)
    changed["source_unchanged"] = False
    add("source immutability", changed)

    changed = copy.deepcopy(matched)
    verifier_payload = changed["verifier"]
    assert isinstance(verifier_payload, dict)
    verifier_payload["passed"] = False
    verifier_payload["score"] = 0.0
    checks = verifier_payload["checks"]
    assert isinstance(checks, list)
    first_check = checks[0]
    assert isinstance(first_check, dict)
    first_check["passed"] = False
    first_check["detail"] = "required file state differed"
    failed_verifier = VerifierResult.model_validate(verifier_payload)
    failed_digest = verifier_summary_digest(failed_verifier)
    changed["source_verifier_digest"] = failed_digest
    changed["replay_verifier_digest"] = failed_digest
    add("Verifier passed", changed)

    changed = copy.deepcopy(matched)
    changed["policy_calls"] = 1
    add("Policy calls", changed)

    for counter, failure_value in (
        ("tool_calls", 1),
        ("physical_executions", 1),
        ("physical_write_executions", 0),
        ("side_effect_executions", 0),
        ("duplicate_side_effects", 1),
    ):
        changed = copy.deepcopy(matched)
        usage = changed["usage"]
        assert isinstance(usage, dict)
        usage[counter] = failure_value
        add(counter, changed)

    for _, payload in broken:
        with pytest.raises(ValidationError):
            ActionReplayResult.model_validate(payload)


def test_success_rate_is_bidirectional_and_counts_stay_in_fixed_slice(
    tmp_path: Path,
) -> None:
    matched = create_match_payload(tmp_path)
    false_failure = copy.deepcopy(matched)
    false_failure["action_replay_match_rate"] = 0.0
    false_failure["vertical_slice_matches"] = 0
    with pytest.raises(ValidationError):
        ActionReplayResult.model_validate(false_failure)

    invalid_counts: list[dict[str, object]] = []
    for field, value in (
        ("actions_resolved", 3),
        ("step_digests_matched", 3),
        ("actions_total", 3),
        ("step_digests_total", 3),
    ):
        changed = copy.deepcopy(matched)
        changed[field] = value
        invalid_counts.append(changed)
    for payload in invalid_counts:
        with pytest.raises(ValidationError):
            ActionReplayResult.model_validate(payload)


def test_workspace_and_verifier_match_flags_bind_their_digests(
    tmp_path: Path,
) -> None:
    matched = create_match_payload(tmp_path)
    inconsistent: list[dict[str, object]] = []

    changed = copy.deepcopy(matched)
    changed["final_snapshot_match"] = False
    changed["action_replay_match_rate"] = 0.0
    changed["vertical_slice_matches"] = 0
    inconsistent.append(changed)

    changed = copy.deepcopy(matched)
    changed["replay_final_workspace_digest"] = "0" * 64
    changed["action_replay_match_rate"] = 0.0
    changed["vertical_slice_matches"] = 0
    inconsistent.append(changed)

    changed = copy.deepcopy(matched)
    changed["verifier_match"] = False
    changed["action_replay_match_rate"] = 0.0
    changed["vertical_slice_matches"] = 0
    inconsistent.append(changed)

    changed = copy.deepcopy(matched)
    changed["source_verifier_digest"] = "0" * 64
    changed["action_replay_match_rate"] = 0.0
    changed["vertical_slice_matches"] = 0
    inconsistent.append(changed)

    changed = copy.deepcopy(matched)
    changed["source_verifier_digest"] = "0" * 64
    changed["replay_verifier_digest"] = "0" * 64
    inconsistent.append(changed)

    for payload in inconsistent:
        with pytest.raises(ValidationError):
            ActionReplayResult.model_validate(payload)
