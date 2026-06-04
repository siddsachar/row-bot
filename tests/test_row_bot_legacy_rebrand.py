from __future__ import annotations

import json
from pathlib import Path

from row_bot.migration import row_bot_legacy_rebrand as mig


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_rebrand_migration_copies_source_rewrites_config_and_writes_marker(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    target = home / ".row-bot"
    source.mkdir(parents=True)
    (source / "threads.db").write_bytes(b"threads")
    (source / "notes.txt").write_text("user wrote about Thoth here", encoding="utf-8")
    _write_json(source / "user_config.json", {"identity": {"name": "Thoth", "personality": "custom"}})
    _write_json(
        source / "tools_config.json",
        {
            "enabled": {"thoth_status": True},
            "tool_configs": {
                "thoth_status": {"workspace_root": "~/.thoth", "guide": "thoth_status_guide"}
            },
        },
    )
    _write_json(
        source / "api_keys.json",
        {
            "version": 2,
            "storage": "keyring",
            "service": mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source),
            "keys": {"OPENAI_API_KEY": {"configured": True, "fingerprint": "****1234"}},
        },
    )
    designer_html = source / "designer" / "projects" / "p1.html"
    designer_html.parent.mkdir(parents=True)
    designer_html.write_text('<div data-thoth-route="home">__thothRuntime</div>', encoding="utf-8")
    buddy_pack = source / "buddy" / "packs" / "hatch-123"
    buddy_motion = buddy_pack / "motions"
    buddy_motion.mkdir(parents=True)
    _write_json(
        buddy_pack / "manifest.json",
        {
            "id": "hatch-123",
            "status": "motion_pack_generated",
            "preview_path": str(source / "buddy" / "packs" / "hatch-123" / "preview.png"),
            "motion_pack_path": str(source / "buddy" / "packs" / "hatch-123" / "motions" / "manifest.json"),
            "motion_clips": {
                "idle": str(source / "buddy" / "generated" / "motions" / "idle.mp4"),
            },
        },
    )

    backend = FakeKeyring()
    old_service = mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source)
    new_service = mig._service_name_for(mig.TARGET_SERVICE_PREFIX, target)
    backend.set_password(old_service, "api_keys:OPENAI_API_KEY", "sk-secret-1234")

    env: dict[str, str] = {}
    result = mig.ensure_legacy_rebrand_migration(environ=env, home=home, keyring_backend=backend)

    assert result["status"] == "completed"
    assert env["ROW_BOT_DATA_DIR"] == str(target)
    assert "THOTH_DATA_DIR" not in env
    assert (source / "threads.db").read_bytes() == b"threads"
    assert (source / "notes.txt").read_text(encoding="utf-8") == "user wrote about Thoth here"
    assert (target / "threads.db").read_bytes() == b"threads"

    user_config = json.loads((target / "user_config.json").read_text(encoding="utf-8"))
    assert user_config["identity"]["name"] == "Row-Bot"
    assert user_config["identity"]["personality"] == "custom"

    tools_config = json.loads((target / "tools_config.json").read_text(encoding="utf-8"))
    assert tools_config["enabled"]["row_bot_status"] is True
    assert tools_config["tool_configs"]["row_bot_status"]["workspace_root"] == "~/.row-bot"
    assert tools_config["tool_configs"]["row_bot_status"]["guide"] == "row_bot_status_guide"

    assert "data-row-bot-route" in (target / "designer" / "projects" / "p1.html").read_text(encoding="utf-8")
    assert "__rowBotRuntime" in (target / "designer" / "projects" / "p1.html").read_text(encoding="utf-8")
    buddy_manifest = json.loads((target / "buddy" / "packs" / "hatch-123" / "manifest.json").read_text(encoding="utf-8"))
    assert ".thoth" not in json.dumps(buddy_manifest)
    assert ".row-bot" in buddy_manifest["preview_path"]
    assert ".row-bot" in buddy_manifest["motion_pack_path"]
    assert backend.get_password(new_service, "api_keys:OPENAI_API_KEY") == "sk-secret-1234"
    assert "sk-secret" not in json.dumps(result)
    assert (target / "migrations" / "row-bot-v4-rebrand.json").is_file()
    assert list((target / "migration_reports").glob("row-bot-v4-rebrand-*.json"))


