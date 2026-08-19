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


def test_all_three_fixed_tasks_complete_end_to_end(tmp_path: Path) -> None:
    fixtures = load_all_tasks()

    assert [fixture.task.task_id for fixture in fixtures] == [
        "workspace.build-summary",
        "workspace.fix-config",
        "workspace.update-status",
    ]

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
