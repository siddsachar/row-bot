"""What the Hermes import brings over takes effect where Row-Bot reads it.

Each test imports from a fake Hermes home into a scratch data folder through the
Settings › Data migration (scan, review, apply), then asks Row-Bot's own readers.
"""

from __future__ import annotations

import json
import os
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
    # The address is shown to the person before it is saved.
    assert "http://127.0.0.1:11434/v1" in _item(preview, "providers:local-llm")["reason"]
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


def _linked_folder(tmp_path: Path) -> Path:
    """A folder reached through a link, as macOS's temporary folders are (/var is /private/var)."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi  # a junction needs no special rights on Windows

        _winapi.CreateJunction(str(real), str(link))
    return link


def test_folders_reached_through_a_linked_parent_still_take_each_item(tmp_path):
    from row_bot.migration import MigrationStatus, apply_migration_plan, build_hermes_plan

    linked = _linked_folder(tmp_path)
    source = create_realistic_hermes_home(linked / ".hermes")
    target = linked / "profile"
    result = apply_migration_plan(build_hermes_plan(source, target_root=target))

    assert [(item.id, item.reason) for item in result.items if item.status == MigrationStatus.ERROR] == []
    assert json.loads((target / "model_settings.json").read_text(encoding="utf-8"))["model"] == "model:openai:gpt-5.4"
    config = json.loads((target / "user_config.json").read_text(encoding="utf-8"))
    assert config["identity"]["personality"] == "Calm, direct, remembers commitments, prefers concise plans."
    endpoints = json.loads((target / "providers.json").read_text(encoding="utf-8"))["custom_endpoints"]
    assert [endpoint["id"] for endpoint in endpoints] == ["local-llm"]


def test_hermes_in_its_windows_folder_is_found(tmp_path, monkeypatch):
    from row_bot.application import client_first_run, client_migration
    from row_bot.migration import detect_hermes_source

    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("HERMES_HOME", raising=False)
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


def test_the_folder_hermes_home_names_is_the_one_found(tmp_path, monkeypatch):
    from row_bot.application import client_first_run, client_migration
    from row_bot.migration import detect_hermes_source

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    create_realistic_hermes_home(home / ".hermes")

    # Hermes uses the folder HERMES_HOME names in place of its usual one; the place is shown, never the path.
    chosen = create_realistic_hermes_home(home / "agents" / "hermes-work")
    monkeypatch.setenv("HERMES_HOME", str(chosen))
    assert client_migration.detect_sources()["sources"][0] == {
        "provider": "hermes", "label": "Hermes Agent", "found": True, "place": "~/agents/hermes-work"}
    assert detect_hermes_source().source.root == chosen
    assert {"id": "hermes", "label": "Hermes Agent"} in client_first_run.import_sources()

    elsewhere = create_realistic_hermes_home(tmp_path / "srv" / "hermes")
    monkeypatch.setenv("HERMES_HOME", str(elsewhere))
    assert client_migration.detect_sources()["sources"][0]["place"] == (
        "%HERMES_HOME%" if os.name == "nt" else "$HERMES_HOME")

    # A link, or an OpenClaw folder, is refused as in the usual places; the usual folder is found instead.
    from row_bot.migration.fixtures import create_realistic_openclaw_home

    monkeypatch.setenv("HERMES_HOME", str(create_realistic_openclaw_home(tmp_path / "claw")))
    assert client_migration.detect_sources()["sources"][0]["place"] == "~/.hermes"
    (tmp_path / "links").mkdir()
    linked = create_realistic_hermes_home(_linked_folder(tmp_path / "links"))
    monkeypatch.setenv("HERMES_HOME", str(linked))
    assert client_migration.detect_sources()["sources"][0]["place"] == "~/.hermes"


def _chat_count(batch: dict) -> int:
    return batch["conversations"]["available"]


def test_past_chats_are_offered_unticked_and_wait_with_the_memories_to_review(tmp_path, profile):
    from row_bot.application import memory_import

    source = create_realistic_hermes_home(tmp_path / ".hermes")
    before = sorted(path.name for path in source.iterdir())
    from row_bot.application import client_migration

    preview = client_migration.scan_migration(owner_id="owner", provider="hermes", source=str(source))
    chats = _item(preview, "memories:past-chats")
    assert chats["label"].startswith("Past chats from state.db: 3 conversations")
    assert chats["selected"] is False and chats["status"] == "planned"
    assert "archive:state.db" not in {item["id"] for item in preview["items"]}

    preview, receipt = _import(source, choose=lambda item: item["category"] == "memories")
    assert receipt["status"] == "completed", receipt
    review = receipt["memory_review"]
    assert (review["found"], review["conversations"]) == (6, 3)
    try:
        batch = memory_import.status("owner", review["batch_id"])
        assert batch["receipt"] is None and _chat_count(batch) == 3
        assert batch["conversations"]["ran"] is False  # looking in them is the person's choice, there
    finally:
        memory_import.discard("owner", review["batch_id"])
    assert not (profile / "memory").exists()
    assert sorted(path.name for path in source.iterdir()) == before  # Hermes's folder is only read


def test_chats_still_in_hermes_write_ahead_log_come_over(tmp_path, profile):
    import sqlite3
    from contextlib import closing

    from row_bot.application import memory_import
    from row_bot.migration.fixtures import write_hermes_chat

    source = create_realistic_hermes_home(tmp_path / ".hermes")
    # Hermes is still running: its newest chat is only in state.db-wal so far.
    with closing(sqlite3.connect(source / "state.db")) as hermes:
        hermes.execute("PRAGMA wal_autocheckpoint=0")
        write_hermes_chat(hermes, "s-new", "Just now", 1763200000, (("user", "My sister is called Ada."),))
        hermes.commit()
        assert (source / "state.db-wal").stat().st_size > 0

        _, receipt = _import(source, choose=lambda item: item["id"] == "memories:past-chats")
        assert receipt["status"] == "completed", receipt
        review = receipt["memory_review"]
        try:
            assert (review["found"], review["conversations"]) == (0, 4)
            assert _chat_count(memory_import.status("owner", review["batch_id"])) == 4
        finally:
            memory_import.discard("owner", review["batch_id"])


def test_cron_jobs_are_named_as_not_converted(tmp_path, profile):
    source = create_realistic_hermes_home(tmp_path / ".hermes")
    preview, receipt = _import(source, choose=lambda item: item["id"] == "identity:personality")
    note = "Cron jobs aren't converted: make a workflow for each one in Workflows."
    assert note in preview["warnings"]
    assert note in receipt["warnings"]
    assert "make a workflow" in _item(preview, "archive:cron")["reason"]
