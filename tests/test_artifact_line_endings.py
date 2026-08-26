from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from agent_learning_loop.eval_runner import run_eval
from agent_learning_loop.eval_schemas import EvalBundleManifest, ExactRatio
from agent_learning_loop.eval_validator import validate_eval_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "b2673b8dec27c6a973667b5a7ab78c7b2b8e32ac"
CANONICAL_PREFIX = Path("reports/v0.1/eval-bundle")
SUITE_FINGERPRINTS = {
    "system-correctness-v1": "624dfb19c2b9575056dd9d24a92e3dcb4852617eb538ee3541fb28cae933488e",
    "runtime-reliability-v1": "a8c5e2389ce1bbe31ae7895ecbfe211be3460aee40563c4c13efb0523d89ac2e",
    "recovery-replay-v1": "4fb499de8c42ac2d78aaa962c6e6fda2419e7df5fda45c64287d5b78d23b9a97",
}


@pytest.fixture(scope="module")
def windows_all_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    bundle = tmp_path_factory.mktemp("artifact-line-endings") / "eval-bundle"
    outcome = run_eval("all", SOURCE_COMMIT, bundle)
    assert outcome.exit_code == 0
    assert outcome.manifest.selection.selected_total == 41
    assert outcome.manifest.suite_fingerprints == SUITE_FINGERPRINTS
    assert outcome.summary.verifier_state_success == ExactRatio(
        numerator=38, denominator=40, rate=0.95
    )
    assert outcome.summary.runtime_completion_success == ExactRatio(
        numerator=6, denominator=10, rate=0.6
    )
    assert outcome.summary.physical_executions == ExactRatio(
        numerator=22, denominator=11, rate=2.0
    )
    assert outcome.summary.physical_writes == ExactRatio(
        numerator=10, denominator=11, rate=10 / 11
    )
    assert outcome.summary.oracle_failure_cell_ids == []
    assert len(outcome.summary.pair_deltas) == 3
    assert len(outcome.summary.diagnostics) == 4
    assert all(diagnostic.passed for diagnostic in outcome.summary.diagnostics)
    return bundle


def _git_blob_id(data: bytes, *, path: Path | None = None) -> str:
    command = ["git", "hash-object"]
    if path is None:
        command.append("--no-filters")
    else:
        command.append(f"--path={path.as_posix()}")
    command.append("--stdin")
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=data,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.decode("ascii").strip()


def test_all_bundle_uses_lf_and_keeps_manifest_hashes(
    windows_all_bundle: Path,
) -> None:
    files = sorted(path for path in windows_all_bundle.rglob("*") if path.is_file())
    contents = {
        path.relative_to(windows_all_bundle).as_posix(): path.read_bytes() for path in files
    }
    manifest = EvalBundleManifest.model_validate_json(contents["eval-manifest.json"])

    assert len(contents) == 167
    assert not [path for path, data in contents.items() if b"\r" in data]
    assert len(manifest.artifacts) == 163
    for artifact in manifest.artifacts:
        assert hashlib.sha256(contents[artifact.path]).hexdigest() == artifact.sha256

    validation = validate_eval_bundle(windows_all_bundle)
    assert validation.selected_cells == 41
    assert validation.execution_calls == 0
    assert validation.source_bytes_unchanged is True


def test_canonical_git_filter_preserves_generated_bytes(
    windows_all_bundle: Path,
) -> None:
    changed: list[str] = []
    for path in sorted(windows_all_bundle.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(windows_all_bundle)
        data = path.read_bytes()
        if _git_blob_id(data) != _git_blob_id(data, path=CANONICAL_PREFIX / relative):
            changed.append(relative.as_posix())

    attribute = subprocess.run(
        [
            "git",
            "check-attr",
            "text",
            "--",
            (CANONICAL_PREFIX / "eval-manifest.json").as_posix(),
        ],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    assert not changed
    assert attribute.endswith(": text: unset")
