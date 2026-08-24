from __future__ import annotations

import json
from pathlib import Path

from agent_learning_loop.schemas import Event, RunResult
from agent_learning_loop.tasks import load_all_tasks, load_task
from agent_learning_loop.vertical_slice import execute_task


def read_events(path: Path) -> list[Event]:
    return [
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def normalized_events(events: list[Event]) -> list[dict[str, object]]:
    return [
        {
            "task_id": event.task_id,
            "step_index": event.step_index,
            "event_kind": event.event_kind,
            "payload": event.payload,
        }
        for event in events
    ]


EXPECTED_TASK_IDS = [
    "workspace.build-deploy-manifest",
    "workspace.build-summary",
    "workspace.create-owner-record",
    "workspace.fix-config",
    "workspace.merge-changelog",
    "workspace.normalize-checklist",
    "workspace.reconcile-inventory",
    "workspace.repair-service-map",
    "workspace.update-route",
    "workspace.update-status",
]

NEW_TASK_RESULTS = {
    "workspace.merge-changelog": (
        "CHANGELOG.md",
        "# Changelog\n\n## 1.5\n- Add safe-boundary resume.\n\n"
        "## 1.4\n- Add runtime retry.\n",
    ),
    "workspace.repair-service-map": (
        "config/services.map",
        "api=api.internal:8080\nworker=worker.internal:9000\n"
        "metrics=metrics.internal:9090\n",
    ),
    "workspace.create-owner-record": (
        "services/payments/OWNER",
        "OWNER=team-payments\n",
    ),
    "workspace.build-deploy-manifest": (
        "output/deploy.manifest",
        "service=catalog-api\nimage=registry.local/catalog:v3\nreplicas=3\n",
    ),
    "workspace.reconcile-inventory": (
        "output/reconciled.csv",
        "item,final\nwidget,8\ngadget,7\n",
    ),
    "workspace.normalize-checklist": (
        "checklist.md",
        "- [x] lint\n- [ ] tests\n- [x] package\n",
    ),
    "workspace.update-route": (
        "routes.conf",
        "/health -> health-v1\n/orders -> orders-v2\nfallback -> legacy\n",
    ),
}


def test_all_ten_fixed_tasks_complete_end_to_end(tmp_path: Path) -> None:
    fixtures = load_all_tasks()

    assert [fixture.task.task_id for fixture in fixtures] == EXPECTED_TASK_IDS

    for index, fixture in enumerate(fixtures):
        run_dir = tmp_path / f"run-{index}"
        result = execute_task(fixture, run_dir, run_id=f"run-{index}")
        stored_result = RunResult.model_validate_json(
            (run_dir / "result.json").read_text(encoding="utf-8")
        )
        events = read_events(run_dir / "events.jsonl")

        assert result.outcome == "passed"
        assert stored_result == result
        assert events[0].event_kind == "task_started"
        assert events[-1].event_kind == "run_finished"
        assert {event.run_id for event in events} == {result.run_id}
        assert {event.task_id for event in events} == {fixture.task.task_id}


def test_seven_m4a_tasks_reach_their_distinct_target_states(tmp_path: Path) -> None:
    for task_id, (path, expected_content) in NEW_TASK_RESULTS.items():
        fixture = load_task(task_id)
        initial_files = dict(fixture.private.setup.files)
        result = execute_task(
            fixture,
            tmp_path / task_id.replace(".", "_"),
            run_id=f"semantic-{task_id}",
        )
        workspace = tmp_path / task_id.replace(".", "_") / "workspace"

        assert result.outcome == "passed"
        assert (workspace / path).read_text(encoding="utf-8") == expected_content
        for unchanged_path in fixture.private.expected.unchanged_files:
            assert (workspace / unchanged_path).read_text(encoding="utf-8") == (
                initial_files[unchanged_path]
            )


def test_same_fixture_has_deterministic_event_semantics(tmp_path: Path) -> None:
    fixture = load_task("workspace.build-summary")

    first = execute_task(fixture, tmp_path / "first", run_id="different-run-a")
    second = execute_task(fixture, tmp_path / "second", run_id="different-run-b")
    first_events = read_events(tmp_path / "first" / first.events_file)
    second_events = read_events(tmp_path / "second" / second.events_file)

    assert normalized_events(first_events) == normalized_events(second_events)
    assert first.verifier == second.verifier
    assert first.outcome == second.outcome == "passed"


def test_private_expected_state_is_not_written_to_public_outputs(tmp_path: Path) -> None:
    fixture = load_task("workspace.fix-config")
    private_marker = "private-verifier-marker.txt"
    fixture.private.expected.forbidden_paths.append(private_marker)

    result = execute_task(fixture, tmp_path / "run", run_id="private-check")
    public_text = (tmp_path / "run" / "events.jsonl").read_text(encoding="utf-8")
    public_text += (tmp_path / "run" / "result.json").read_text(encoding="utf-8")

    assert result.outcome == "passed"
    assert private_marker not in public_text
    assert json.loads((tmp_path / "run" / "result.json").read_text(encoding="utf-8"))
