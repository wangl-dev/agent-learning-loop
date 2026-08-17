from __future__ import annotations

import subprocess
import sys


def test_module_version_command_reports_project_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_learning_loop", "--version"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "agent-learning-loop 0.1.0.dev0"
    assert result.stderr == ""
