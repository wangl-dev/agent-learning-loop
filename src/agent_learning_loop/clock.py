"""Injectable monotonic clock and sleeper for deterministic M2 budget tests."""

from __future__ import annotations

import time
from typing import Protocol


class ClockProtocol(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
