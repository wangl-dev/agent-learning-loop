"""M3B reference-based action replay for one validated durable source run."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from agent_learning_loop.action_catalog import (
    GOLDEN_CATALOG_FINGERPRINTS,
    ActionCatalog,
    CatalogAction,
    action_fingerprint,
    catalog_fingerprint,
    load_action_catalog,
)
from agent_learning_loop.action_journal import (
    ActionJournalRecord,
    ActionJournalValidationError,
    read_and_validate_action_journal,
)
from agent_learning_loop.durable_schemas import (
    CheckpointingMode,
    DurableEvent,
    DurableResult,
    durable_result_summary_digest,
    verifier_summary_digest,
)
from agent_learning_loop.event_replay import (
    TrajectoryValidationError,
    validate_trajectory,
    workspace_digest,
)
from agent_learning_loop.journal import (
    JournalValidationError,
    read_and_validate_journal,
)
from agent_learning_loop.runtime_schemas import RuntimeMode, RuntimeState
from agent_learning_loop.schemas import (
    StrictModel,
    ToolResult,
    VerifierResult,
    WorkspaceSnapshot,
    WorkspaceTaskFixture,
)
from agent_learning_loop.tasks import load_task
from agent_learning_loop.verifier import WorkspaceStateVerifier
from agent_learning_loop.workspace import WorkspaceEnvironment
from agent_learning_loop.workspace_tools import ReadTextTool, WriteTextTool


class ActionReplayValidationError(ValueError):
    """The source, catalog, or output boundary cannot safely be replayed."""


class ActionReplayUsage(StrictModel):
    schema_version: Literal["1"] = "1"
    tool_calls: int = Field(ge=0)
    physical_executions: int = Field(ge=0)
    physical_write_executions: int = Field(ge=0)
    side_effect_executions: int = Field(ge=0)
    duplicate_side_effects: int = Field(ge=0)


class ActionReplayResult(StrictModel):
    schema_version: Literal["1"] = "1"
    source_run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    task_id: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    catalog_id: str = Field(min_length=1)
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_final_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_action_final_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_result_summary_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actions_resolved: int = Field(ge=0)
    actions_total: int = Field(ge=0)
    step_digests_matched: int = Field(ge=0)
    step_digests_total: int = Field(ge=0)
    source_final_workspace_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    replay_final_workspace_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_verifier_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    replay_verifier_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier: VerifierResult
    usage: ActionReplayUsage
    final_snapshot_match: bool
    verifier_match: bool
    task_match: bool
    source_unchanged: bool
    policy_calls: Literal[0] = 0
    action_replay_match_rate: float = Field(ge=0.0, le=1.0)
    vertical_slice_matches: Literal[0, 1]
    vertical_slice_total: Literal[1] = 1
    workspace_dir: Literal["workspace"] = "workspace"
    limitation: str = (
        "M3B replays only validated logical action references in a new Workspace; "
        "it does not rerun Policy, retry failures, resume checkpoints, or promise "
        "arbitrary action replay."
    )

    @model_validator(mode="after")
    def match_rate_is_binary_and_consistent(self) -> Self:
        if self.action_replay_match_rate not in (0.0, 1.0):
            raise ValueError("action replay match rate must be binary")
        if self.actions_total != 2 or self.step_digests_total != 2:
            raise ValueError("replay totals do not match the fixed two-action slice")
        if (
            self.actions_resolved > self.actions_total
            or self.step_digests_matched > self.step_digests_total
        ):
            raise ValueError("replay matched counts exceed their totals")

        workspace_digests_match = (
            self.source_final_workspace_digest
            == self.replay_final_workspace_digest
        )
        if self.final_snapshot_match != workspace_digests_match:
            raise ValueError("final snapshot flag does not match Workspace digests")

        if self.replay_verifier_digest != verifier_summary_digest(self.verifier):
            raise ValueError("replay Verifier digest does not match the full Verifier")
        verifier_digests_match = (
            self.source_verifier_digest == self.replay_verifier_digest
        )
        if self.verifier_match != verifier_digests_match:
            raise ValueError("Verifier match flag does not match Verifier digests")

        success = all(
            (
                self.actions_resolved == self.actions_total == 2,
                self.step_digests_matched == self.step_digests_total == 2,
                self.final_snapshot_match,
                self.verifier_match,
                self.verifier.passed,
                self.task_match,
                self.source_unchanged,
                self.policy_calls == 0,
                self.usage.tool_calls == 2,
                self.usage.physical_executions == 2,
                self.usage.physical_write_executions == 1,
                self.usage.side_effect_executions == 1,
                self.usage.duplicate_side_effects == 0,
            )
        )
        expected_rate = 1.0 if success else 0.0
        if self.action_replay_match_rate != expected_rate:
            raise ValueError("action replay rate contradicts the fixed success conditions")
        if self.vertical_slice_matches != int(self.action_replay_match_rate):
            raise ValueError("vertical-slice count must match action replay rate")
        return self


@dataclass(frozen=True)
class _ValidatedSource:
    source: Path
    output: Path
    result: DurableResult
    fixture: WorkspaceTaskFixture
    catalog: ActionCatalog
    records: list[ActionJournalRecord]
    source_snapshot: WorkspaceSnapshot
    source_manifest: dict[str, tuple[str, int, str]]


def replay_actions(source_run_directory: Path, output_directory: Path) -> ActionReplayResult:
    """Validate one fixed source completely, then execute its two catalog refs once."""
    validated = _validate_source(source_run_directory, output_directory)
    validated.output.mkdir(parents=True, exist_ok=True)
    environment = WorkspaceEnvironment(validated.output / "workspace")
    read_tool = ReadTextTool()
    write_tool = WriteTextTool()
    verifier = WorkspaceStateVerifier()
    step_digests_matched = 0
    tool_calls = 0
    physical_executions = 0
    physical_writes = 0
    all_tools_ok = True
    try:
        environment.reset(
            validated.fixture.private.setup,
            task_id=validated.fixture.task.task_id,
        )
        finished_records = [
            record
            for record in validated.records
            if record.event_kind == "action_finished"
        ]
        entries_by_ref = {
            entry.action_ref: entry for entry in validated.catalog.actions
        }
        resolved_entries = [
            entries_by_ref[record.action_ref or ""] for record in finished_records
        ]
        for entry, recorded in zip(resolved_entries, finished_records, strict=True):
            tool_calls += 1
            physical_executions += 1
            if entry.tool_name == "read_text":
                tool_result = read_tool.execute(environment, entry.action)
            elif entry.tool_name == "write_text":
                physical_writes += 1
                tool_result = write_tool.execute(environment, entry.action)
            else:  # Catalog validation currently prevents this fixed-slice branch.
                tool_result = ToolResult(status="error", payload={})
            all_tools_ok = all_tools_ok and tool_result.status == "ok"
            if (
                workspace_digest(environment.root)
                == recorded.post_action_workspace_digest
            ):
                step_digests_matched += 1

        replay_snapshot = environment.snapshot()
        replay_workspace_digest = workspace_digest(environment.root)
        verifier_result = verifier.verify(
            WorkspaceSnapshot(files=dict(validated.fixture.private.setup.files)),
            replay_snapshot,
            validated.fixture.private.expected,
        )
    finally:
        environment.close()

    source_finished = validated.records[-1]
    replay_verifier_digest = verifier_summary_digest(verifier_result)
    source_unchanged = _directory_manifest(validated.source) == validated.source_manifest
    final_snapshot_match = replay_snapshot == validated.source_snapshot
    verifier_match = replay_verifier_digest == source_finished.source_verifier_digest
    task_match = (
        validated.result.task_id == validated.fixture.task.task_id
        and validated.catalog.task_id == validated.fixture.task.task_id
    )
    matched = all(
        (
            all_tools_ok,
            len(validated.catalog.actions) == 2,
            step_digests_matched == len(validated.catalog.actions),
            final_snapshot_match,
            verifier_match,
            verifier_result.passed,
            task_match,
            source_unchanged,
            tool_calls == 2,
            physical_executions == 2,
            physical_writes == 1,
        )
    )
    result = ActionReplayResult(
        source_run_id=validated.result.run_id,
        task_id=validated.result.task_id,
        fixture_id=validated.result.identity.fixture_id,
        catalog_id=validated.catalog.catalog_id,
        catalog_fingerprint=catalog_fingerprint(validated.catalog),
        source_event_final_hash=validated.result.journal_final_hash,
        source_action_final_hash=source_finished.record_hash,
        source_result_summary_digest=durable_result_summary_digest(
            validated.result.summary()
        ),
        actions_resolved=len(validated.catalog.actions),
        actions_total=len(validated.catalog.actions),
        step_digests_matched=step_digests_matched,
        step_digests_total=len(validated.catalog.actions),
        source_final_workspace_digest=(
            source_finished.source_final_workspace_digest or ""
        ),
        replay_final_workspace_digest=replay_workspace_digest,
        source_verifier_digest=source_finished.source_verifier_digest or "",
        replay_verifier_digest=replay_verifier_digest,
        verifier=verifier_result,
        usage=ActionReplayUsage(
            tool_calls=tool_calls,
            physical_executions=physical_executions,
            physical_write_executions=physical_writes,
            side_effect_executions=physical_writes,
            duplicate_side_effects=max(physical_writes - 1, 0),
        ),
        final_snapshot_match=final_snapshot_match,
        verifier_match=verifier_match,
        task_match=task_match,
        source_unchanged=source_unchanged,
        action_replay_match_rate=1.0 if matched else 0.0,
        vertical_slice_matches=1 if matched else 0,
    )
    _write_result_once(validated.output / "replay-result.json", result)
    return result


def _validate_source(source: Path, output: Path) -> _ValidatedSource:
    source_resolved, output_resolved = _validate_paths(source, output)
    source_manifest = _directory_manifest(source_resolved)
    try:
        trajectory = validate_trajectory(source_resolved)
        result = DurableResult.model_validate_json(
            (source_resolved / "result.json").read_text(encoding="utf-8")
        )
        records = read_and_validate_action_journal(
            source_resolved / "actions.jsonl"
        )
        events = read_and_validate_journal(source_resolved / "events.jsonl")
        fixture = load_task("workspace.fix-config")
        catalog = load_action_catalog("workspace.fix-config")
        source_snapshot = _read_workspace_snapshot(source_resolved / "workspace")
    except (
        ActionJournalValidationError,
        JournalValidationError,
        OSError,
        TrajectoryValidationError,
        UnicodeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise ActionReplayValidationError(
            "source run failed complete replay prevalidation"
        ) from exc

    if trajectory.status != "valid_completed":
        raise ActionReplayValidationError("action replay needs a completed trajectory")
    _validate_fixed_source(result, fixture, catalog)
    _validate_action_records(result, catalog, records, events)
    if (
        workspace_digest(source_resolved / "workspace")
        != records[-1].source_final_workspace_digest
    ):
        raise ActionReplayValidationError(
            "source Workspace does not match source_finished"
        )
    expected_source_files = dict(fixture.private.setup.files)
    expected_source_files.update(fixture.private.expected.required_files)
    if source_snapshot != WorkspaceSnapshot(files=expected_source_files):
        raise ActionReplayValidationError(
            "source Workspace does not match the fixed verified final state"
        )
    if _directory_manifest(source_resolved) != source_manifest:
        raise ActionReplayValidationError("source changed during prevalidation")
    return _ValidatedSource(
        source=source_resolved,
        output=output_resolved,
        result=result,
        fixture=fixture,
        catalog=catalog,
        records=records,
        source_snapshot=source_snapshot,
        source_manifest=source_manifest,
    )


def _validate_fixed_source(
    result: DurableResult,
    fixture: WorkspaceTaskFixture,
    catalog: ActionCatalog,
) -> None:
    expected_catalog_fingerprint = GOLDEN_CATALOG_FINGERPRINTS[
        "workspace.fix-config"
    ]
    if (
        result.task_id != "workspace.fix-config"
        or result.identity.task_id != "workspace.fix-config"
        or result.identity.fixture_id != fixture.task.fixture_id
        or result.identity.fixture_fingerprint
        != _fingerprint(fixture.model_dump(mode="json"))
        or result.identity.failure_schedule_id != "workspace.lost-write-result.v1"
        or result.identity.interruption_schedule_id is not None
        or result.identity.interruption_schedule_fingerprint is not None
        or result.identity.checkpointing is not CheckpointingMode.OFF
        or result.runtime_config.mode is not RuntimeMode.SAFEGUARDED
        or result.runtime_config.schedule_id != "workspace.lost-write-result.v1"
        or result.resumed
        or result.checkpoint_id is not None
        or result.checkpoint_step is not None
        or result.terminal_state is not RuntimeState.SUCCEEDED
        or not result.verifier.passed
        or result.usage.physical_write_executions != 1
        or result.usage.side_effect_executions != 1
        or result.usage.duplicate_side_effects != 0
        or catalog.task_id != result.task_id
        or catalog_fingerprint(catalog) != expected_catalog_fingerprint
        or len(catalog.actions) != 2
    ):
        raise ActionReplayValidationError("source is outside the fixed M3B slice")


def _validate_action_records(
    result: DurableResult,
    catalog: ActionCatalog,
    records: list[ActionJournalRecord],
    events: list[DurableEvent],
) -> None:
    expected_kinds = [
        "source_started",
        "action_started",
        "action_finished",
        "action_started",
        "action_finished",
        "source_finished",
    ]
    catalog_digest = catalog_fingerprint(catalog)
    if (
        [record.event_kind for record in records] != expected_kinds
        or any(record.source_run_id != result.run_id for record in records)
        or any(record.task_id != result.task_id for record in records)
        or any(record.catalog_id != catalog.catalog_id for record in records)
        or any(record.catalog_fingerprint != catalog_digest for record in records)
    ):
        raise ActionReplayValidationError("action journal shape or identity changed")

    action_records = records[1:-1]
    for index, entry in enumerate(catalog.actions):
        started = action_records[index * 2]
        finished = action_records[index * 2 + 1]
        attempts = [
            event
            for event in events
            if getattr(event, "event_kind", None) == "attempt_started"
            and getattr(event, "step_index", None) == entry.step_index
            and getattr(getattr(event, "payload", None), "tool_name", None)
            == entry.tool_name
        ]
        if not _records_match_entry(started, finished, entry):
            raise ActionReplayValidationError("action ref does not resolve to its catalog")
        if finished.attempt_count != len(attempts):
            raise ActionReplayValidationError("action attempts do not bind to events")
    finished_records = [records[2], records[4]]
    if [record.attempt_count for record in finished_records] != [1, 2]:
        raise ActionReplayValidationError("source is not the fixed lost-result run")

    source_finished = records[-1]
    workspace_root = Path(result.workspace_dir)
    if (
        source_finished.action_count != len(catalog.actions)
        or source_finished.source_event_final_hash != result.journal_final_hash
        or source_finished.source_result_summary_digest
        != durable_result_summary_digest(result.summary())
        or source_finished.source_verifier_digest
        != verifier_summary_digest(result.verifier)
        or source_finished.source_final_workspace_digest is None
        or source_finished.source_final_workspace_digest
        != finished_records[-1].post_action_workspace_digest
        or workspace_root.is_absolute()
    ):
        raise ActionReplayValidationError("source_finished binding is invalid")


def _records_match_entry(
    started: ActionJournalRecord,
    finished: ActionJournalRecord,
    entry: CatalogAction,
) -> bool:
    expected = (
        entry.step_index,
        entry.action_ref,
        entry.tool_name,
        action_fingerprint(entry.action),
    )
    return (
        started.step_index,
        started.action_ref,
        started.tool_name,
        started.action_fingerprint,
    ) == expected and (
        finished.step_index,
        finished.action_ref,
        finished.tool_name,
        finished.action_fingerprint,
    ) == expected


def _validate_paths(source: Path, output: Path) -> tuple[Path, Path]:
    try:
        if source.is_symlink():
            raise ActionReplayValidationError("source cannot be a symbolic link")
        source_resolved = source.resolve(strict=True)
        output_resolved = output.resolve(strict=False)
    except OSError as exc:
        raise ActionReplayValidationError("source/output path could not be resolved") from exc
    if not source_resolved.is_dir():
        raise ActionReplayValidationError("source must be a run directory")
    if (
        source_resolved == output_resolved
        or source_resolved in output_resolved.parents
        or output_resolved in source_resolved.parents
    ):
        raise ActionReplayValidationError("source and output cannot overlap")
    if output.exists() and (
        output.is_symlink() or not output.is_dir() or any(output.iterdir())
    ):
        raise ActionReplayValidationError("output directory must be absent or empty")
    return source_resolved, output_resolved


def _read_workspace_snapshot(root: Path) -> WorkspaceSnapshot:
    files: dict[str, str] = {}
    resolved_root = root.resolve(strict=True)
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ActionReplayValidationError("source Workspace contains a symlink")
        if path.is_file():
            path.resolve(strict=True).relative_to(resolved_root)
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return WorkspaceSnapshot(files=files)


def _directory_manifest(root: Path) -> dict[str, tuple[str, int, str]]:
    manifest: dict[str, tuple[str, int, str]] = {}
    try:
        resolved_root = root.resolve(strict=True)
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ActionReplayValidationError("source contains a symbolic link")
            path.resolve(strict=True).relative_to(resolved_root)
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                manifest[relative] = ("directory", 0, "")
            elif path.is_file():
                content = path.read_bytes()
                manifest[relative] = (
                    "file",
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                )
    except (OSError, ValueError) as exc:
        raise ActionReplayValidationError("source manifest could not be computed") from exc
    return manifest


def _fingerprint(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_result_once(path: Path, result: ActionReplayResult) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(result.model_dump_json(indent=2))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
