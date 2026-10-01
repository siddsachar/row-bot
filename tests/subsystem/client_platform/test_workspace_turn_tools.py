"""A turn bound to a code folder works through Developer, not the generic file and shell tools."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from tests.contracts.client_platform.test_headless_lifecycle import platform, command  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream

pytestmark = pytest.mark.subsystem

ENABLED = ("filesystem", "shell", "developer", "web_search")


def _turn_tools(platform, conversation: str) -> list[str]:  # noqa: F811
    """The tool names the provider receives for one submitted turn."""
    seen: list[list[str]] = []
    final = f"{conversation}-final"
    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(content="Done", id=final),), final), ("done", "Done")))

    def provider(text, tools, config, **kwargs):
        seen.append(list(tools))
        yield from fake.stream(text, tools, config, **kwargs)

    platform.stream_factory = provider
    receipt = platform.execute(owner_id="fixture", idempotency_key=conversation, target=conversation,
        command=command("conversation.submit", conversation, {
            "text": "Fix the failing test", "attachment_refs": [], "submission_id": f"{conversation}-input",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}},
            revision=str(platform.get_conversation(conversation)["revision"])))
    handle = platform.registry.get(receipt["execution_id"])
    assert handle.producer_done.wait(20) and handle.status == "completed"
    return seen[0]


def test_code_folder_turn_drops_the_generic_file_and_shell_tools(platform, monkeypatch, tmp_path):  # noqa: F811
    from row_bot import conversation_resources
    from row_bot.developer import storage
    from row_bot.tools import registry

    monkeypatch.setattr(registry, "get_enabled_tools", lambda: [SimpleNamespace(name=name) for name in ENABLED])
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    folder = tmp_path / "project"
    folder.mkdir()
    workspace = storage.add_or_update_local_workspace(str(folder))
    conversation_resources.bind("conversation-a", "workspace", workspace.id, expected_revision="0")

    # Generic filesystem/shell would bypass the Developer ledger, lease and sandbox.
    assert _turn_tools(platform, "conversation-a") == ["developer", "web_search"]
    assert _turn_tools(platform, "conversation-b") == list(ENABLED)