def test_rebrand_migration_merges_existing_target_without_overwrite(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    target = home / ".row-bot"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "threads.db").write_bytes(b"legacy")
    (source / "memory.db").write_bytes(b"memory")
    (target / "threads.db").write_bytes(b"existing")

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    assert result["status"] == "completed"
    assert (target / "threads.db").read_bytes() == b"existing"
    assert (target / "memory.db").read_bytes() == b"memory"
    assert any("threads.db" in conflict for conflict in result["conflicts"])


def test_rebrand_migration_rewrites_exact_legacy_default_workspace_without_moving_files(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    legacy_workspace = home / "Documents" / "Thoth"
    target_workspace = home / "Documents" / "Row-Bot"
    source.mkdir(parents=True)
    legacy_workspace.mkdir(parents=True)
    (legacy_workspace / "keep.txt").write_text("workspace content", encoding="utf-8")
    _write_json(
        source / "tools_config.json",
        {
            "tool_configs": {
                "filesystem": {
                    "workspace_root": str(legacy_workspace),
                    "selected_operations": ["read_file"],
                }
            }
        },
    )

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    cfg = json.loads((home / ".row-bot" / "tools_config.json").read_text(encoding="utf-8"))
    assert cfg["tool_configs"]["filesystem"]["workspace_root"] == str(target_workspace)
    assert (legacy_workspace / "keep.txt").read_text(encoding="utf-8") == "workspace content"
    assert not target_workspace.exists()
    workspace = result["workspace_migration"]
    assert workspace["action"] == "rewritten_to_row_bot_default"
    assert workspace["configured_workspace_before"] == str(legacy_workspace)
    assert workspace["configured_workspace_after"] == str(target_workspace)
    assert workspace["legacy_default_exists"] is True


def test_rebrand_migration_preserves_custom_workspace_even_when_name_contains_legacy_brand(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    custom_workspace = home / "Documents" / "Thoth Client Archive"
    source.mkdir(parents=True)
    custom_workspace.mkdir(parents=True)
    _write_json(
        source / "tools_config.json",
        {"tool_configs": {"filesystem": {"workspace_root": str(custom_workspace)}}},
    )

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    cfg = json.loads((home / ".row-bot" / "tools_config.json").read_text(encoding="utf-8"))
    assert cfg["tool_configs"]["filesystem"]["workspace_root"] == str(custom_workspace)
    workspace = result["workspace_migration"]
    assert workspace["action"] == "preserved_custom_workspace"
    assert workspace["configured_workspace_after"] == str(custom_workspace)


def test_rebrand_migration_fresh_install_sets_env_without_marker(tmp_path):
    home = tmp_path / "home"
    env: dict[str, str] = {}

    result = mig.ensure_legacy_rebrand_migration(environ=env, home=home, keyring_backend=FakeKeyring())

    target = home / ".row-bot"
    assert result["status"] == "fresh_install"
    assert env["ROW_BOT_DATA_DIR"] == str(target)
    assert "THOTH_DATA_DIR" not in env
    assert not (target / "migrations" / "row-bot-v4-rebrand.json").exists()


def test_rebrand_migration_copies_plugin_and_chunked_provider_secrets(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    target = home / ".row-bot"
    source.mkdir(parents=True)
    _write_json(
        source / "plugin_secrets.json",
        {
            "version": 2,
            "storage": "keyring",
            "service": mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source),
            "plugins": {"plug": {"TOKEN": {"configured": True, "fingerprint": "****plug"}}},
        },
    )
    _write_json(source / "providers.json", {"version": 1, "providers": {"codex": {"configured": True}}})

    backend = FakeKeyring()
    old_service = mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source)
    new_service = mig._service_name_for(mig.TARGET_SERVICE_PREFIX, target)
    backend.set_password(old_service, "plugin_secrets:plug:TOKEN", "plug-secret")
    backend.set_password(old_service, "providers:codex:access_token.__chunks", "v1:2")
    backend.set_password(old_service, "providers:codex:access_token.__chunk.0000", "abc")
    backend.set_password(old_service, "providers:codex:access_token.__chunk.0001", "def")

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=backend)

    assert result["secret_migration"]["plugin_secrets"]["copied"] == 1
    assert backend.get_password(new_service, "plugin_secrets:plug:TOKEN") == "plug-secret"
    assert backend.get_password(new_service, "providers:codex:access_token.__chunks") == "v1:2"
    assert backend.get_password(new_service, "providers:codex:access_token.__chunk.0000") == "abc"
    assert backend.get_password(new_service, "providers:codex:access_token.__chunk.0001") == "def"


