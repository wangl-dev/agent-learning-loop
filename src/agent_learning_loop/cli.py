"""Command-line entry point for the M0 package foundation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from agent_learning_loop import __version__


def build_parser() -> argparse.ArgumentParser:
    """Create the project command-line parser."""
    parser = argparse.ArgumentParser(prog="agent-learning-loop")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a process exit code."""
    build_parser().parse_args(argv)
    return 0
