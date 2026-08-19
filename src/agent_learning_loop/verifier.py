"""State-based verification for M1 Workspace tasks."""

from __future__ import annotations

from agent_learning_loop.schemas import (
    VerifierCheck,
    VerifierResult,
    WorkspaceExpectedState,
    WorkspaceSnapshot,
)


class WorkspaceStateVerifier:
    """Compare actual final files with private requirements and mutation scope."""

    def verify(
        self,
        initial: WorkspaceSnapshot,
        final: WorkspaceSnapshot,
        expected: WorkspaceExpectedState,
    ) -> VerifierResult:
        required_ok = all(
            final.files.get(path) == content for path, content in expected.required_files.items()
        )
        unchanged_ok = all(
            path in initial.files and final.files.get(path) == initial.files[path]
            for path in expected.unchanged_files
        )
        forbidden_ok = all(path not in final.files for path in expected.forbidden_paths)
        changed_paths = {
            path
            for path in initial.files.keys() | final.files.keys()
            if initial.files.get(path) != final.files.get(path)
        }
        mutation_scope_ok = changed_paths <= set(expected.allowed_mutations)
        checks = [
            VerifierCheck(
                name="required_state",
                passed=required_ok,
                detail=(
                    "required file states matched"
                    if required_ok
                    else "required file state differed"
                ),
            ),
            VerifierCheck(
                name="unchanged_state",
                passed=unchanged_ok,
                detail=(
                    "protected files stayed unchanged"
                    if unchanged_ok
                    else "a protected file changed"
                ),
            ),
            VerifierCheck(
                name="forbidden_side_effects",
                passed=forbidden_ok,
                detail=(
                    "forbidden paths stayed absent"
                    if forbidden_ok
                    else "a forbidden path appeared"
                ),
            ),
            VerifierCheck(
                name="mutation_scope",
                passed=mutation_scope_ok,
                detail=(
                    "all mutations stayed in scope"
                    if mutation_scope_ok
                    else "an out-of-scope mutation occurred"
                ),
            ),
        ]
        passed = all(check.passed for check in checks)
        return VerifierResult(passed=passed, score=1.0 if passed else 0.0, checks=checks)
