from __future__ import annotations

from agent_learning_loop.protocols import (
    EnvironmentProtocol,
    PolicyProtocol,
    ToolProtocol,
    VerifierProtocol,
    WorkspaceOperationsProtocol,
)
from agent_learning_loop.schemas import (
    Action,
    Observation,
    Task,
    ToolResult,
    VerifierCheck,
    VerifierResult,
    WorkspaceExpectedState,
    WorkspaceSetup,
    WorkspaceSnapshot,
)


class FakeEnvironment:
    def reset(self, setup: WorkspaceSetup, *, task_id: str) -> Observation:
        return Observation(
            task_id=task_id,
            step_index=0,
            visible_paths=sorted(setup.files),
        )

    def observe(
        self, *, task_id: str, step_index: int, last_tool_result: ToolResult | None
    ) -> Observation:
        return Observation(
            task_id=task_id,
            step_index=step_index,
            visible_paths=["file.txt"],
            last_tool_result=last_tool_result,
        )

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(files={"file.txt": "value\n"})

    def close(self) -> None:
        return None


class FakeWorkspaceOperations:
    def list_files(self, path: str) -> list[str]:
        return [path]

    def read_text(self, path: str) -> str:
        return path

    def write_text(self, path: str, content: str) -> None:
        return None


class FakeTool:
    name = "read_text"

    def execute(
        self, environment: WorkspaceOperationsProtocol, action: Action
    ) -> ToolResult:
        return ToolResult(status="ok", payload={"content": environment.read_text("file.txt")})


class FakePolicy:
    def decide(self, task: Task, observation: Observation) -> Action | None:
        if observation.step_index == 0:
            return Action(tool_name=task.allowed_tools[0], arguments={"path": "file.txt"})
        return None


class FakeVerifier:
    def verify(
        self,
        initial: WorkspaceSnapshot,
        final: WorkspaceSnapshot,
        expected: WorkspaceExpectedState,
    ) -> VerifierResult:
        passed = final.files == expected.required_files
        return VerifierResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            checks=[VerifierCheck(name="fake_state", passed=passed, detail="fake contract")],
        )


def test_fake_environment_satisfies_environment_contract() -> None:
    environment: EnvironmentProtocol = FakeEnvironment()

    observation = environment.reset(
        WorkspaceSetup(files={"file.txt": "value\n"}),
        task_id="workspace.fake",
    )

    assert observation.visible_paths == ["file.txt"]
    assert environment.snapshot().files == {"file.txt": "value\n"}
    environment.close()


def test_fake_tool_satisfies_tool_contract() -> None:
    tool: ToolProtocol = FakeTool()
    result = tool.execute(
        FakeWorkspaceOperations(),
        Action(tool_name="read_text", arguments={"path": "file.txt"}),
    )

    assert result.status == "ok"
    assert result.payload == {"content": "file.txt"}


def test_fake_policy_satisfies_policy_contract() -> None:
    policy: PolicyProtocol = FakePolicy()
    task = Task(
        task_id="workspace.fake",
        instruction="Read one file.",
        allowed_tools=["read_text"],
        fixture_id="workspace.fake.v1",
        provenance="test fixture",
    )

    action = policy.decide(
        task,
        Observation(task_id=task.task_id, step_index=0, visible_paths=["file.txt"]),
    )

    assert action == Action(tool_name="read_text", arguments={"path": "file.txt"})


def test_fake_verifier_satisfies_verifier_contract() -> None:
    verifier: VerifierProtocol = FakeVerifier()
    snapshot = WorkspaceSnapshot(files={"file.txt": "value\n"})
    expected = WorkspaceExpectedState(
        required_files={"file.txt": "value\n"},
        allowed_mutations=["file.txt"],
    )

    result = verifier.verify(snapshot, snapshot, expected)

    assert result.passed is True
