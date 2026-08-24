"""Shared canonical JSON bytes and unkeyed SHA-256 content identities."""

from __future__ import annotations

import hashlib
import json


def canonical_json_bytes(payload: object) -> bytes:
    """Serialize JSON-compatible data with one stable UTF-8 representation."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_sha256(payload: object) -> str:
    """Return an unkeyed content digest; this is not a signature."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
