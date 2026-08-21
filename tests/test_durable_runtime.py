from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_learning_loop.durable_runtime import (
    ControlledInterruption,
    DurableValidationError,
    execute_durable_task,
    resume_durable_task,
)
from agent_learning_loop.durable_schemas import CheckpointingMode
from agent_learning_loop.event_replay import (
    TrajectoryValidationError,
    validate_trajectory,
    workspace_digest,
)
from agent_learning_loop.failure_schedules import load_failure_schedule
from agent_learning_loop.interruption_schedules import load_interruption_schedule
from agent_learning_loop.runtime_schemas import RuntimeConfig, RuntimeMode, RuntimeState
from agent_learning_loop.tasks import load_task


def config() -> RuntimeConfig:
    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    return RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
    )


def interrupt_run(run_directory: Path, checkpointing: CheckpointingMode) -> None:
    with pytest.raises(ControlledInterruption):
        execute_durable_task(
            load_task("workspace.fix-config"),
            run_directory,
            run_id="durable-run",
            config=config(),
            failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
            checkpointing=checkpointing,
            interruption_schedule=load_interruption_schedule(
                "workspace.post-write-boundary.v1"
            ),
        )


def test_checkpoint_off_leaves_valid_partial_but_resume_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "off"
    interrupt_run(run_directory, CheckpointingMode.OFF)
    before_journal = (run_directory / "events.jsonl").read_bytes()
    before_workspace = (run_directory / "workspace" / "app.conf").read_bytes()

    class ComponentCalled(AssertionError):
        pass

    def fail_component(*args: object, **kwargs: object) -> None:
        raise ComponentCalled("resume called a component before validation")

    monkeypatch.setattr("agent_learning_loop.durable_runtime.WorkspaceEnvironment", fail_component)

    assert validate_trajectory(run_directory).status == "valid_partial"
    with pytest.raises(DurableValidationError, match="checkpoint"):
        resume_durable_task(run_directory)

    assert (run_directory / "events.jsonl").read_bytes() == before_journal
    assert (run_directory / "workspace" / "app.conf").read_bytes() == before_workspace
    assert not (run_directory / "result.json").exists()


def test_checkpoint_on_resumes_without_reset_or_duplicate_write_and_matches_reference(
    tmp_path: Path,
) -> None:
    resumed_directory = tmp_path / "resumed"
    interrupt_run(resumed_directory, CheckpointingMode.ON)
    prefix = (resumed_directory / "events.jsonl").read_bytes()
    partial = validate_trajectory(resumed_directory)

    result = resume_durable_task(resumed_directory)

    reference_directory = tmp_path / "reference"
    reference = execute_durable_task(
        load_task("workspace.fix-config"),
        reference_directory,
        run_id="reference-run",
        config=config(),
        failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
    )

    assert partial.status == "valid_partial"
    assert result.run_id == "durable-run"
    assert result.terminal_state is RuntimeState.SUCCEEDED
    assert result.verifier.passed is True
    assert result.resumed is True
    assert result.segment_count == 2
    assert result.usage.steps == 3
    assert result.usage.tool_calls == 3
    assert result.usage.physical_executions == 2
    assert result.usage.physical_write_executions == 1
    assert result.usage.side_effect_executions == 1
    assert result.usage.duplicate_side_effects == 0
    assert result.usage.retries == 1
    assert result.usage.idempotency_hits == 1
    assert (resumed_directory / "events.jsonl").read_bytes().startswith(prefix)
    assert validate_trajectory(resumed_directory).status == "valid_completed"
    assert reference.terminal_state is RuntimeState.SUCCEEDED
    assert reference.verifier == result.verifier
    assert reference.usage.physical_write_executions == 1
    assert reference.usage.duplicate_side_effects == 0
    assert workspace_files(resumed_directory) == workspace_files(reference_directory)


