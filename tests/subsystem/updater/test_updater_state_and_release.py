from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone

import pytest


pytestmark = pytest.mark.subsystem


def _reload_updater(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    sys.modules.pop("row_bot.updater", None)
    import row_bot.updater as updater

    return importlib.reload(updater)


def _release(version: str, *, prerelease: bool = False, asset_name: str = "Row-Bot-9.0.0-Windows-x64.exe") -> dict:
    body = (
        "# Release notes\n\n"
        "- deterministic updater coverage\n\n"
        "<!-- row-bot-update-manifest -->\n"
        "```manifest\nschema: 1\nfiles:\n"
        f"  {asset_name}: sha256={'a' * 64}\n"
        "```\n"
    )
    return {
        "tag_name": f"v{version}",
        "prerelease": prerelease,
        "published_at": "2026-06-23T12:00:00Z",
        "html_url": f"https://github.com/siddsachar/row-bot/releases/tag/v{version}",
        "body": body,
        "assets": [
            {
                "name": asset_name,
                "size": 123,
                "browser_download_url": f"https://github.com/siddsachar/row-bot/releases/download/v{version}/{asset_name}",
            }
        ],
    }


def test_load_state_normalizes_invalid_fields_and_state_mutators_notify(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    updater._CONFIG_PATH.write_text(
        json.dumps(
            {
                "channel": "nightly",
                "auto_check": False,
                "check_interval_hours": 0,
                "last_check": "2026-06-23T00:00:00+00:00",
                "last_success": "2026-06-23T01:00:00+00:00",
                "skipped_versions": ["8.0.0", 9],
                "dismissed_banner_versions": ["7.0.0", None],
            }
        ),
        encoding="utf-8",
    )
    updater._state = None
    state = updater.get_update_state()
    seen: list[tuple[str, list[str], list[str], object]] = []
    unsubscribe = updater.subscribe(
        lambda st: seen.append((st.channel, list(st.skipped_versions), list(st.dismissed_banner_versions), st.available))
    )
    state.available = updater.UpdateInfo(
        version="9.0.0",
        channel="stable",
        published_at="",
        notes_md="",
        notes_summary="",
        asset_name="Row-Bot-9.0.0-Windows-x64.exe",
        asset_url="https://github.com/siddsachar/row-bot/releases/download/v9.0.0/Row-Bot-9.0.0-Windows-x64.exe",
        asset_size=1,
        sha256="a" * 64,
        html_url="",
        is_prerelease=False,
    )

    updater.skip_version("9.0.0")
    updater.dismiss_banner("9.0.0")
    updater.set_channel("beta")
    unsubscribe()
    updater.set_channel("stable")

    assert state.channel == "stable"
    assert state.auto_check is False
    assert state.check_interval_hours == 1
    assert state.skipped_versions[:1] == ["8.0.0"]
    assert state.dismissed_banner_versions[:1] == ["7.0.0"]
    assert seen[-1] == ("beta", ["8.0.0", "9.0.0"], ["7.0.0", "9.0.0"], None)
    with pytest.raises(ValueError, match="invalid channel"):
        updater.set_channel("canary")


def test_parse_release_selects_platform_asset_and_summarizes_notes(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")

    info = updater._parse_release(_release("9.0.0"), "stable")

    assert info is not None
    assert info.version == "9.0.0"
    assert info.asset_name == "Row-Bot-9.0.0-Windows-x64.exe"
    assert info.sha256 == "a" * 64
    assert "deterministic updater coverage" in info.notes_summary
    assert "manifest" not in info.notes_summary.lower()
    assert updater._parse_release({"tag_name": "v9.0.0", "assets": []}, "stable") is None
    assert updater._parse_release({**_release("9.0.0"), "tag_name": ""}, "stable") is None


def test_check_for_updates_filters_channel_versions_skips_and_fetch_failures(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater, "__version__", "4.0.0")
    state = updater.get_update_state()
    state.channel = "beta"
    state.skipped_versions = ["4.3.0"]
    releases = [
        _release("4.1.0"),
        _release("4.3.0"),
        _release("4.2.0", prerelease=True),
        _release("3.9.0"),
    ]
    monkeypatch.setattr(updater, "_fetch_releases_payload", lambda _channel: releases)

    best = updater.check_for_updates(force=True)

    assert best is not None
    assert best.version == "4.2.0"
    assert updater.get_update_state().available is best
    assert updater.get_update_state().last_success is not None

    monkeypatch.setattr(updater, "_fetch_releases_payload", lambda _channel: None)
    assert updater.check_for_updates(force=True) is best


def test_check_for_updates_debounce_returns_cached_update_without_fetching(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    state = updater.get_update_state()
    state.available = updater.UpdateInfo(
        version="9.0.0",
        channel="stable",
        published_at="",
        notes_md="",
        notes_summary="",
        asset_name="Row-Bot-9.0.0-Windows-x64.exe",
        asset_url="https://github.com/siddsachar/row-bot/releases/download/v9.0.0/Row-Bot-9.0.0-Windows-x64.exe",
        asset_size=1,
        sha256="a" * 64,
        html_url="",
        is_prerelease=False,
    )
    state.last_check = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        updater,
        "_fetch_releases_payload",
        lambda _channel: pytest.fail("debounced update check should not fetch release metadata"),
    )

    assert updater.check_for_updates(force=False) is state.available


def test_scheduler_is_idempotent_and_disabled_for_dev_installs(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "is_dev_install", lambda: True)

    updater.start_update_scheduler()
    updater.start_update_scheduler()
    updater.stop_update_scheduler()

    assert updater._scheduler_thread is None