def test_rebrand_migration_repairs_known_api_keys_missing_from_metadata(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    target = home / ".row-bot"
    source.mkdir(parents=True)
    _write_json(
        source / "api_keys.json",
        {
            "version": 2,
            "storage": "keyring",
            "service": mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source),
            "keys": {},
        },
    )

    backend = FakeKeyring()
    old_service = mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source)
    new_service = mig._service_name_for(mig.TARGET_SERVICE_PREFIX, target)
    backend.set_password(old_service, "api_keys:ANTHROPIC_API_KEY", "sk-ant-known")
    backend.set_password(old_service, "api_keys:OPENROUTER_API_KEY", "sk-or-known")

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=backend)

    assert result["secret_migration"]["api_keys"]["copied"] == 2
    assert result["secret_migration"]["api_keys"]["metadata_updated"] == 2
    assert backend.get_password(new_service, "api_keys:ANTHROPIC_API_KEY") == "sk-ant-known"
    assert backend.get_password(new_service, "api_keys:OPENROUTER_API_KEY") == "sk-or-known"
    metadata = json.loads((target / "api_keys.json").read_text(encoding="utf-8"))
    assert metadata["service"] == new_service
    assert metadata["keys"]["ANTHROPIC_API_KEY"]["configured"] is True
    assert metadata["keys"]["ANTHROPIC_API_KEY"]["fingerprint"] == "****nown"
    assert "sk-ant-known" not in json.dumps(result)


def test_rebrand_migration_repairs_already_completed_channel_secrets(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    target = home / ".row-bot"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    _write_json(source / "api_keys.json", {"version": 2, "storage": "keyring", "keys": {}})
    _write_json(
        target / "migrations" / "row-bot-v4-rebrand.json",
        {"migration_id": mig.MIGRATION_ID, "status": "completed", "source": str(source), "target": str(target)},
    )

    backend = FakeKeyring()
    old_service = mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source)
    new_service = mig._service_name_for(mig.TARGET_SERVICE_PREFIX, target)
    backend.set_password(old_service, "channels:slack:SLACK_BOT_TOKEN", "xoxb-old-token")
    backend.set_password(old_service, "api_keys:SLACK_APP_TOKEN", "xapp-old-token")
    backend.set_password(new_service, "channels:slack:SLACK_USER_ID", "keep-existing")
    backend.set_password(old_service, "channels:slack:SLACK_USER_ID", "old-user")

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=backend)

    assert result["status"] == "already_completed"
    assert result["secret_migration"]["channel_secrets"]["copied"] == 2
    assert backend.get_password(new_service, "channels:slack:SLACK_BOT_TOKEN") == "xoxb-old-token"
    assert backend.get_password(new_service, "channels:slack:SLACK_APP_TOKEN") == "xapp-old-token"
    assert backend.get_password(new_service, "channels:slack:SLACK_USER_ID") == "keep-existing"
    assert list((target / "migration_reports").glob("row-bot-v4-rebrand-repair-*.json"))


