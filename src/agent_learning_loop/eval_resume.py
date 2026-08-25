"""Internal fixed-argument second-process resume used by the M5A diagnostic."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_learning_loop.durable_runtime import DurableValidationError, resume_durable_task
from agent_learning_loop.eval_clock import DeterministicEvalClock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-learning-loop-eval-resume")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        resume_durable_task(args.run_dir, clock=DeterministicEvalClock())
    except (DurableValidationError, OSError, UnicodeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
