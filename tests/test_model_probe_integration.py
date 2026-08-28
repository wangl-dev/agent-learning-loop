from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_learning_loop.canonical import canonical_json_bytes, canonical_sha256
from agent_learning_loop.cli import main
from agent_learning_loop.eval_bundle import compute_bundle_fingerprint
from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.model_probe_projection import (
    ModelProbeProjectionError,
    build_validation_prefixes,
)
from agent_learning_loop.model_probe_runner import ModelProbeRunError, run_model_probe
from agent_learning_loop.model_probe_schemas import (
    CapacityEvidence,
    LocalModelSpec,
    ModelBackendInput,
    ModelGeneration,
    ModelRuntimeMetrics,
    ProbeMessage,
    ValidationPrefix,
)
from agent_learning_loop.model_probe_specs import load_probe_contract, select_model_spec
from agent_learning_loop.model_probe_validator import (
    ModelProbeValidationError,
    validate_model_probe,
)
from agent_learning_loop.sft_schemas import scan_sft_sensitive_text

PUBLIC_SOURCE = "65d6c441f4e2be1e2dce3e363bc87f593aab221a"
VALIDATION_TASKS = {
    "workspace.normalize-checklist",
    "workspace.update-status",
    "incident.isolate-inventory-config-change",
    "incident.recover-auth-dependency-chain",
    "dataops.atomic-parent-child-migration",
    "dataops.rollback-unique-key-conflict",
}
TEST_TASK_MARKERS = {
    "workspace.reconcile-inventory",
    "workspace.update-route",
    "incident.reject-premature-checkout-ack",
    "incident.rollback-checkout-canary",
    "dataops.detect-stale-version-precondition",
    "dataops.reject-transactionless-update",
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source_eval(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("m7ca-source") / "eval"
    outcome = run_eval(
        "system-correctness",
        PUBLIC_SOURCE,
        root,
        split="validation",
    )
    assert outcome.exit_code == 0
    return root


def _inventory(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _qwen_generation(
    spec: LocalModelSpec,
    *,
    impossible_runtime: bool = False,
) -> ModelGeneration:
    return ModelGeneration(
        raw_generation="plain answer",
        input_tokens=0 if impossible_runtime else 128,
        output_tokens=0 if impossible_runtime else 2,
        finish_reason="capacity_blocked" if impossible_runtime else "stop",
        formatted_prompt_sha256="1" * 64,
        chat_template_sha256=spec.chat_template_sha256,
        metrics=ModelRuntimeMetrics(
            torch_version="not-installed" if impossible_runtime else "2.7.1+cu126",
            transformers_version="not-installed" if impossible_runtime else "4.53.3",
            cuda_runtime="not-used" if impossible_runtime else "12.6",
            driver_version="not-used" if impossible_runtime else "566.26",
            gpu_name="fake-cpu" if impossible_runtime else "NVIDIA test CUDA GPU",
            wall_time_ms=0 if impossible_runtime else 1,
            peak_allocated_bytes=0 if impossible_runtime else 1_000,
            peak_reserved_bytes=0 if impossible_runtime else 2_000,
            free_vram_bytes=0 if impossible_runtime else 4_000,
            total_vram_bytes=0 if impossible_runtime else 8_000,
        ),
    )


def test_foreign_source_joint_resign_is_rejected(
    source_eval: Path, tmp_path: Path
) -> None:
    honest_bundle = tmp_path / "honest"
    run_model_probe(source_eval, honest_bundle, backend_kind="fake", seed=17)
    foreign_source = tmp_path / "foreign-source"
    shutil.copytree(source_eval, foreign_source)
    source_manifest_path = foreign_source / "eval-manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["source_commit"] = "0" * 40
    source_manifest["bundle_fingerprint"] = compute_bundle_fingerprint(source_manifest)
    source_manifest_path.write_text(
        json.dumps(source_manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    forged_bundle = tmp_path / "foreign-bundle"
    shutil.copytree(honest_bundle, forged_bundle)
    probe_manifest_path = forged_bundle / "probe-manifest.json"
    probe_manifest = json.loads(probe_manifest_path.read_text(encoding="utf-8"))
    old_source_fingerprint = probe_manifest["source_eval_fingerprint"]
    probe_manifest["source_commit"] = "0" * 40
    probe_manifest["source_eval_fingerprint"] = source_manifest["bundle_fingerprint"]
    report_path = forged_bundle / "report.md"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace(
            old_source_fingerprint,
            source_manifest["bundle_fingerprint"],
        ),
        encoding="utf-8",
        newline="\n",
    )
    for artifact in probe_manifest["artifacts"]:
        if artifact["path"] == "report.md":
            artifact["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    probe_manifest["bundle_fingerprint"] = canonical_sha256(
        {
            key: value
            for key, value in probe_manifest.items()
            if key != "bundle_fingerprint"
        }
    )
    probe_manifest_path.write_text(
        json.dumps(probe_manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ModelProbeValidationError, match="source_eval_public_commit"):
        validate_model_probe(forged_bundle, foreign_source)


def test_projection_and_runner_reject_foreign_source_at_entry(
    tmp_path: Path,
) -> None:
    foreign_source = tmp_path / "foreign-source"
    outcome = run_eval(
        "system-correctness",
        "0" * 40,
        foreign_source,
        split="validation",
    )
    assert outcome.exit_code == 0

    with pytest.raises(ModelProbeProjectionError, match="source_eval_public_commit"):
        build_validation_prefixes(foreign_source)
    output = tmp_path / "foreign-output"
    with pytest.raises(ModelProbeProjectionError, match="source_eval_public_commit"):
        run_model_probe(foreign_source, output, backend_kind="fake", seed=17)
    assert not output.exists()


def test_reference_action_content_leak_is_rejected_after_prompt_resign(
    source_eval: Path,
) -> None:
    prefix = build_validation_prefixes(source_eval)[0]
    leaked_contents = (
        canonical_json_bytes(prefix.reference_action.model_dump(mode="json")).decode(
            "utf-8"
        ),
        canonical_json_bytes(
            {
                "name": prefix.reference_action.tool_name,
                "arguments": prefix.reference_action.arguments,
            }
        ).decode("utf-8"),
    )
    for leaked_content in leaked_contents:
        leaked_messages = [
            *prefix.messages,
            ProbeMessage(role="user", content=leaked_content),
        ]
        payload = prefix.model_dump(mode="json")
        payload["messages"] = [
            message.model_dump(mode="json") for message in leaked_messages
        ]
        payload["prompt_fingerprint"] = canonical_sha256(
            {
                "task_id": prefix.task_id,
                "environment": prefix.environment,
                "step_index": prefix.step_index,
                "task": prefix.task.model_dump(mode="json"),
                "messages": payload["messages"],
                "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
            }
        )

        with pytest.raises(ValidationError, match="reference_action_in_prompt"):
            ValidationPrefix.model_validate(payload)


def test_pretty_and_reordered_reference_json_leaks_are_rejected_after_resign(
    source_eval: Path,
) -> None:
    prefix = build_validation_prefixes(source_eval)[0]
    leaked_contents = (
        json.dumps(
            prefix.reference_action.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
        ),
        "<tool_call>\n"
        + json.dumps(
            {
                "arguments": prefix.reference_action.arguments,
                "name": prefix.reference_action.tool_name,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n</tool_call>",
    )
    for leaked_content in leaked_contents:
        payload = prefix.model_dump(mode="json")
        payload["messages"] = [
            *[message.model_dump(mode="json") for message in prefix.messages],
            ProbeMessage(
                role="user", content=f"Do not repeat this value:\n{leaked_content}"
            ).model_dump(mode="json"),
        ]
        payload["prompt_fingerprint"] = canonical_sha256(
            {
                "task_id": prefix.task_id,
                "environment": prefix.environment,
                "step_index": prefix.step_index,
                "task": prefix.task.model_dump(mode="json"),
                "messages": payload["messages"],
                "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
            }
        )

        with pytest.raises(ValidationError, match="reference_action_in_prompt"):
            ValidationPrefix.model_validate(payload)


def test_json_string_encoded_reference_leaks_are_rejected_after_resign(
    source_eval: Path,
) -> None:
    prefix = build_validation_prefixes(source_eval)[0]
    reference_json = json.dumps(
        {
            "name": prefix.reference_action.tool_name,
            "arguments": prefix.reference_action.arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    for leaked_content in (json.dumps(reference_json), json.dumps(json.dumps(reference_json))):
        payload = prefix.model_dump(mode="json")
        payload["messages"] = [
            *[message.model_dump(mode="json") for message in prefix.messages],
            ProbeMessage(role="user", content=leaked_content).model_dump(mode="json"),
        ]
        payload["prompt_fingerprint"] = canonical_sha256(
            {
                "task_id": prefix.task_id,
                "environment": prefix.environment,
                "step_index": prefix.step_index,
                "task": prefix.task.model_dump(mode="json"),
                "messages": payload["messages"],
                "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
            }
        )

        with pytest.raises(ValidationError, match="reference_action_in_prompt"):
            ValidationPrefix.model_validate(payload)

    for allowed_content in (
        "ordinary instruction text",
        json.dumps("input/title.txt"),
        json.dumps(json.dumps({"kind": "unrelated"})),
    ):
        payload = prefix.model_dump(mode="json")
        payload["messages"] = [
            *[message.model_dump(mode="json") for message in prefix.messages],
            ProbeMessage(role="user", content=allowed_content).model_dump(mode="json"),
        ]
        payload["prompt_fingerprint"] = canonical_sha256(
            {
                "task_id": prefix.task_id,
                "environment": prefix.environment,
                "step_index": prefix.step_index,
                "task": prefix.task.model_dump(mode="json"),
                "messages": payload["messages"],
                "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
            }
        )
        ValidationPrefix.model_validate(payload)

    for bounded_content, error in (
        (json.dumps(json.dumps(json.dumps(reference_json))), "reference_json_depth_limit"),
        ("x" * 65_537, "reference_json_scan_limit"),
    ):
        payload = prefix.model_dump(mode="json")
        payload["messages"] = [
            *[message.model_dump(mode="json") for message in prefix.messages],
            ProbeMessage(role="user", content=bounded_content).model_dump(mode="json"),
        ]
        payload["prompt_fingerprint"] = canonical_sha256(
            {
                "task_id": prefix.task_id,
                "environment": prefix.environment,
                "step_index": prefix.step_index,
                "task": prefix.task.model_dump(mode="json"),
                "messages": payload["messages"],
                "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
            }
        )
        with pytest.raises(ValidationError, match=error):
            ValidationPrefix.model_validate(payload)


@pytest.mark.parametrize("encoding_depth", [1, 2])
def test_mixed_prose_json_string_reference_leaks_are_rejected_after_resign(
    source_eval: Path,
    encoding_depth: int,
) -> None:
    prefix = build_validation_prefixes(source_eval)[0]
    leaked_content = json.dumps(
        {
            "name": prefix.reference_action.tool_name,
            "arguments": prefix.reference_action.arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    for _ in range(encoding_depth):
        leaked_content = json.dumps(leaked_content)
    payload = prefix.model_dump(mode="json")
    payload["messages"] = [
        *[message.model_dump(mode="json") for message in prefix.messages],
        ProbeMessage(
            role="user", content=f"Reference: {leaked_content}"
        ).model_dump(mode="json"),
    ]
    payload["prompt_fingerprint"] = canonical_sha256(
        {
            "task_id": prefix.task_id,
            "environment": prefix.environment,
            "step_index": prefix.step_index,
            "task": prefix.task.model_dump(mode="json"),
            "messages": payload["messages"],
            "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
        }
    )

    with pytest.raises(ValidationError, match="reference_action_in_prompt"):
        ValidationPrefix.model_validate(payload)


def test_mixed_prose_json_scan_allows_benign_quoted_and_relative_content(
    source_eval: Path,
) -> None:
    prefix = build_validation_prefixes(source_eval)[0]
    allowed_contents = (
        'The operator said "continue with the public input".',
        f"Reference: {json.dumps('input/title.txt')}",
        f"Reference: {json.dumps({'kind': 'unrelated'})}",
        f"Reference: {json.dumps(json.dumps({'kind': 'unrelated'}))}",
    )
    for allowed_content in allowed_contents:
        payload = prefix.model_dump(mode="json")
        payload["messages"] = [
            *[message.model_dump(mode="json") for message in prefix.messages],
            ProbeMessage(role="user", content=allowed_content).model_dump(mode="json"),
        ]
        payload["prompt_fingerprint"] = canonical_sha256(
            {
                "task_id": prefix.task_id,
                "environment": prefix.environment,
                "step_index": prefix.step_index,
                "task": prefix.task.model_dump(mode="json"),
                "messages": payload["messages"],
                "tools": [tool.model_dump(mode="json") for tool in prefix.tools],
            }
        )
        ValidationPrefix.model_validate(payload)


def test_projection_uses_exact_six_validation_tasks_and_public_prefixes_only(
    source_eval: Path,
) -> None:
    prefixes = build_validation_prefixes(source_eval)

    assert {prefix.task_id for prefix in prefixes} == VALIDATION_TASKS
    assert Counter(prefix.environment for prefix in prefixes) == {
        "workspace": 5,
        "incident": 8,
        "dataops": 8,
    }
    assert len(prefixes) == 21
    serialized = json.dumps(
        [prefix.model_dump(mode="json") for prefix in prefixes],
        sort_keys=True,
    )
    assert not TEST_TASK_MARKERS & set(serialized.split('"'))
    for prohibited in (
        '"private"',
        '"expected"',
        '"verifier"',
        '"audit"',
        '"run_id"',
        '"checks"',
        '"detail"',
    ):
        assert prohibited not in serialized
    assert scan_sft_sensitive_text(serialized) == ()
    for prefix in prefixes:
        reference_action_json = canonical_json_bytes(
            prefix.reference_action.model_dump(mode="json")
        ).decode("utf-8")
        reference_tool_json = canonical_json_bytes(
            {
                "name": prefix.reference_action.tool_name,
                "arguments": prefix.reference_action.arguments,
            }
        ).decode("utf-8")
        assert all(
            reference_action_json not in message.content
            and reference_tool_json not in message.content
            for message in prefix.messages
        )


def test_ci_model_probe_source_is_derived_from_packaged_contract() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    step_marker = "      - name: Run deterministic M7C-A validation next-action probe"
    step_start = workflow.index(step_marker)
    step_end = workflow.index("\n      - name:", step_start + len(step_marker))
    step = workflow[step_start:step_end]
    other_steps = workflow[:step_start] + workflow[step_end:]

    assert load_probe_contract().public_source_commit == PUBLIC_SOURCE
    assert "load_probe_contract().public_source_commit" in step
    assert '--source-commit "$source_commit"' in step
    assert "$GITHUB_SHA" not in step
    assert "$GITHUB_SHA" in other_steps


def test_fake_probe_is_deterministic_and_read_only_validates(
    source_eval: Path, tmp_path: Path
) -> None:
    source_before = _inventory(source_eval)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_outcome = run_model_probe(source_eval, first, backend_kind="fake", seed=17)
    run_model_probe(source_eval, second, backend_kind="fake", seed=17)
    validation = validate_model_probe(first, source_eval)

    assert first_outcome.status == "completed"
    assert validation.valid is True
    assert validation.task_total == 6
    assert validation.prefix_total == 21
    assert validation.exact_match_prefixes == 21
    assert validation.all_prefix_exact_tasks == 6
    assert validation.execution_calls == 0
    assert _inventory(first) == _inventory(second)
    assert _inventory(source_eval) == source_before


def test_validator_rejects_identity_and_derived_evidence_tampering(
    source_eval: Path, tmp_path: Path
) -> None:
    bundle = tmp_path / "tampered"
    run_model_probe(source_eval, bundle, backend_kind="fake", seed=17)
    manifest_path = bundle / "probe-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["model_revision"] = "0" * 40
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ModelProbeValidationError, match="model_spec_identity"):
        validate_model_probe(bundle, source_eval)


def test_fake_raw_generation_joint_resign_is_rejected(
    source_eval: Path, tmp_path: Path
) -> None:
    bundle = tmp_path / "joint-resign"
    run_model_probe(source_eval, bundle, backend_kind="fake", seed=17)

    raw_path = bundle / "raw-generations.jsonl"
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["generation"]["raw_generation"] = (
        '<tool_call>{"name":"read_text","arguments":{"path":"forged.txt"}}</tool_call>'
    )
    lines[0] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    # Even if an attacker refreshes the raw artifact hash, the fixed fake backend
    # transcript remains an external expectation for this CI-only backend.
    manifest_path = bundle / "probe-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    from hashlib import sha256

    for artifact in manifest["artifacts"]:
        if artifact["path"] == "raw-generations.jsonl":
            artifact["sha256"] = sha256(raw_path.read_bytes()).hexdigest()
    manifest["bundle_fingerprint"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ModelProbeValidationError, match="fake_generation_identity"):
        validate_model_probe(bundle, source_eval)


def test_projection_rejects_private_or_reference_injection_after_source_resign(
    source_eval: Path, tmp_path: Path
) -> None:
    forged = tmp_path / "forged-source"
    shutil.copytree(source_eval, forged)
    event_path = (
        forged
        / "runs/system-correctness-v1/system.workspace.normalize-checklist/events.jsonl"
    )
    lines = event_path.read_text(encoding="utf-8").splitlines()
    started = json.loads(lines[0])
    started["payload"]["private"] = {"expected": "forged reference"}
    lines[0] = json.dumps(started, sort_keys=True, separators=(",", ":"))
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    from hashlib import sha256

    from agent_learning_loop.eval_bundle import compute_bundle_fingerprint
    from agent_learning_loop.eval_schemas import EvalBundleManifest

    manifest_path = forged / "eval-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = event_path.relative_to(forged).as_posix()
    for artifact in manifest["artifacts"]:
        if artifact["path"] == relative:
            artifact["sha256"] = sha256(event_path.read_bytes()).hexdigest()
    manifest["bundle_fingerprint"] = compute_bundle_fingerprint(manifest)
    validated = EvalBundleManifest.model_validate(manifest)
    manifest_path.write_text(
        json.dumps(validated.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="workspace_started_context"):
        build_validation_prefixes(forged)


def test_projection_rejects_semantic_tool_result_tamper_after_source_resign(
    source_eval: Path, tmp_path: Path
) -> None:
    forged = tmp_path / "forged-result-source"
    shutil.copytree(source_eval, forged)
    event_path = (
        forged
        / "runs/system-correctness-v1/system.workspace.normalize-checklist/events.jsonl"
    )
    lines = event_path.read_text(encoding="utf-8").splitlines()
    completed = json.loads(lines[2])
    completed["payload"]["payload"]["content"] = "forged observation\n"
    lines[2] = json.dumps(completed, sort_keys=True, separators=(",", ":"))
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    from hashlib import sha256

    from agent_learning_loop.eval_bundle import compute_bundle_fingerprint

    manifest_path = forged / "eval-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = event_path.relative_to(forged).as_posix()
    for artifact in manifest["artifacts"]:
        if artifact["path"] == relative:
            artifact["sha256"] = sha256(event_path.read_bytes()).hexdigest()
    manifest["bundle_fingerprint"] = compute_bundle_fingerprint(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="workspace_probe_result_projection"):
        build_validation_prefixes(forged)


@pytest.mark.parametrize(
    ("cell_id", "line_index", "payload_key", "replacement", "category"),
    [
        (
            "system.incident.isolate-inventory-config-change",
            1,
            "approval_id",
            "forged-approval",
            "incident_probe_result_projection",
        ),
        (
            "system.dataops.atomic-parent-child-migration",
            3,
            "matched_row_count",
            2,
            "dataops_probe_result_projection",
        ),
    ],
)
def test_projection_rejects_incident_and_dataops_result_tamper(
    source_eval: Path,
    tmp_path: Path,
    cell_id: str,
    line_index: int,
    payload_key: str,
    replacement: object,
    category: str,
) -> None:
    forged = tmp_path / cell_id
    shutil.copytree(source_eval, forged)
    event_path = forged / f"runs/system-correctness-v1/{cell_id}/events.jsonl"
    lines = event_path.read_text(encoding="utf-8").splitlines()
    tool_result = json.loads(lines[line_index])
    tool_result["payload"][payload_key] = replacement
    lines[line_index] = json.dumps(tool_result, sort_keys=True, separators=(",", ":"))
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    from hashlib import sha256

    from agent_learning_loop.eval_bundle import compute_bundle_fingerprint

    manifest_path = forged / "eval-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = event_path.relative_to(forged).as_posix()
    for artifact in manifest["artifacts"]:
        if artifact["path"] == relative:
            artifact["sha256"] = sha256(event_path.read_bytes()).hexdigest()
    manifest["bundle_fingerprint"] = compute_bundle_fingerprint(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match=category):
        build_validation_prefixes(forged)


def test_projection_never_opens_test_task_resources(
    source_eval: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_learning_loop.model_probe_projection as projection

    original = projection._resource_json
    opened: list[str] = []

    def guarded(relative: str) -> tuple[object, str]:
        opened.append(relative)
        if any(marker.split(".", 1)[1] in relative for marker in TEST_TASK_MARKERS):
            raise AssertionError("test_task_resource_read")
        return original(relative)

    monkeypatch.setattr(projection, "_resource_json", guarded)
    assert len(build_validation_prefixes(source_eval)) == 21
    assert not any(
        marker.split(".", 1)[1] in relative
        for marker in TEST_TASK_MARKERS
        for relative in opened
    )


def test_validator_calls_no_model_environment_policy_tool_subprocess_or_network(
    source_eval: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "read-only"
    run_model_probe(source_eval, bundle, backend_kind="fake", seed=17)

    from agent_learning_loop.dataops_environment import DataOpsEnvironment
    from agent_learning_loop.incident_environment import IncidentEnvironment
    from agent_learning_loop.policy import ScriptedPolicy
    from agent_learning_loop.workspace import WorkspaceEnvironment

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("execution attempted")

    monkeypatch.setattr(DataOpsEnvironment, "execute", forbidden)
    monkeypatch.setattr(IncidentEnvironment, "execute", forbidden)
    monkeypatch.setattr(WorkspaceEnvironment, "write_text", forbidden)
    monkeypatch.setattr(ScriptedPolicy, "decide", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    assert validate_model_probe(bundle, source_eval).execution_calls == 0


class _CapacityBackend:
    def __init__(self, model_id: str) -> None:
        self._spec = select_model_spec(model_id)

    @property
    def spec(self) -> LocalModelSpec:
        return self._spec

    def generate(self, model_input: ModelBackendInput) -> ModelGeneration:
        from agent_learning_loop.model_probe_backend import ModelProbeCapacityError

        raise ModelProbeCapacityError(
            CapacityEvidence(
                category="cuda_out_of_memory",
                free_vram_bytes=128,
                total_vram_bytes=8_000,
                peak_allocated_bytes=7_000,
                peak_reserved_bytes=7_500,
            )
        )

    def close(self) -> None:
        return None


class _PartialCapacityBackend(_CapacityBackend):
    def __init__(self) -> None:
        super().__init__("Qwen/Qwen3-1.7B")
        self.calls = 0

    def generate(self, model_input: ModelBackendInput) -> ModelGeneration:
        self.calls += 1
        if self.calls == 1:
            return _qwen_generation(self.spec)
        return super().generate(model_input)


class _ImpossibleRuntimeBackend:
    def __init__(self) -> None:
        self._spec = select_model_spec("Qwen/Qwen3-1.7B")

    @property
    def spec(self) -> LocalModelSpec:
        return self._spec

    def generate(self, model_input: ModelBackendInput) -> ModelGeneration:
        return _qwen_generation(self.spec, impossible_runtime=True)

    def close(self) -> None:
        return None


class _ValidRuntimeBackend(_ImpossibleRuntimeBackend):
    def generate(self, model_input: ModelBackendInput) -> ModelGeneration:
        return _qwen_generation(self.spec)


def test_partial_oom_after_first_generation_fails_without_bundle(
    source_eval: Path, tmp_path: Path
) -> None:
    output = tmp_path / "partial-oom"
    backend = _PartialCapacityBackend()

    with pytest.raises(ModelProbeRunError, match="partial_generation_capacity_failure"):
        run_model_probe(
            source_eval,
            output,
            backend_kind="qwen3",
            model_id="Qwen/Qwen3-1.7B",
            seed=17,
            backend=backend,
        )

    assert backend.calls == 2
    assert not output.exists()


def test_completed_qwen_rejects_impossible_runtime_evidence(
    source_eval: Path, tmp_path: Path
) -> None:
    output = tmp_path / "impossible-runtime"

    with pytest.raises(ModelProbeRunError, match="qwen_generation_runtime"):
        run_model_probe(
            source_eval,
            output,
            backend_kind="qwen3",
            model_id="Qwen/Qwen3-1.7B",
            seed=17,
            backend=_ImpossibleRuntimeBackend(),
        )

    assert not output.exists()


def test_validator_rejects_resigned_impossible_qwen_runtime(
    source_eval: Path, tmp_path: Path
) -> None:
    bundle = tmp_path / "valid-runtime"
    run_model_probe(
        source_eval,
        bundle,
        backend_kind="qwen3",
        model_id="Qwen/Qwen3-1.7B",
        seed=17,
        backend=_ValidRuntimeBackend(),
    )
    assert validate_model_probe(bundle, source_eval).status == "completed"

    raw_path = bundle / "raw-generations.jsonl"
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    impossible = _qwen_generation(
        select_model_spec("Qwen/Qwen3-1.7B"), impossible_runtime=True
    )
    first["generation"] = impossible.model_dump(mode="json")
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    manifest_path = bundle / "probe-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact["path"] == "raw-generations.jsonl":
            artifact["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest["bundle_fingerprint"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "bundle_fingerprint"
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ModelProbeValidationError, match="qwen_generation_runtime"):
        validate_model_probe(bundle, source_eval)


def test_only_1_7b_can_publish_capacity_blocked_without_actions(
    source_eval: Path, tmp_path: Path
) -> None:
    blocked = tmp_path / "blocked"
    backend = _CapacityBackend("Qwen/Qwen3-1.7B")
    outcome = run_model_probe(
        source_eval,
        blocked,
        backend_kind="qwen3",
        model_id="Qwen/Qwen3-1.7B",
        seed=17,
        backend=backend,
    )

    assert outcome.status == "capacity_blocked"
    assert (blocked / "raw-generations.jsonl").read_bytes() == b""
    assert (blocked / "records.jsonl").read_bytes() == b""
    assert validate_model_probe(blocked, source_eval).status == "capacity_blocked"

    required = tmp_path / "required"
    with pytest.raises(ModelProbeRunError, match="required_0_6b_capacity_failure"):
        run_model_probe(
            source_eval,
            required,
            backend_kind="qwen3",
            model_id="Qwen/Qwen3-0.6B",
            seed=17,
            backend=_CapacityBackend("Qwen/Qwen3-0.6B"),
        )
    assert not required.exists()


def test_validator_rejects_joint_resigned_impossible_capacity(
    source_eval: Path, tmp_path: Path
) -> None:
    bundle = tmp_path / "capacity"
    run_model_probe(
        source_eval,
        bundle,
        backend_kind="qwen3",
        model_id="Qwen/Qwen3-1.7B",
        seed=17,
        backend=_CapacityBackend("Qwen/Qwen3-1.7B"),
    )
    assert validate_model_probe(bundle, source_eval).status == "capacity_blocked"

    manifest_path = bundle / "probe-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["capacity_evidence"]["peak_allocated_bytes"] = 7_600
    manifest["bundle_fingerprint"] = canonical_sha256(
        {key: value for key, value in manifest.items() if key != "bundle_fingerprint"}
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ModelProbeValidationError, match="invalid_model_probe_bundle"):
        validate_model_probe(bundle, source_eval)


def test_model_probe_cli_runs_fake_and_read_only_validation(
    source_eval: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "cli-probe"

    assert (
        main(
            [
                "run-model-probe",
                "--eval-bundle",
                str(source_eval),
                "--output-dir",
                str(bundle),
                "--backend",
                "fake",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "validate-model-probe",
                "--bundle",
                str(bundle),
                "--eval-bundle",
                str(source_eval),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "tasks=6, prefixes=21, executions=0" in output
    assert '"execution_calls":0' in output


def test_qwen_cli_reports_stable_missing_extra(
    source_eval: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import agent_learning_loop.model_probe_runner as runner
    from agent_learning_loop.model_probe_backend import ModelProbeBackendError

    def missing(*args: object, **kwargs: object) -> object:
        raise ModelProbeBackendError("model_probe_extra_missing")

    monkeypatch.setattr(runner, "Qwen3LocalBackend", missing)
    exit_code = main(
        [
            "run-model-probe",
            "--eval-bundle",
            str(source_eval),
            "--output-dir",
            str(tmp_path / "missing"),
            "--backend",
            "qwen3",
            "--model-id",
            "Qwen/Qwen3-0.6B",
            "--snapshot-dir",
            str(tmp_path / "snapshot"),
        ]
    )

    assert exit_code == 2
    assert "model_probe_extra_missing" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()
