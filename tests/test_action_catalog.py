from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from agent_learning_loop.action_catalog import (
    ActionCatalog,
    action_fingerprint,
    catalog_fingerprint,
    load_action_catalog,
    load_all_action_catalogs,
)
from agent_learning_loop.policy import ScriptedPolicy
from agent_learning_loop.schemas import Action, Observation
from agent_learning_loop.tasks import load_task

EXPECTED_ACTIONS = {
    "workspace.fix-config": [
        Action(tool_name="read_text", arguments={"path": "app.conf"}),
        Action(
            tool_name="write_text",
            arguments={"path": "app.conf", "content": "mode=production\nport=8080\n"},
        ),
    ],
    "workspace.build-summary": [
        Action(tool_name="read_text", arguments={"path": "input/title.txt"}),
        Action(tool_name="read_text", arguments={"path": "input/items.txt"}),
        Action(
            tool_name="write_text",
            arguments={
                "path": "output/summary.txt",
                "content": "Release: M1\nItems:\n- schema\n- workspace\n",
            },
        ),
    ],
    "workspace.update-status": [
        Action(tool_name="list_files", arguments={"path": "."}),
        Action(tool_name="read_text", arguments={"path": "state/status.txt"}),
        Action(
            tool_name="write_text",
            arguments={"path": "state/status.txt", "content": "ready\n"},
        ),
    ],
}

EXPECTED_CATALOG_FINGERPRINTS = {
    "workspace.fix-config": "b525ee02e439264500f7020abe5c02d8fd344651d91d4b3651686f210d0cf7c4",
    "workspace.build-summary": "79a52f1f6cef8f91bfdc47e3398b71f7f9a7430bb0f086783e58e5acab79d8af",
    "workspace.update-status": "369a631bd2636a25e8b8f7bfbe9ed3ac5399f2485cc5a0a975454a7ee53def8b",
}

EXPECTED_ACTION_FINGERPRINTS = {
    "workspace.fix-config": [
        "7f7857b0f75232a109761b78eb70c6b9d5366e8cac2f114ecb5e66aca5ad6f67",
        "16cb3dbed1e6ba7d6579f9bc0e646dea21b73aac11d0d3fb47882daf7d3f962f",
    ],
    "workspace.build-summary": [
        "7cef14111b786b7621c93fd818e66c65769c1ee3fe6c61bc4266803234e6ec70",
        "3b30b2366fc31d7538d77d06af25c44016baee70fd5421f96e74745057b60f75",
        "12a7726f1fc3c7e5fc7da0720e2776f46cf68d249255768a932bb530dc3f07ab",
    ],
    "workspace.update-status": [
        "3da0b7349aa13a6903a88b9de69f66feee2eeb212a183ee919ebb5c201cb8f9c",
        "b446ff68cd267974bd317625c355e3548473201b6bac7f4f79790f63f5500c29",
        "da4a1101004cb23e23ea311f93ab7d3842fc93143f4edcb3579fa93e26e93601",
    ],
}


def test_existing_catalogs_keep_their_golden_actions_and_fingerprints() -> None:
    catalogs = load_all_action_catalogs()
    catalogs_by_task = {catalog.task_id: catalog for catalog in catalogs}

    assert len(catalogs) == 10
    assert set(EXPECTED_ACTIONS) <= set(catalogs_by_task)
    for task_id, expected_actions in EXPECTED_ACTIONS.items():
        catalog = catalogs_by_task[task_id]
        assert catalog_fingerprint(catalog) == EXPECTED_CATALOG_FINGERPRINTS[
            catalog.task_id
        ]
        assert [entry.action for entry in catalog.actions] == expected_actions
        assert [action_fingerprint(entry.action) for entry in catalog.actions] == (
            EXPECTED_ACTION_FINGERPRINTS[catalog.task_id]
        )


def test_scripted_policy_action_sequence_is_unchanged_after_catalog_migration() -> None:
    policy = ScriptedPolicy()

    for task_id, expected_actions in EXPECTED_ACTIONS.items():
        task = load_task(task_id).task
        actual = [
            policy.decide(
                task,
                Observation(task_id=task_id, step_index=index, visible_paths=[]),
            )
            for index in range(len(expected_actions) + 1)
        ]
        assert actual == [*expected_actions, None]


def test_catalog_schema_is_strict_and_content_change_breaks_golden_identity() -> None:
    catalog = load_action_catalog("workspace.fix-config")
    payload = catalog.model_dump(mode="json")
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        ActionCatalog.model_validate(payload)

    changed = copy.deepcopy(catalog.model_dump(mode="json"))
    changed["actions"][1]["action"]["arguments"]["content"] = "mode=debug\n"
    changed_catalog = ActionCatalog.model_validate(changed)
    assert catalog_fingerprint(changed_catalog) != EXPECTED_CATALOG_FINGERPRINTS[
        catalog.task_id
    ]
