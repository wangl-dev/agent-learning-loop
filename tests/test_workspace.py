from __future__ import annotations

from pathlib import Path

import pytest

from agent_learning_loop.schemas import Action, WorkspaceSetup
from agent_learning_loop.workspace import (
    WorkspaceBoundaryError,
    WorkspaceEnvironment,
    ensure_resolved_within_root,
)
from agent_learning_loop.workspace_tools import (
    ListFilesTool,
    ReadTextTool,
    WriteTextPayload,
    WriteTextTool,
)


def test_workspace_tools_list_read_and_write_utf8_text(tmp_path: Path) -> None:
    environment = WorkspaceEnvironment(tmp_path / "workspace")
    environment.reset(
        WorkspaceSetup(files={"notes/hello.txt": "你好\n", "keep.txt": "unchanged\n"}),
        task_id="workspace.test",
    )

    listed = ListFilesTool().execute(
        environment,
        Action(tool_name="list_files", arguments={"path": "."}),
    )
    read = ReadTextTool().execute(
        environment,
        Action(tool_name="read_text", arguments={"path": "notes/hello.txt"}),
    )
    written = WriteTextTool().execute(
        environment,
        Action(
            tool_name="write_text",
            arguments={"path": "output/result.txt", "content": "完成\n"},
        ),
    )

    assert listed.payload == {"paths": ["keep.txt", "notes/hello.txt"]}
    assert read.payload == {"content": "你好\n"}
    assert written.payload == {"path": "output/result.txt", "bytes_written": 7}
    assert environment.read_text("output/result.txt") == "完成\n"


def test_tool_arguments_are_validated_before_file_access(tmp_path: Path) -> None:
    environment = WorkspaceEnvironment(tmp_path / "workspace")
    environment.reset(WorkspaceSetup(files={}), task_id="workspace.test")

    result = WriteTextTool().execute(
        environment,
        Action(tool_name="write_text", arguments={"path": "missing-content.txt"}),
    )

    assert result.status == "error"
    assert not (environment.root / "missing-content.txt").exists()


def test_tool_result_payload_rejects_wrong_types_and_unknown_fields() -> None:
    with pytest.raises(ValueError):
        WriteTextPayload.model_validate(
            {"path": "result.txt", "bytes_written": "7", "unexpected": True}
        )


@pytest.mark.parametrize("unsafe_path", ["../outside.txt", "nested/../../outside.txt"])
def test_parent_traversal_is_rejected_without_outside_mutation(
    tmp_path: Path, unsafe_path: str
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("original\n", encoding="utf-8")
    environment = WorkspaceEnvironment(tmp_path / "workspace")
    environment.reset(WorkspaceSetup(files={}), task_id="workspace.test")

    result = WriteTextTool().execute(
        environment,
        Action(tool_name="write_text", arguments={"path": unsafe_path, "content": "changed\n"}),
    )

    assert result.status == "error"
    assert outside.read_text(encoding="utf-8") == "original\n"


def test_absolute_path_is_rejected_without_outside_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n", encoding="utf-8")
    environment = WorkspaceEnvironment(tmp_path / "workspace")
    environment.reset(WorkspaceSetup(files={}), task_id="workspace.test")

    result = ReadTextTool().execute(
        environment,
        Action(tool_name="read_text", arguments={"path": str(outside.resolve())}),
    )

    assert result.status == "error"
    assert "private" not in str(result.payload)


def test_resolved_path_containment_rejects_an_outside_target(tmp_path: Path) -> None:
    root = (tmp_path / "workspace").resolve()
    root.mkdir()
    outside = (tmp_path / "outside.txt").resolve()

    with pytest.raises(WorkspaceBoundaryError):
        ensure_resolved_within_root(root, outside)


def test_symlink_escape_is_rejected_when_links_are_available(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n", encoding="utf-8")
    environment = WorkspaceEnvironment(tmp_path / "workspace")
    environment.reset(WorkspaceSetup(files={}), task_id="workspace.test")
    link = environment.root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"real symlink creation unavailable: {exc}")

    result = ReadTextTool().execute(
        environment,
        Action(tool_name="read_text", arguments={"path": "link.txt"}),
    )

    assert result.status == "error"
    assert "private" not in str(result.payload)
