"""Reconstruct validation-only teacher-forced prefixes from raw Eval evidence."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from agent_learning_loop.action_catalog import ActionCatalog, catalog_fingerprint
from agent_learning_loop.canonical import canonical_json_bytes, canonical_sha256
from agent_learning_loop.corpus import WorkspaceCorpusManifest
from agent_learning_loop.dataops_catalog import (
    DataOpsActionCatalog,
    dataops_catalog_fingerprint,
)
from agent_learning_loop.dataops_corpus import DataOpsCorpusManifest
from agent_learning_loop.dataops_schemas import (
    DataOpsAction,
    DataOpsTaskFixture,
    DataOpsToolResult,
)
from agent_learning_loop.eval_bundle import compute_bundle_fingerprint
from agent_learning_loop.eval_records import (
    _DataOpsProjection,
    _IncidentProjection,
    _project_workspace_step,
)
from agent_learning_loop.eval_schemas import (
    EvalBundleManifest,
    EvalSuiteManifest,
    SystemEvalCell,
)
from agent_learning_loop.eval_suites import CANONICAL_EVAL_SUITE_FINGERPRINTS
from agent_learning_loop.incident_catalog import (
    IncidentActionCatalog,
    incident_catalog_fingerprint,
)
from agent_learning_loop.incident_corpus import IncidentCorpusManifest
from agent_learning_loop.incident_schemas import (
    IncidentAction,
    IncidentTaskFixture,
    IncidentToolResult,
)
from agent_learning_loop.model_probe_schemas import (
    ProbeAction,
    ProbeEnvironment,
    ProbeMessage,
    ProbeTaskContext,
    ValidationPrefix,
)
from agent_learning_loop.model_probe_specs import load_probe_contract
from agent_learning_loop.model_probe_tools import build_tool_definitions
from agent_learning_loop.schemas import Action, ToolResult, WorkspaceTaskFixture
from agent_learning_loop.sft_normalizers import (
    normalize_incident_trajectory,
    normalize_workspace_trajectory,
)
from agent_learning_loop.sft_schemas import SftAssistantAction, SftToolResult, SftTurn


class ModelProbeProjectionError(ValueError):
    """Source Eval or packaged public evidence cannot produce trusted prefixes."""


def _probe_jsonl(path: Path) -> list[object]:
    try:
        raw = path.read_bytes()
        if not raw or b"\r" in raw or not raw.endswith(b"\n"):
            raise ModelProbeProjectionError("probe_events_encoding")
        return [json.loads(line) for line in raw.splitlines()]
    except ModelProbeProjectionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelProbeProjectionError("invalid_probe_events") from exc


def _normalize_dataops_probe_trajectory(
    events: Path,
    fixture: DataOpsTaskFixture,
    catalog: DataOpsActionCatalog,
) -> list[SftTurn]:
    """Normalize validation DataOps results, including a rejected insert before rollback."""
    payloads = _probe_jsonl(events)
    if len(payloads) != len(catalog.actions) * 2:
        raise ModelProbeProjectionError("dataops_probe_event_count")
    turns: list[SftTurn] = []
    try:
        for offset, entry in enumerate(catalog.actions):
            action = DataOpsAction.model_validate(payloads[offset * 2])
            result = DataOpsToolResult.model_validate(payloads[offset * 2 + 1])
            if action != entry.action or action.tool_name not in fixture.task.allowed_tools:
                raise ModelProbeProjectionError("dataops_probe_catalog_binding")
            if (result.status == "ok") != (result.error_category is None):
                raise ModelProbeProjectionError("dataops_probe_result_category")
            if result.status != "ok" and result.payload:
                raise ModelProbeProjectionError("dataops_probe_rejection_payload")
            turns.extend(
                [
                    SftAssistantAction(
                        tool_name=action.tool_name, arguments=action.arguments
                    ),
                    SftToolResult.model_validate(
                        {
                            "tool_name": action.tool_name,
                            "status": result.status,
                            "payload": result.payload,
                            "error_category": result.error_category,
                            "idempotency_hit": result.idempotency_hit,
                        }
                    ),
                ]
            )
    except ModelProbeProjectionError:
        raise
    except (ValidationError, ValueError, TypeError) as exc:
        raise ModelProbeProjectionError("invalid_dataops_probe_trajectory") from exc
    return turns


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resource_json(relative: str) -> tuple[object, str]:
    resource = files("agent_learning_loop").joinpath(*relative.split("/"))
    try:
        text = resource.read_text(encoding="utf-8")
        return json.loads(text), text
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelProbeProjectionError("invalid_public_probe_resource") from exc


def _validation_cells() -> dict[str, SystemEvalCell]:
    payload, text = _resource_json("eval_suites/suites-v1.json")
    try:
        suites = TypeAdapter(list[EvalSuiteManifest]).validate_json(text)
    except ValidationError as exc:
        raise ModelProbeProjectionError("invalid_eval_suite_resource") from exc
    suite = next(item for item in suites if item.suite_id == "system-correctness-v1")
    suite_payload = suite.model_dump(mode="json", exclude={"manifest_fingerprint"})
    if (
        suite.manifest_fingerprint != canonical_sha256(suite_payload)
        or suite.manifest_fingerprint
        != CANONICAL_EVAL_SUITE_FINGERPRINTS["system-correctness-v1"]
    ):
        raise ModelProbeProjectionError("eval_suite_identity")
    contract = load_probe_contract()
    registered = {item.cell_id: item for item in contract.validation_tasks}
    selected = {
        cell.cell_id: cell
        for cell in suite.cells
        if isinstance(cell, SystemEvalCell) and cell.cell_id in registered
    }
    if set(selected) != set(registered) or any(
        cell.task_id != registered[cell_id].task_id
        or cell.environment != registered[cell_id].environment
        or cell.split != "validation"
        for cell_id, cell in selected.items()
    ):
        raise ModelProbeProjectionError("validation_cell_identity")
    return selected


def _validate_source_eval(root: Path) -> EvalBundleManifest:
    try:
        if root.is_symlink() or not root.is_dir():
            raise ModelProbeProjectionError("invalid_source_eval_directory")
        manifest = EvalBundleManifest.model_validate_json(
            (root / "eval-manifest.json").read_text(encoding="utf-8")
        )
    except ModelProbeProjectionError:
        raise
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ModelProbeProjectionError("invalid_source_eval_manifest") from exc
    contract = load_probe_contract()
    if manifest.source_commit != contract.public_source_commit:
        raise ModelProbeProjectionError("source_eval_public_commit")
    expected_cells = [item.cell_id for item in contract.validation_tasks]
    selection = manifest.selection
    if (
        selection.suite != "system-correctness"
        or selection.split != "validation"
        or selection.environment is not None
        or selection.tag is not None
        or selection.pair is not None
        or selection.candidate_total != 30
        or selection.selected_total != 6
        or selection.cell_ids != expected_cells
        or manifest.suite_fingerprints
        != {
            "system-correctness-v1": CANONICAL_EVAL_SUITE_FINGERPRINTS[
                "system-correctness-v1"
            ]
        }
        or manifest.bundle_fingerprint != compute_bundle_fingerprint(manifest)
    ):
        raise ModelProbeProjectionError("source_eval_validation_identity")
    artifact_paths = [artifact.path for artifact in manifest.artifacts]
    if len(artifact_paths) != len(set(artifact_paths)):
        raise ModelProbeProjectionError("source_eval_duplicate_artifact")
    expected_files = {
        "eval-manifest.json",
        "records.jsonl",
        "summary.json",
        "report.md",
        *artifact_paths,
    }
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_files != expected_files:
        raise ModelProbeProjectionError("source_eval_artifact_inventory")
    for artifact in manifest.artifacts:
        path = root / artifact.path
        if path.is_symlink() or _sha256(path) != artifact.sha256:
            raise ModelProbeProjectionError("source_eval_artifact_hash")
    return manifest


def _load_task_resources(
    environment: ProbeEnvironment,
    task_id: str,
) -> tuple[
    WorkspaceCorpusManifest | IncidentCorpusManifest | DataOpsCorpusManifest,
    WorkspaceTaskFixture | IncidentTaskFixture | DataOpsTaskFixture,
    ActionCatalog | IncidentActionCatalog | DataOpsActionCatalog,
    object,
]:
    leaf = task_id.split(".", 1)[1]
    manifest_payload, manifest_text = _resource_json(
        f"task_manifests/{environment}/{leaf}.json"
    )
    fixture_payload, fixture_text = _resource_json(
        f"task_fixtures/{environment}/{leaf}.json"
    )
    catalog_path = (
        f"action_catalogs/{leaf}.json"
        if environment == "workspace"
        else f"action_catalogs/{environment}/{leaf}.json"
    )
    catalog_payload, catalog_text = _resource_json(catalog_path)
    manifest: WorkspaceCorpusManifest | IncidentCorpusManifest | DataOpsCorpusManifest
    fixture: WorkspaceTaskFixture | IncidentTaskFixture | DataOpsTaskFixture
    catalog: ActionCatalog | IncidentActionCatalog | DataOpsActionCatalog
    try:
        if environment == "workspace":
            workspace_manifest = WorkspaceCorpusManifest.model_validate_json(manifest_text)
            workspace_fixture = WorkspaceTaskFixture.model_validate_json(fixture_text)
            workspace_catalog = ActionCatalog.model_validate_json(catalog_text)
            manifest = workspace_manifest
            fixture = workspace_fixture
            catalog = workspace_catalog
            fixture_fingerprint = canonical_sha256(
                workspace_fixture.model_dump(mode="json")
            )
            action_fingerprint = catalog_fingerprint(workspace_catalog)
        elif environment == "incident":
            incident_manifest = IncidentCorpusManifest.model_validate_json(manifest_text)
            incident_fixture = IncidentTaskFixture.model_validate_json(fixture_text)
            incident_catalog = IncidentActionCatalog.model_validate_json(catalog_text)
            manifest = incident_manifest
            fixture = incident_fixture
            catalog = incident_catalog
            fixture_fingerprint = canonical_sha256(fixture_payload)
            action_fingerprint = incident_catalog_fingerprint(incident_catalog)
        else:
            dataops_manifest = DataOpsCorpusManifest.model_validate_json(manifest_text)
            dataops_fixture = DataOpsTaskFixture.model_validate_json(fixture_text)
            dataops_catalog = DataOpsActionCatalog.model_validate_json(catalog_text)
            manifest = dataops_manifest
            fixture = dataops_fixture
            catalog = dataops_catalog
            fixture_fingerprint = canonical_sha256(fixture_payload)
            action_fingerprint = dataops_catalog_fingerprint(dataops_catalog)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ModelProbeProjectionError("invalid_validation_public_resource") from exc
    if (
        manifest.task_id != task_id
        or manifest.environment_kind != environment
        or manifest.split != "validation"
        or fixture.task.task_id != task_id
        or catalog.task_id != task_id
        or manifest.fixture_id != fixture.task.fixture_id
        or manifest.fixture_fingerprint != fixture_fingerprint
        or manifest.catalog_id != catalog.catalog_id
        or manifest.catalog_fingerprint != action_fingerprint
    ):
        raise ModelProbeProjectionError("validation_public_resource_identity")
    return manifest, fixture, catalog, manifest_payload


def _context_and_turns(
    root: Path,
    cell: SystemEvalCell,
) -> tuple[ProbeTaskContext, list[SftTurn]]:
    manifest, fixture, catalog, _ = _load_task_resources(cell.environment, cell.task_id)
    if (
        cell.resource_id != manifest.manifest_id
        or cell.resource_fingerprint
        != canonical_sha256(manifest.model_dump(mode="json"))
        or cell.fixture_id != manifest.fixture_id
        or cell.fixture_fingerprint != manifest.fixture_fingerprint
        or cell.catalog_id != manifest.catalog_id
        or cell.catalog_fingerprint != manifest.catalog_fingerprint
        or cell.seed != manifest.seed
        or cell.tags != manifest.tags
    ):
        raise ModelProbeProjectionError("validation_cell_resource_identity")
    events = root / "runs" / "system-correctness-v1" / cell.cell_id / "events.jsonl"
    if cell.environment == "workspace":
        workspace_fixture = cast(WorkspaceTaskFixture, fixture)
        workspace_catalog = cast(ActionCatalog, catalog)
        context = ProbeTaskContext(
            instruction=workspace_fixture.task.instruction,
            allowed_tools=list(workspace_fixture.task.allowed_tools),
            constraints=list(manifest.safety_constraints),
        )
        turns = normalize_workspace_trajectory(
            events, workspace_fixture.task, workspace_catalog
        )
        files_state = dict(workspace_fixture.private.setup.files)
        for offset in range(0, len(turns), 2):
            action_turn = cast(SftAssistantAction, turns[offset])
            result_turn = cast(SftToolResult, turns[offset + 1])
            workspace_action = Action.model_validate(
                {
                    "tool_name": action_turn.tool_name,
                    "arguments": action_turn.arguments,
                }
            )
            workspace_actual = ToolResult.model_validate(
                {"status": result_turn.status, "payload": result_turn.payload}
            )
            if workspace_actual != _project_workspace_step(
                files_state, workspace_action
            ):
                raise ModelProbeProjectionError("workspace_probe_result_projection")
    elif cell.environment == "incident":
        incident_fixture = cast(IncidentTaskFixture, fixture)
        incident_catalog = cast(IncidentActionCatalog, catalog)
        context = ProbeTaskContext(
            instruction=incident_fixture.task.instruction,
            allowed_tools=list(incident_fixture.task.allowed_tools),
            constraints=list(manifest.safety_constraints),
        )
        turns = normalize_incident_trajectory(
            events, incident_fixture.task, incident_catalog
        )
        incident_projection = _IncidentProjection(incident_fixture, run_id="model-probe")
        for offset in range(0, len(turns), 2):
            action_turn = cast(SftAssistantAction, turns[offset])
            result_turn = cast(SftToolResult, turns[offset + 1])
            incident_action = IncidentAction.model_validate(
                {
                    "tool_name": action_turn.tool_name,
                    "arguments": action_turn.arguments,
                }
            )
            incident_actual = IncidentToolResult.model_validate(
                {
                    "status": result_turn.status,
                    "payload": result_turn.payload,
                    "error_category": result_turn.error_category,
                    "idempotency_hit": result_turn.idempotency_hit,
                }
            )
            incident_projected, _ = incident_projection.step(incident_action)
            if incident_actual != incident_projected:
                raise ModelProbeProjectionError("incident_probe_result_projection")
    else:
        dataops_fixture = cast(DataOpsTaskFixture, fixture)
        dataops_catalog = cast(DataOpsActionCatalog, catalog)
        context = ProbeTaskContext(
            instruction=dataops_fixture.task.instruction,
            allowed_tools=list(dataops_fixture.task.allowed_tools),
            public_scope=[
                cast(dict[str, JsonValue], item.model_dump(mode="json"))
                for item in dataops_fixture.task.scope
            ],
            constraints=list(
                dict.fromkeys(
                    [
                        *manifest.safety_constraints,
                        *dataops_fixture.task.public_constraints,
                    ]
                )
            ),
        )
        turns = _normalize_dataops_probe_trajectory(
            events, dataops_fixture, dataops_catalog
        )
        dataops_projection = _DataOpsProjection(dataops_fixture, run_id="model-probe")
        for offset in range(0, len(turns), 2):
            action_turn = cast(SftAssistantAction, turns[offset])
            result_turn = cast(SftToolResult, turns[offset + 1])
            dataops_action = DataOpsAction.model_validate(
                {
                    "tool_name": action_turn.tool_name,
                    "arguments": action_turn.arguments,
                }
            )
            dataops_actual = DataOpsToolResult.model_validate(
                {
                    "status": result_turn.status,
                    "payload": result_turn.payload,
                    "error_category": result_turn.error_category,
                    "idempotency_hit": result_turn.idempotency_hit,
                }
            )
            dataops_projected, _ = dataops_projection.step(dataops_action)
            if dataops_actual != dataops_projected:
                raise ModelProbeProjectionError("dataops_probe_result_projection")
    return context, turns


def _tool_call(action: SftAssistantAction) -> str:
    payload = canonical_json_bytes(
        {"name": action.tool_name, "arguments": action.arguments}
    ).decode("utf-8")
    return f"<tool_call>{payload}</tool_call>"


def _tool_result(result: SftToolResult) -> str:
    return canonical_json_bytes(
        {
            "name": result.tool_name,
            "status": result.status,
            "payload": result.payload,
            "error_category": result.error_category,
            "idempotency_hit": result.idempotency_hit,
        }
    ).decode("utf-8")


def build_validation_prefixes(
    source_eval: Path,
    *,
    selected_task_ids: list[str] | None = None,
) -> tuple[ValidationPrefix, ...]:
    """Build independent correct-history prefixes without reading test task resources."""
    _validate_source_eval(source_eval)
    contract = load_probe_contract()
    registered_ids = [item.task_id for item in contract.validation_tasks]
    if selected_task_ids is None:
        selected_task_ids = registered_ids
    if (
        not selected_task_ids
        or len(selected_task_ids) != len(set(selected_task_ids))
        or not set(selected_task_ids) <= set(registered_ids)
    ):
        raise ModelProbeProjectionError("invalid_probe_task_selection")
    cells = _validation_cells()
    by_task = {cell.task_id: cell for cell in cells.values()}
    prefixes: list[ValidationPrefix] = []
    for task_id in selected_task_ids:
        cell = by_task[task_id]
        context, turns = _context_and_turns(source_eval, cell)
        if len(turns) % 2 != 0:
            raise ModelProbeProjectionError("validation_turn_pairing")
        tools = build_tool_definitions(cell.environment, context.allowed_tools)
        history: list[ProbeMessage] = [
            ProbeMessage(
                role="system",
                content=(
                    "Choose the next action for this synthetic task. Return exactly one "
                    "<tool_call> JSON object and no other text."
                ),
            ),
            ProbeMessage(
                role="user",
                content=canonical_json_bytes(
                    {
                        "instruction": context.instruction,
                        "allowed_tools": context.allowed_tools,
                        "public_scope": context.public_scope,
                        "constraints": context.constraints,
                    }
                ).decode("utf-8"),
            ),
        ]
        for offset in range(0, len(turns), 2):
            action = turns[offset]
            result = turns[offset + 1]
            if not isinstance(action, SftAssistantAction) or not isinstance(
                result, SftToolResult
            ):
                raise ModelProbeProjectionError("validation_turn_order")
            reference = ProbeAction(
                tool_name=action.tool_name, arguments=action.arguments
            )
            step_index = offset // 2 + 1
            prompt_payload = {
                "task_id": task_id,
                "environment": cell.environment,
                "step_index": step_index,
                "task": context.model_dump(mode="json"),
                "messages": [item.model_dump(mode="json") for item in history],
                "tools": [item.model_dump(mode="json") for item in tools],
            }
            prefixes.append(
                ValidationPrefix(
                    prefix_id=f"probe.{task_id}.step-{step_index}",
                    task_id=task_id,
                    environment=cell.environment,
                    step_index=step_index,
                    task=context,
                    messages=list(history),
                    tools=tools,
                    reference_action=reference,
                    prompt_fingerprint=canonical_sha256(prompt_payload),
                    reference_fingerprint=canonical_sha256(
                        reference.model_dump(mode="json")
                    ),
                )
            )
            history.extend(
                [
                    ProbeMessage(role="assistant", content=_tool_call(action)),
                    ProbeMessage(role="tool", content=_tool_result(result)),
                ]
            )
    return tuple(prefixes)


def source_eval_manifest(source_eval: Path) -> EvalBundleManifest:
    """Expose the already validated source identity to the runner and validator."""
    return _validate_source_eval(source_eval)
