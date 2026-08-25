"""A deterministic clock for the fixed M5A Runtime experiments."""

from __future__ import annotations


class DeterministicEvalClock:
    """Advance only when the Runtime asks to sleep; never use wall-clock time."""

    def __init__(self) -> None:
        self._current = 0.0

    def monotonic(self) -> float:
        return self._current

    def sleep(self, seconds: float) -> None:
        self._current += seconds
