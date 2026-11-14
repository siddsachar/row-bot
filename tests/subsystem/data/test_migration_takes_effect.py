"""What the Hermes import brings over takes effect where Row-Bot reads it.

Each test imports from a fake Hermes home into a scratch data folder through the
Settings › Data migration (scan, review, apply), then asks Row-Bot's own readers.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.migration.fixtures import create_realistic_hermes_home

pytestmark = pytest.mark.platform


@pytest.fixture
def profile(tmp_path, reload_for_data_dir):
    """A scratch Row-Bot data folder, with the modules that bind it at import re-bound to it."""
    data = tmp_path / "profile"
    data.mkdir()
    reload_for_data_dir(data, "row_bot.mcp_client.config", "row_bot.providers.config",
                        "row_bot.providers.saved_model_settings")
    return data


def _import(source: Path, *, choose, overwrite: bool = False, owner: str = "owner") -> tuple[dict, dict]:
    """Scan, review and apply the items ``choose`` picks from the preview; the preview and receipt."""
    from row_bot.application import client_migration

    preview = client_migration.scan_migration(owner_id=owner, provider="hermes", source=str(source))
    chosen = [item["id"] for item in preview["items"] if choose(item)]
    assert chosen, [item["id"] for item in preview["items"]]
    request = dict(owner_id=owner, plan_id=preview["plan_id"], revision=preview["revision"],
                   selected_ids=chosen, overwrite=overwrite)
    review = client_migration.review_migration_apply(**request)
    receipt = client_migration.apply_selected_migration(
        **request, command_id=str(uuid4()), review_digest=review["review_digest"], confirmed=True)
    return preview, receipt


def _item(preview: dict, item_id: str) -> dict:
    return next(item for item in preview["items"] if item["id"] == item_id)


def test_the_imported_persona_is_the_personality_the_app_reads(tmp_path, profile, reload_for_data_dir):
    source = create_realistic_hermes_home(tmp_path / ".hermes")
    preview, receipt = _import(source, choose=lambda item: item["id"] == "identity:personality")
    assert receipt["status"] == "completed", receipt
    assert "Calm, direct" in _item(preview, "identity:personality")["reason"]

    (identity,) = reload_for_data_dir(None, "row_bot.identity")
    assert identity.get_personality() == "Calm, direct, remembers commitments, prefers concise plans."


def test_a_persona_never_silently_replaces_the_personality_already_set(tmp_path, profile, reload_for_data_dir):
    source = create_realistic_hermes_home(tmp_path / ".hermes")
    (profile / "user_config.json").write_text(json.dumps({"identity": {"name": "Rowan", "personality": "Warm."}}),
                                              encoding="utf-8")
    from row_bot.application import client_migration

    preview = client_migration.scan_migration(owner_id="owner", provider="hermes", source=str(source))
    persona = _item(preview, "identity:personality")
    assert persona["status"] == "conflict" and not persona["selected"]
    with pytest.raises(Exception, match="invalid_migration_selection"):
        client_migration.review_migration_apply(owner_id="owner", plan_id=preview["plan_id"],
                                                revision=preview["revision"], selected_ids=[persona["id"]],
                                                overwrite=False)

    _, receipt = _import(source, choose=lambda item: item["id"] == "identity:personality", overwrite=True)
    assert receipt["status"] == "completed", receipt
    (identity,) = reload_for_data_dir(None, "row_bot.identity")
    assert identity.get_identity_config() == {
        "name": "Rowan", "personality": "Calm, direct, remembers commitments, prefers concise plans."}
    backups = list((profile / "migration-backups").glob("*/user_config.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["identity"]["personality"] == "Warm."


def test_agent_instructions_become_an_unpinned_skill(tmp_path, profile, reload_for_data_dir):
    source = create_realistic_hermes_home(tmp_path / ".hermes")
    _, receipt = _import(source, choose=lambda item: item["id"] == "skills:instructions-from-hermes")
    assert receipt["status"] == "completed", receipt

    (skills,) = reload_for_data_dir(None, "row_bot.skills")
    skills.load_skills()
    skill = skills.get_skill("instructions-from-hermes")
    assert skill is not None
    assert "ask before destructive shell operations" in skill.instructions
    assert not skills.is_pinned("instructions-from-hermes")


def test_mcp_servers_appear_turned_off_in_the_real_mcp_config(tmp_path, profile):
    import row_bot.mcp_client.config as mcp_config

    source = create_realistic_hermes_home(tmp_path / ".hermes")
    preview, receipt = _import(source, choose=lambda item: item["category"] == "mcp")
    assert {item["id"] for item in preview["items"] if item["category"] == "mcp"} == {"mcp:linear", "mcp:time"}
    assert receipt["status"] == "completed", receipt

    servers = mcp_config.load_config()["servers"]
    assert servers["time"]["enabled"] is False
    assert (servers["time"]["command"], servers["time"]["args"]) == ("npx", ["-y", "mcp-server-time"])
    # Its settings are named for the person to fill in; no value is copied.
    assert servers["linear"]["enabled"] is False and servers["linear"]["env"] == {}
    assert "LINEAR_API_KEY" in _item(preview, "mcp:linear")["reason"]
    assert mcp_config.get_servers(enabled_only=True) == {}


def test_an_mcp_server_already_set_up_is_never_replaced(tmp_path, profile):
    import row_bot.mcp_client.config as mcp_config

    mine = mcp_config.normalize_config({"servers": {"time": {"command": "uvx", "args": ["my-time"], "enabled": True}}})
    mcp_config.save_config(mine)
    source = create_realistic_hermes_home(tmp_path / ".hermes")
    from row_bot.application import client_migration

    preview = client_migration.scan_migration(owner_id="owner", provider="hermes", source=str(source))
    time_item = _item(preview, "mcp:time")
    assert time_item["status"] == "skipped" and not time_item["selected"]
    with pytest.raises(Exception, match="invalid_migration_selection"):
        client_migration.review_migration_apply(owner_id="owner", plan_id=preview["plan_id"],
                                                revision=preview["revision"], selected_ids=["mcp:time"],
                                                overwrite=True)
    _import(source, choose=lambda item: item["id"] == "mcp:linear")
    servers = mcp_config.load_config()["servers"]
    assert (servers["time"]["command"], servers["time"]["enabled"]) == ("uvx", True)
    assert servers["linear"]["enabled"] is False


def test_memories_wait_in_the_import_review_queue_until_the_person_checks_them(tmp_path, profile):
    from row_bot.application import memory_import

    source = create_realistic_hermes_home(tmp_path / ".hermes")
    preview, receipt = _import(source, choose=lambda item: item["category"] == "memories")
    assert receipt["status"] == "completed", receipt
    review = receipt["memory_review"]
    assert review["found"] == 6
    assert "review" in _item(preview, "memories:memory.md")["label"].lower()
    try:
        batch = memory_import.status("owner", review["batch_id"])
        assert batch["status"] == "open" and batch["scanned"] is True and batch["receipt"] is None
        found = memory_import.candidates("owner", review["batch_id"])["items"]
        texts = {item["text"] for item in found}
        assert "User prefers local-first tools unless cloud is explicitly faster." in texts
        assert "Uses a Windows desktop and a local Ollama fallback." in texts
        assert {item["source"] for item in found} == {"hermes"}
        # Another window's session can't read them.
        with pytest.raises(Exception, match="not_found"):
            memory_import.status("someone-else", review["batch_id"])
    finally:
        memory_import.discard("owner", review["batch_id"])
    assert not (profile / "memory").exists()


def test_the_default_model_and_providers_merge_without_clobbering(tmp_path, profile):
    from row_bot.providers import custom, saved_model_settings

    source = create_realistic_hermes_home(tmp_path / ".hermes")
    preview, receipt = _import(source, choose=lambda item: item["id"] in {"model:default", "providers:local-llm"})
    assert receipt["status"] == "completed", receipt
    assert saved_model_settings.read_saved_model_settings()[0]["model"] == "model:openai:gpt-5.4"
    (endpoint,) = custom.list_custom_endpoints()
    assert (endpoint["id"], endpoint["base_url"]) == ("local-llm", "http://127.0.0.1:11434/v1")

    # A second import keeps the person's own choices: their default model and their endpoint.
    saved_model_settings.update_saved_model_settings(lambda raw: {**raw, "model": "model:anthropic:claude"})
    again = tmp_path / "again" / ".hermes"
    create_realistic_hermes_home(again)
    from row_bot.application import client_migration

    preview = client_migration.scan_migration(owner_id="owner", provider="hermes", source=str(again))
    assert _item(preview, "model:default")["status"] == "conflict"
    assert _item(preview, "providers:local-llm")["status"] == "skipped"
    assert not _item(preview, "model:default")["selected"]
    assert saved_model_settings.read_saved_model_settings()[0]["model"] == "model:anthropic:claude"
    assert len(custom.list_custom_endpoints()) == 1


def test_a_model_on_a_provider_row_bot_lacks_is_not_brought_over(tmp_path, profile):
    source = create_realistic_hermes_home(tmp_path / ".hermes")
    config = (source / "config.yaml").read_text(encoding="utf-8")
    (source / "config.yaml").write_text(config.replace("  provider: openai\n  model: gpt-5.4",
                                                       "  provider: nous\n  model: Hermes-4-405B", 1),
                                        encoding="utf-8")
    from row_bot.application import client_migration

    preview = client_migration.scan_migration(owner_id="owner", provider="hermes", source=str(source))
    model = _item(preview, "model:default")
    assert model["action"] == "manual_review" and not model["selected"]
    assert "Choose a model" in model["reason"]


def test_hermes_in_its_windows_folder_is_found(tmp_path, monkeypatch):
    from row_bot.application import client_first_run, client_migration
    from row_bot.migration import detect_hermes_source

    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert client_migration.detect_sources()["sources"][0]["found"] is False

    create_realistic_hermes_home(local / "hermes")
    assert client_migration.detect_sources()["sources"][0] == {
        "provider": "hermes", "label": "Hermes Agent", "found": True, "place": "%LOCALAPPDATA%\\hermes"}
    assert {"id": "hermes", "label": "Hermes Agent"} in client_first_run.import_sources()
    assert detect_hermes_source().source.found is True
    preview = client_migration.scan_migration(owner_id="owner", provider="hermes", source="",
                                              target=str(tmp_path / "target"))
    assert preview["source_found"] is True and preview["summary"]["total"] > 0

    # The home folder's own copy comes first when both exist.
    create_realistic_hermes_home(home / ".hermes")
    assert client_migration.detect_sources()["sources"][0]["place"] == "~/.hermes"