def test_rebrand_migration_repairs_already_completed_buddy_paths_without_legacy_source(tmp_path):
    home = tmp_path / "home"
    target = home / ".row-bot"
    target.mkdir(parents=True)
    _write_json(
        target / "migrations" / "row-bot-v4-rebrand.json",
        {"migration_id": mig.MIGRATION_ID, "status": "completed", "source": str(home / ".thoth"), "target": str(target)},
    )
    _write_json(
        target / "buddy" / "packs" / "hatch-123" / "manifest.json",
        {
            "id": "hatch-123",
            "status": "motion_pack_generated",
            "preview_path": str(home / ".thoth" / "buddy" / "packs" / "hatch-123" / "preview.png"),
            "motion_pack_path": str(home / ".thoth" / "buddy" / "packs" / "hatch-123" / "motions" / "manifest.json"),
        },
    )

    env: dict[str, str] = {}
    result = mig.ensure_legacy_rebrand_migration(environ=env, home=home, keyring_backend=FakeKeyring())

    assert result["status"] == "already_completed"
    assert result["files_rewritten_count"] == 1
    assert not (home / ".thoth").exists()
    manifest = json.loads((target / "buddy" / "packs" / "hatch-123" / "manifest.json").read_text(encoding="utf-8"))
    assert ".thoth" not in json.dumps(manifest)
    assert ".row-bot" in manifest["preview_path"]
    assert ".row-bot" in manifest["motion_pack_path"]
    assert list((target / "migration_reports").glob("row-bot-v4-rebrand-repair-*.json"))


def test_rebrand_migration_writes_workspace_guidance_repair_for_old_completed_reports(tmp_path):
    home = tmp_path / "home"
    target = home / ".row-bot"
    custom_workspace = home / "Documents" / "Client Work"
    target.mkdir(parents=True)
    custom_workspace.mkdir(parents=True)
    _write_json(
        target / "migrations" / "row-bot-v4-rebrand.json",
        {"migration_id": mig.MIGRATION_ID, "status": "completed", "source": str(home / ".thoth"), "target": str(target)},
    )
    _write_json(
        target / "migration_reports" / "row-bot-v4-rebrand-20260604T100000Z.json",
        {"migration_id": mig.MIGRATION_ID, "status": "completed", "report_path": "old-format"},
    )
    _write_json(
        target / "tools_config.json",
        {"tool_configs": {"filesystem": {"workspace_root": str(custom_workspace)}}},
    )

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    assert result["status"] == "already_completed"
    assert result["workspace_migration"]["action"] == "preserved_custom_workspace"
    reports = sorted((target / "migration_reports").glob("row-bot-v4-rebrand-repair-*.json"))
    assert reports
    repair = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert repair["workspace_migration"]["configured_workspace_after"] == str(custom_workspace)


def test_rebrand_migration_does_not_repair_json_formatting_only_changes(tmp_path):
    home = tmp_path / "home"
    target = home / ".row-bot"
    target.mkdir(parents=True)
    _write_json(
        target / "migrations" / "row-bot-v4-rebrand.json",
        {"migration_id": mig.MIGRATION_ID, "status": "completed", "source": str(home / ".thoth"), "target": str(target)},
    )
    _write_json(
        target / "migration_reports" / "row-bot-v4-rebrand-repair-20260604T100000Z.json",
        {
            "migration_id": mig.MIGRATION_ID,
            "status": "already_completed",
            "workspace_migration": {"action": "already_row_bot_default"},
        },
    )
    (target / "skills_config.json").write_text('{"skills":[]}', encoding="utf-8")
    (target / "app_config.json").write_text('{"setup_complete":true}', encoding="utf-8")
    reports_before = set((target / "migration_reports").glob("row-bot-v4-rebrand-repair-*.json"))

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    assert result["status"] == "already_completed"
    assert result["files_rewritten_count"] == 0
    reports_after = set((target / "migration_reports").glob("row-bot-v4-rebrand-repair-*.json"))
    assert reports_after == reports_before


