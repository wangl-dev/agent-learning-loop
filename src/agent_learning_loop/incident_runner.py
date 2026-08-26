"""Scripted runner for packaged Incident v1 fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agent_learning_loop.incident_corpus import IncidentCorpus, validate_incident_corpus
from agent_learning_loop.incident_environment import IncidentEnvironment
from agent_learning_loop.incident_schemas import IncidentRunResult
from agent_learning_loop.incident_verifier import IncidentStateVerifier
from agent_learning_loop.vertical_slice import OutputExistsError


def run_incident_task(
    corpus: IncidentCorpus,
    task_id: str,
    output_directory: Path,
    *,
    run_id: str,
) -> IncidentRunResult:
    """Run one reviewed catalog without mutating packaged resources."""
    if output_directory.exists() and any(output_directory.iterdir()):
        raise OutputExistsError("run directory is not empty")
    fixture = next((item for item in corpus.fixtures if item.task.task_id == task_id), None)
    catalog = next((item for item in corpus.catalogs if item.task_id == task_id), None)
    if fixture is None or catalog is None:
        raise ValueError("unknown_incident_task")
    output_directory.mkdir(parents=True, exist_ok=True)
    environment = IncidentEnvironment(fixture, run_id=run_id)
    initial = environment.snapshot()
    events: list[str] = []
    for entry in catalog.actions:
        result = environment.execute(entry.action)
        events.append(entry.action.model_copy(update={"schema_version": "1"}).model_dump_json())
        events.append(result.model_dump_json())
    verifier = IncidentStateVerifier().verify(
        initial,
        environment.snapshot(),
        fixture.private.expected,
        list(environment.audit),
        run_id=run_id,
        task_id=task_id,
    )
    outcome: Literal["passed", "failed"] = "passed" if verifier.passed else "failed"
    run_result = IncidentRunResult(
        run_id=run_id,
        task_id=task_id,
        outcome=outcome,
        verifier=verifier,
    )
    (output_directory / "events.jsonl").write_text(
        "\n".join(events) + "\n", encoding="utf-8", newline="\n"
    )
    (output_directory / "audit.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in environment.audit),
        encoding="utf-8",
        newline="\n",
    )
    (output_directory / "result.json").write_text(
        run_result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return run_result


def run_all_incident_tasks(output_root: Path) -> list[IncidentRunResult]:
    corpus = validate_incident_corpus()
    if output_root.exists() and any(output_root.iterdir()):
        raise OutputExistsError("output directory is not empty")
    output_root.mkdir(parents=True, exist_ok=True)
    return [
        run_incident_task(
            corpus,
            fixture.task.task_id,
            output_root / fixture.task.task_id.replace(".", "_"),
            run_id=f"incident-{index}",
        )
        for index, fixture in enumerate(corpus.fixtures, start=1)
    ]