def test_resume_rejects_tampered_journal_or_workspace_before_component_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal_run = tmp_path / "journal-tamper"
    interrupt_run(journal_run, CheckpointingMode.ON)
    lines = (journal_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["payload"]["seed"] = 999
    lines[0] = json.dumps(payload, separators=(",", ":"))
    (journal_run / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    workspace_run = tmp_path / "workspace-tamper"
    interrupt_run(workspace_run, CheckpointingMode.ON)
    (workspace_run / "workspace" / "app.conf").write_text(
        "mode=tampered\n", encoding="utf-8"
    )

    class ComponentCalled(AssertionError):
        pass

    def fail_component(*args: object, **kwargs: object) -> None:
        raise ComponentCalled("resume called a component before validation")

    monkeypatch.setattr("agent_learning_loop.durable_runtime.WorkspaceEnvironment", fail_component)

    with pytest.raises(DurableValidationError):
        resume_durable_task(journal_run)
    with pytest.raises(DurableValidationError, match="Workspace"):
        resume_durable_task(workspace_run)


def test_private_expected_secret_read_result_and_raw_exception_are_not_persisted(
    tmp_path: Path,
) -> None:
    fixture = load_task("workspace.fix-config").model_copy(deep=True)
    private_marker = "PRIVATE_EXPECTED_MARKER_DO_NOT_PERSIST"
    secret_marker = "ghp_read_result_marker_do_not_persist"
    fixture.private.expected.forbidden_paths.append(private_marker)
    fixture.private.setup.files["app.conf"] = f"{secret_marker}\nport=8080\n"
    run_directory = tmp_path / "safe-fields"

    result = execute_durable_task(
        fixture,
        run_directory,
        run_id="safe-fields-run",
        config=config(),
        failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
        checkpointing=CheckpointingMode.OFF,
        interruption_schedule=None,
    )

    assert result.terminal_state is RuntimeState.SUCCEEDED
    public_text = (run_directory / "events.jsonl").read_text(encoding="utf-8")
    public_text += (run_directory / "result.json").read_text(encoding="utf-8")
    assert private_marker not in public_text
    assert secret_marker not in public_text
    assert "Traceback" not in public_text
    assert "\\Users\\" not in public_text

    interrupted_directory = tmp_path / "safe-checkpoint-fields"
    with pytest.raises(ControlledInterruption):
        execute_durable_task(
            fixture,
            interrupted_directory,
            run_id="safe-checkpoint-run",
            config=config(),
            failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
            checkpointing=CheckpointingMode.ON,
            interruption_schedule=load_interruption_schedule(
                "workspace.post-write-boundary.v1"
            ),
        )
    checkpoint_text = (interrupted_directory / "checkpoint.json").read_text(
        encoding="utf-8"
    )
    journal_text = (interrupted_directory / "events.jsonl").read_text(encoding="utf-8")
    assert private_marker not in checkpoint_text + journal_text
    assert secret_marker not in checkpoint_text + journal_text
    assert "Traceback" not in checkpoint_text + journal_text
    assert "\\Users\\" not in checkpoint_text + journal_text


def test_checkpoint_write_failure_never_reports_interruption_or_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "checkpoint-write-failure"

    def fail_checkpoint(*args: object, **kwargs: object) -> None:
        raise OSError("controlled checkpoint write failure")

    monkeypatch.setattr(
        "agent_learning_loop.durable_runtime.write_checkpoint_atomic", fail_checkpoint
    )
    with pytest.raises(OSError, match="controlled checkpoint write failure"):
        execute_durable_task(
            load_task("workspace.fix-config"),
            run_directory,
            run_id="checkpoint-failure-run",
            config=config(),
            failure_schedule=load_failure_schedule("workspace.lost-write-result.v1"),
            checkpointing=CheckpointingMode.ON,
            interruption_schedule=load_interruption_schedule(
                "workspace.post-write-boundary.v1"
            ),
        )

    assert not (run_directory / "checkpoint.json").exists()
    assert not (run_directory / "result.json").exists()
    assert '"event_kind":"interruption_injected"' not in (
        run_directory / "events.jsonl"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "different-run"),
        ("task_id", "workspace.build-summary"),
        ("failure_schedule_id", "workspace.transient-read.v1"),
        ("config_max_steps", 7),
        ("counter_tool_calls", 2),
    ],
)
def test_resume_rejects_run_task_schedule_or_config_identity_before_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    run_directory = tmp_path / field
    interrupt_run(run_directory, CheckpointingMode.ON)
    checkpoint_path = run_directory / "checkpoint.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if field == "run_id":
        payload["run_id"] = value
    elif field == "task_id":
        payload["identity"]["task_id"] = value
    elif field == "failure_schedule_id":
        payload["identity"]["failure_schedule_id"] = value
    elif field == "config_max_steps":
        payload["runtime_config"]["max_steps"] = value
    else:
        payload["usage"]["tool_calls"] = value
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    def fail_component(*args: object, **kwargs: object) -> None:
        raise AssertionError("resume constructed an execution component before validation")

    monkeypatch.setattr("agent_learning_loop.durable_runtime.WorkspaceEnvironment", fail_component)
    monkeypatch.setattr("agent_learning_loop.durable_runtime.ScriptedPolicy", fail_component)
    monkeypatch.setattr(
        "agent_learning_loop.durable_runtime.WorkspaceStateVerifier", fail_component
    )
    monkeypatch.setattr("agent_learning_loop.durable_runtime.ReadTextTool", fail_component)

    with pytest.raises(DurableValidationError):
        resume_durable_task(run_directory)


