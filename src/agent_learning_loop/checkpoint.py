"""Atomic read/write helpers for the field-minimized M3A checkpoint."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from agent_learning_loop.durable_schemas import Checkpoint
from agent_learning_loop.journal import canonical_json_bytes


class CheckpointIdentityError(ValueError):
    """The checkpoint ID does not bind the checkpoint's persisted recovery state."""


def checkpoint_id(checkpoint: Checkpoint) -> str:
    """Hash every checkpoint field except the ID itself using canonical JSON."""
    payload = checkpoint.model_dump(mode="json", exclude={"checkpoint_id"})
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def write_checkpoint_atomic(path: Path, checkpoint: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(checkpoint.model_dump_json(indent=2))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_checkpoint(path: Path) -> Checkpoint:
    checkpoint = Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
    if checkpoint.checkpoint_id != checkpoint_id(checkpoint):
        raise CheckpointIdentityError("checkpoint ID does not match recovery state")
    return checkpoint