def test_rebrand_migration_updates_legacy_tool_ids_for_phase5_runtime(tmp_path):
    home = tmp_path / "home"
    target = home / ".row-bot"
    target.mkdir(parents=True)
    _write_json(
        target / "migrations" / "row-bot-v4-rebrand.json",
        {"migration_id": mig.MIGRATION_ID, "status": "completed", "source": str(home / ".thoth"), "target": str(target)},
    )
    _write_json(
        target / "migration_reports" / "row-bot-v4-rebrand-repair-20260604T100000Z.json",
        {
            "migration_id": mig.MIGRATION_ID,
            "status": "already_completed",
            "workspace_migration": {"action": "already_row_bot_default"},
        },
    )
    _write_json(
        target / "tools_config.json",
        {
            "tools": {"row_bot_status": True, "row_bot_updater": False},
            "tool_configs": {
                "row_bot_status": {"guide": "row_bot_status_guide"},
                "row_bot_updater": {"last_check": "manual"},
            },
        },
    )
    _write_json(
        target / "skills_config.json",
        {"skills": {"row_bot_status_guide": False, "browser_guide": True}},
    )

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    assert result["status"] == "already_completed"
    assert result["files_rewritten_count"] == 0

    _write_json(
        target / "tools_config.json",
        {
            "tools": {"thoth_status": True, "thoth_updater": False},
            "tool_configs": {
                "thoth_status": {
                    "guide": "thoth_status_guide",
                    "module": "tools/thoth_status_tool.py",
                    "guide_path": "tool_guides/thoth_status_guide/SKILL.md",
                },
                "thoth_updater": {"last_check": "manual"},
            },
        },
    )
    _write_json(
        target / "skills_config.json",
        {"skills": {"thoth_status_guide": False, "browser_guide": True}},
    )

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=FakeKeyring())

    assert sorted(result["files_rewritten"]) == ["skills_config.json", "tools_config.json"]
    tools_config = json.loads((target / "tools_config.json").read_text(encoding="utf-8"))
    assert "thoth_status" not in tools_config["tools"]
    assert tools_config["tools"]["row_bot_status"] is True
    assert "thoth_updater" not in tools_config["tool_configs"]
    assert tools_config["tool_configs"]["row_bot_status"]["guide"] == "row_bot_status_guide"
    assert tools_config["tool_configs"]["row_bot_status"]["module"] == "tools/row_bot_status_tool.py"
    assert tools_config["tool_configs"]["row_bot_status"]["guide_path"] == "tool_guides/row_bot_status_guide/SKILL.md"
    skills_config = json.loads((target / "skills_config.json").read_text(encoding="utf-8"))
    assert "thoth_status_guide" not in skills_config["skills"]
    assert skills_config["skills"]["row_bot_status_guide"] is False


def test_rebrand_migration_copies_codex_account_secret(tmp_path):
    home = tmp_path / "home"
    source = home / ".thoth"
    target = home / ".row-bot"
    source.mkdir(parents=True)
    _write_json(source / "providers.json", {"version": 1, "providers": {"codex": {"configured": True}}})

    backend = FakeKeyring()
    old_service = mig._service_name_for(mig.LEGACY_SERVICE_PREFIX, source)
    new_service = mig._service_name_for(mig.TARGET_SERVICE_PREFIX, target)
    backend.set_password(old_service, "providers:codex:account", "acct-1234")

    result = mig.ensure_legacy_rebrand_migration(environ={}, home=home, keyring_backend=backend)

    assert result["secret_migration"]["provider_secrets"]["copied"] == 1
    assert backend.get_password(new_service, "providers:codex:account") == "acct-1234"