def test_workspace_and_checkpoint_digest_rewrite_fails_before_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / "combined-workspace-digest"
    interrupt_run(run_directory, CheckpointingMode.ON)
    (run_directory / "workspace" / "notes.txt").write_text(
        "tampered note\n", encoding="utf-8"
    )
    checkpoint_path = run_directory / "checkpoint.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["workspace_digest"] = workspace_digest(run_directory / "workspace")
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    component_calls: list[str] = []

    def fail_component(*args: object, **kwargs: object) -> None:
        component_calls.append("called")
        raise AssertionError("resume constructed an execution component")

    monkeypatch.setattr("agent_learning_loop.durable_runtime.WorkspaceEnvironment", fail_component)
    monkeypatch.setattr("agent_learning_loop.durable_runtime.ScriptedPolicy", fail_component)
    monkeypatch.setattr(
        "agent_learning_loop.durable_runtime.WorkspaceStateVerifier", fail_component
    )
    monkeypatch.setattr("agent_learning_loop.durable_runtime.ReadTextTool", fail_component)

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)
    with pytest.raises(DurableValidationError):
        resume_durable_task(run_directory)
    assert component_calls == []


@pytest.mark.parametrize("mutation", ["elapsed", "counter", "idempotency"])
def test_checkpoint_recovery_state_tamper_fails_before_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    run_directory = tmp_path / mutation
    interrupt_run(run_directory, CheckpointingMode.ON)
    checkpoint_path = run_directory / "checkpoint.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if mutation == "elapsed":
        payload["usage"]["elapsed_seconds"] = 0.0
    elif mutation == "counter":
        payload["usage"]["physical_write_executions"] = 2
    else:
        payload["idempotency_entries"][0]["bytes_written"] += 1
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    component_calls: list[str] = []

    def fail_component(*args: object, **kwargs: object) -> None:
        component_calls.append("called")
        raise AssertionError("resume constructed an execution component")

    monkeypatch.setattr("agent_learning_loop.durable_runtime.WorkspaceEnvironment", fail_component)
    monkeypatch.setattr("agent_learning_loop.durable_runtime.ScriptedPolicy", fail_component)
    monkeypatch.setattr(
        "agent_learning_loop.durable_runtime.WorkspaceStateVerifier", fail_component
    )
    monkeypatch.setattr("agent_learning_loop.durable_runtime.ReadTextTool", fail_component)

    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)
    with pytest.raises(DurableValidationError):
        resume_durable_task(run_directory)
    assert component_calls == []


@dataclass
class FakeClock:
    current: float = 0.0
    sleep_calls: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current += seconds


def test_backoff_beyond_remaining_deadline_fails_without_sleep_or_checkpoint(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "backoff-deadline"
    schedule = load_failure_schedule("workspace.lost-write-result.v1")
    clock = FakeClock()
    deadline_config = RuntimeConfig.for_mode(
        RuntimeMode.SAFEGUARDED,
        schedule_id=schedule.schedule_id,
        seed=schedule.seed,
        timeout_seconds=0.5,
        retry_backoff_seconds=[1.0],
    )

    with pytest.raises(DurableValidationError, match="deadline"):
        execute_durable_task(
            load_task("workspace.fix-config"),
            run_directory,
            run_id="deadline-run",
            config=deadline_config,
            failure_schedule=schedule,
            checkpointing=CheckpointingMode.ON,
            interruption_schedule=load_interruption_schedule(
                "workspace.post-write-boundary.v1"
            ),
            clock=clock,
        )

    assert clock.sleep_calls == []
    assert not (run_directory / "checkpoint.json").exists()
    assert not (run_directory / "result.json").exists()
    journal = (run_directory / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_kind":"checkpoint_committed"' not in journal
    assert '"event_kind":"interruption_injected"' not in journal
    with pytest.raises(TrajectoryValidationError):
        validate_trajectory(run_directory)


def workspace_files(run_directory: Path) -> dict[str, bytes]:
    root = run_directory / "workspace"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
