"""Version-route M1 and M2 event/result artifacts without changing v1."""

from __future__ import annotations

import json
from pathlib import Path

from agent_learning_loop.runtime_schemas import RuntimeEvent, RuntimeResult
from agent_learning_loop.schemas import Event, RunResult


def read_event_artifact(path: Path) -> Event | RuntimeEvent:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") == "1":
        return Event.model_validate(payload)
    if payload.get("schema_version") == "2":
        return RuntimeEvent.model_validate(payload)
    raise ValueError("unsupported event artifact schema version")


def read_result_artifact(path: Path) -> RunResult | RuntimeResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") == "1":
        return RunResult.model_validate(payload)
    if payload.get("schema_version") == "2":
        return RuntimeResult.model_validate(payload)
    raise ValueError("unsupported result artifact schema version")
