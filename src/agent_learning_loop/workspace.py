"""A deterministic UTF-8 file environment with a resolved-path boundary."""

from __future__ import annotations

from pathlib import Path

from agent_learning_loop.schemas import Observation, ToolResult, WorkspaceSetup, WorkspaceSnapshot


class WorkspaceError(RuntimeError):
    """Base error for expected Workspace failures."""


class WorkspaceBoundaryError(WorkspaceError):
    """A requested path escaped or attempted to traverse the Workspace root."""


class WorkspaceOperationError(WorkspaceError):
    """A requested in-boundary file operation was invalid."""


def ensure_resolved_within_root(root: Path, candidate: Path) -> None:
    """Reject a resolved candidate that is not root or one of its descendants."""
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceBoundaryError("resolved path is outside the Workspace root") from exc


class WorkspaceEnvironment:
    """Own one explicit root and expose only three controlled text operations."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def reset(self, setup: WorkspaceSetup, *, task_id: str) -> Observation:
        self.root.mkdir(parents=True, exist_ok=True)
        if any(self.root.iterdir()):
            raise WorkspaceOperationError("Workspace root must be empty before reset")
        for path, content in setup.files.items():
            self.write_text(path, content)
        return self.observe(task_id=task_id, step_index=0, last_tool_result=None)

    def observe(
        self, *, task_id: str, step_index: int, last_tool_result: ToolResult | None
    ) -> Observation:
        return Observation(
            task_id=task_id,
            step_index=step_index,
            visible_paths=self.list_files("."),
            last_tool_result=last_tool_result,
        )

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            files={path: self.read_text(path) for path in self.list_files(".")}
        )

    def close(self) -> None:
        """Keep files available as run artifacts; no external resource is held."""

    def list_files(self, path: str) -> list[str]:
        directory = self._resolve_user_path(path)
        if not directory.is_dir():
            raise WorkspaceOperationError("list_files requires an existing directory")
        paths: list[str] = []
        for candidate in directory.rglob("*"):
            resolved = candidate.resolve()
            ensure_resolved_within_root(self.root, resolved)
            if candidate.is_file():
                paths.append(candidate.relative_to(self.root).as_posix())
        return sorted(paths)

    def read_text(self, path: str) -> str:
        candidate = self._resolve_user_path(path)
        if not candidate.is_file():
            raise WorkspaceOperationError("read_text requires an existing file")
        return candidate.read_text(encoding="utf-8")

    def write_text(self, path: str, content: str) -> None:
        candidate = self._resolve_user_path(path)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        ensure_resolved_within_root(self.root, candidate.resolve())
        candidate.write_text(content, encoding="utf-8", newline="\n")

    def _resolve_user_path(self, path: str) -> Path:
        requested = Path(path)
        if requested.is_absolute() or requested.drive:
            raise WorkspaceBoundaryError("absolute paths are not allowed")
        if ".." in requested.parts:
            raise WorkspaceBoundaryError("parent traversal is not allowed")
        candidate = (self.root / requested).resolve()
        ensure_resolved_within_root(self.root, candidate)
        return candidate
