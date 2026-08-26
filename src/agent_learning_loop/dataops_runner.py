"""Scripted runner for the fixed DataOps v1 corpus."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Literal, cast

from agent_learning_loop.dataops_corpus import DataOpsCorpus, validate_dataops_corpus
from agent_learning_loop.dataops_environment import DataOpsEnvironment
from agent_learning_loop.dataops_schemas import DataOpsRunResult
from agent_learning_loop.dataops_verifier import DataOpsStateVerifier
from agent_learning_loop.vertical_slice import OutputExistsError


class DataOpsRunError(ValueError):
    """Stable public category for a sanitized DataOps execution failure."""


def run_dataops_task(
    corpus: DataOpsCorpus,
    task_id: str,
    output_directory: Path,
    *,
    run_id: str,
) -> DataOpsRunResult:
    if output_directory.exists():
        raise OutputExistsError("run directory already exists")
    fixture = next((item for item in corpus.fixtures if item.task.task_id == task_id), None)
    catalog = next((item for item in corpus.catalogs if item.task_id == task_id), None)
    manifest = next((item for item in corpus.manifests if item.task_id == task_id), None)
    if fixture is None or catalog is None or manifest is None:
        raise ValueError("unknown_dataops_task")
    output_directory.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix="all-dataops-run-") as temporary:
            with DataOpsEnvironment(
                fixture,
                run_id=run_id,
                database_directory=Path(temporary),
            ) as environment:
                initial = environment.snapshot()
                events: list[str] = []
                for entry in catalog.actions:
                    result = environment.execute(entry.action)
                    events.append(entry.action.model_dump_json())
                    events.append(result.model_dump_json())
                final = environment.snapshot()
                audit = list(environment.audit)
        verifier = DataOpsStateVerifier().verify(
            initial,
            final,
            fixture.private.expected,
            fixture.task.scope,
            audit,
            [entry.action for entry in catalog.actions],
            run_id=run_id,
            task_id=task_id,
            trusted_fixture=fixture,
            trusted_split=manifest.split,
        )
        outcome: Literal["passed", "failed"] = "passed" if verifier.passed else "failed"
        run_result = DataOpsRunResult(
            run_id=run_id,
            task_id=task_id,
            split=manifest.split,
            outcome=outcome,
            terminal_state=cast(Literal["committed", "rolled_back"], final.terminal_state),
            attempted_row_count=final.attempted_row_count,
            committed_row_count=final.committed_row_count,
            verifier=verifier,
        )
        (output_directory / "events.jsonl").write_text(
            "\n".join(events) + "\n", encoding="utf-8", newline="\n"
        )
        (output_directory / "audit.jsonl").write_text(
            "".join(record.model_dump_json() + "\n" for record in audit),
            encoding="utf-8",
            newline="\n",
        )
        (output_directory / "result.json").write_text(
            run_result.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return run_result
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(output_directory, ignore_errors=True)
        raise DataOpsRunError("dataops_execution_error") from exc
    except Exception:
        shutil.rmtree(output_directory, ignore_errors=True)
        raise


def run_all_dataops_tasks(output_root: Path) -> list[DataOpsRunResult]:
    corpus = validate_dataops_corpus()
    if output_root.exists():
        raise OutputExistsError("output directory already exists")
    output_root.mkdir(parents=True)
    try:
        return [
            run_dataops_task(
                corpus,
                fixture.task.task_id,
                output_root / fixture.task.task_id.replace(".", "_"),
                run_id=f"dataops-{index}",
            )
            for index, fixture in enumerate(corpus.fixtures, start=1)
        ]
    except Exception:
        shutil.rmtree(output_root, ignore_errors=True)
        raise
