from __future__ import annotations

from pathlib import Path

import pytest
from test_durable_schemas import checkpoint

from agent_learning_loop import checkpoint as checkpoint_module
from agent_learning_loop.checkpoint import read_checkpoint, write_checkpoint_atomic


def test_checkpoint_atomic_replace_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    saved = checkpoint()

    write_checkpoint_atomic(path, saved)

    assert read_checkpoint(path) == saved
    assert not (tmp_path / ".checkpoint.json.tmp").exists()


def test_checkpoint_replace_failure_never_publishes_formal_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "checkpoint.json"

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("controlled replace failure")

    monkeypatch.setattr("agent_learning_loop.checkpoint.os.replace", fail_replace)

    with pytest.raises(OSError):
        write_checkpoint_atomic(path, checkpoint())

    assert not path.exists()
    assert not (tmp_path / "result.json").exists()


def test_checkpoint_id_canonically_binds_every_recovery_field() -> None:
    saved = checkpoint()
    compute_id = getattr(checkpoint_module, "checkpoint_id")

    assert compute_id(saved) == saved.checkpoint_id
    for field, value in [
        ("elapsed_seconds", saved.usage.elapsed_seconds + 1.0),
        ("tool_calls", saved.usage.tool_calls + 1),
    ]:
        changed_usage = saved.usage.model_copy(update={field: value})
        changed = saved.model_copy(update={"usage": changed_usage})
        assert compute_id(changed) != saved.checkpoint_id
    changed_entry = saved.idempotency_entries[0].model_copy(
        update={"bytes_written": saved.idempotency_entries[0].bytes_written + 1}
    )
    changed = saved.model_copy(update={"idempotency_entries": [changed_entry]})
    assert compute_id(changed) != saved.checkpoint_id
    changed = saved.model_copy(update={"workspace_digest": "c" * 64})
    assert compute_id(changed) != saved.checkpoint_id
