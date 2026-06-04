from __future__ import annotations

import json
from pathlib import Path

from row_bot.migration import row_bot_legacy_rebrand as mig
from row_bot.ui import post_migration


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_post_migration_notice_builds_workspace_guidance_and_respects_seen_marker(tmp_path):
    data_dir = tmp_path / ".row-bot"
    report_path = data_dir / "migration_reports" / "row-bot-v4-rebrand-20260604T120000Z.json"
    report = {
        "migration_id": mig.MIGRATION_ID,
        "status": "completed",
        "source": str(tmp_path / ".thoth"),
        "target": str(data_dir),
        "report_path": str(report_path),
        "files_copied_count": 12,
        "files_rewritten_count": 3,
        "files_skipped_count": 1,
        "secret_migration": {
            "api_keys": {"copied": 2, "metadata_updated": 2, "failed": 0},
            "channel_secrets": {"copied": 1, "failed": 0},
        },
        "workspace_migration": {
            "action": "rewritten_to_row_bot_default",
            "configured_workspace_before": str(tmp_path / "Documents" / "Thoth"),
            "configured_workspace_after": str(tmp_path / "Documents" / "Row-Bot"),
            "user_guidance": "Files were not moved.",
        },
        "warnings": ["Legacy workspace exists."],
    }
    _write_json(report_path, report)

    notice = post_migration.should_show_post_migration_notice(data_dir=data_dir, app_config={})

    assert notice is not None
    assert notice["id"] == str(report_path)
    assert notice["summary"] == [
        "Copied 12 file(s)",
        "Rewritten 3 app-owned file(s)",
        "Skipped 1 existing target file(s)",
    ]
    assert "API keys: copied 2, metadata repaired 2" in notice["secret_lines"]
    assert notice["workspace"]["headline"].startswith("Workspace set to Documents/Row-Bot")
    assert post_migration.should_show_post_migration_notice(
        data_dir=data_dir,
        app_config={post_migration.NOTICE_SEEN_CONFIG_KEY: mig.MIGRATION_ID},
    ) is None
    assert post_migration.should_show_post_migration_notice(
        data_dir=data_dir,
        app_config={post_migration.NOTICE_SEEN_REPORT_CONFIG_KEY: str(report_path)},
    ) is None


def test_post_migration_notice_ignores_fresh_installs(tmp_path):
    data_dir = tmp_path / ".row-bot"
    report_path = data_dir / "migration_reports" / "row-bot-v4-rebrand-20260604T120000Z.json"
    _write_json(
        report_path,
        {
            "migration_id": mig.MIGRATION_ID,
            "status": "fresh_install",
            "report_path": str(report_path),
        },
    )

    assert post_migration.should_show_post_migration_notice(data_dir=data_dir, app_config={}) is None
