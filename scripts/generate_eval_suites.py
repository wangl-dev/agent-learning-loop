"""Regenerate the checked-in M5A suite resource from frozen identities."""

from __future__ import annotations

import json
from pathlib import Path

from agent_learning_loop.eval_suites import expected_eval_suites


def render_eval_suites_resource() -> str:
    suites = expected_eval_suites()
    ordered = [
        suites["system-correctness-v1"],
        suites["runtime-reliability-v1"],
        suites["recovery-replay-v1"],
    ]
    return (
        json.dumps(
            [suite.model_dump(mode="json") for suite in ordered],
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def main() -> None:
    target = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_learning_loop"
        / "eval_suites"
        / "suites-v1.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_eval_suites_resource(), encoding="utf-8")


if __name__ == "__main__":
    main()
