"""Back up and restore the local profile (decision 21): no secrets, no silent overwrite."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from row_bot import profile_restore
from row_bot.application import profile_backup as backup

pytestmark = pytest.mark.subsystem

KEY = "sk-live-SYNTHETIC-DO-NOT-ARCHIVE-1234567890"
WEBHOOK = "webhook-SYNTHETIC-secret-abcdef"
MCP_KEY = "mcp-SYNTHETIC-header-key-xyz"


def _db(path: Path, statements: list[str]) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        for statement in statements:
            conn.execute(statement)


def _rows(path: Path, query: str) -> list:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(query).fetchall()


def _profile(root: Path) -> Path:
    data = root / "profile"
    data.mkdir()
    (data / "api_keys.json").write_text(json.dumps({"openai": KEY}), encoding="utf-8")
    (data / "gmail").mkdir()
    (data / "gmail" / "token.json").write_text(json.dumps({"token": KEY}), encoding="utf-8")
    (data / "browser_profile").mkdir()
    (data / "browser_profile" / "Cookies").write_text(KEY, encoding="utf-8")
    (data / "whatsapp_session").mkdir()
    (data / "whatsapp_session" / "session").write_text(KEY, encoding="utf-8")
    (data / "runtime").mkdir()
    (data / "runtime" / "launch.json").write_text(KEY, encoding="utf-8")
    (data / "cache").mkdir()
    (data / "cache" / "big.bin").write_bytes(b"x" * 1024)
    (data / "logs").mkdir()
    (data / "logs" / "row_bot.log").write_text("log line", encoding="utf-8")
    (data / "model_catalog_cache.json").write_text("{}", encoding="utf-8")
    (data / "launcher-abc-splash.ready").write_text("", encoding="utf-8")
    (data / "tools_config.json.lock").write_text("", encoding="utf-8")
    # B179: leftovers of atomic writes and the checkpoint lock folder.
    (data / ".skills_activation.x7k2m9qa.json").write_text("{}", encoding="utf-8")
    (data / ".checkpoint-locks").mkdir()
    (data / ".checkpoint-locks" / "thread.lock").write_text("", encoding="utf-8")
    _db(data / "mobile.db", ["CREATE TABLE sessions(token TEXT)", f"INSERT INTO sessions VALUES ('{KEY}')"])
    _db(data / "threads.db", ["CREATE TABLE threads(id TEXT, title TEXT)",
                               "INSERT INTO threads VALUES ('t1', 'Kept conversation')"])
    _db(data / "tasks.db", ["CREATE TABLE tasks(id TEXT, name TEXT, trigger TEXT)",
                             "INSERT INTO tasks VALUES ('w1', 'Hook', '" + json.dumps({"type": "webhook", "secret": WEBHOOK}) + "')",
                             "INSERT INTO tasks VALUES ('w2', 'Daily', NULL)"])
    (data / "mcp_servers.json").write_text(json.dumps({"servers": {"search": {
        "transport": "streamable_http", "url": "https://example.invalid/mcp",
        "headers": {"x-api-key": MCP_KEY}, "env": {}}}}), encoding="utf-8")
    (data / "providers.json").write_text(json.dumps({"providers": {
        "openai": {"api_key_ref": "keyring:openai"}, "ollama": {"host": "http://127.0.0.1:11434"}}}), encoding="utf-8")
    (data / "designer").mkdir()
    (data / "designer" / "projects").mkdir()
    (data / "designer" / "projects" / "deck.json").write_text(json.dumps({"name": "Deck"}), encoding="utf-8")
    (data / "tools_config.json").write_text(json.dumps({"enabled": ["web_search"]}), encoding="utf-8")
    return data


def _archive_text(archive: Path) -> str:
    with zipfile.ZipFile(archive) as bundle:
        return "\n".join(bundle.read(name).decode("utf-8", "replace") for name in bundle.namelist())


def test_a_backup_holds_the_profile_without_secrets_caches_or_logs(tmp_path):
    data = _profile(tmp_path)
    result = backup.create_backup(data, tmp_path / "Backups", now=datetime(2026, 9, 29, 7, 10))
    archive = Path(result["path"])
    assert archive.name == "Row-Bot backup 2026-09-29 0710.zip"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert {"threads.db", "tasks.db", "mcp_servers.json", "providers.json", "tools_config.json",
            "designer/projects/deck.json", backup.MANIFEST} <= names
    for left_out in ("api_keys.json", "gmail/token.json", "browser_profile/Cookies",
                     "whatsapp_session/session", "runtime/launch.json", "cache/big.bin",
                     "logs/row_bot.log", "model_catalog_cache.json", "mobile.db",
                     "launcher-abc-splash.ready", "tools_config.json.lock",
                     ".skills_activation.x7k2m9qa.json", ".checkpoint-locks/thread.lock"):
        assert left_out not in names
    text = _archive_text(archive)
    assert KEY not in text and WEBHOOK not in text and MCP_KEY not in text
    manifest = json.loads(zipfile.ZipFile(archive).read(backup.MANIFEST))
    assert manifest["format"] == "row-bot-backup" and manifest["format_version"] == 1
    assert {"kind": "provider", "name": "openai"} in manifest["sign_in_again"]
    assert {"kind": "account", "name": "Gmail"} in manifest["sign_in_again"]
    assert {"kind": "mcp", "name": "search"} in manifest["sign_in_again"]
    assert {"kind": "webhooks", "name": "1 webhook workflow"} in manifest["sign_in_again"]
    assert backup.backup_state(data)["name"] == archive.name
    # The live profile is untouched.
    assert WEBHOOK in json.dumps(_rows(data / "tasks.db", "SELECT trigger FROM tasks"))


def _zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, text in members.items():
            bundle.writestr(name, text)
    return path


def _manifest(**changes) -> str:
    return json.dumps({"format": "row-bot-backup", "format_version": 1, "app_version": "1.0.0",
                       "created_at": "2026-09-29T07:10:00", **changes})


@pytest.mark.parametrize("members, code", [
    ({"notes.txt": "hello"}, "backup_not_row_bot"),
    ({backup.MANIFEST: json.dumps({"format": "something-else"})}, "backup_not_row_bot"),
    ({backup.MANIFEST: _manifest(format_version=2)}, "backup_newer"),
    ({backup.MANIFEST: _manifest(app_version="99.0.0")}, "backup_newer"),
    ({backup.MANIFEST: _manifest(), "../outside.txt": "x"}, "backup_invalid"),
    ({backup.MANIFEST: _manifest(), "api_keys.json": "{}"}, "backup_invalid"),
    ({backup.MANIFEST: _manifest(), "gmail/token.json": "{}"}, "backup_invalid"),
])
def test_foreign_newer_or_unsafe_archives_are_refused(tmp_path, members, code):
    archive = _zip(tmp_path / "candidate.zip", members)
    with pytest.raises(backup.BackupError, match=code):
        backup.inspect_backup(archive, app_version="4.9.1")
    data = tmp_path / "profile"
    data.mkdir()
    with pytest.raises(backup.BackupError, match=code):
        backup.stage_restore(archive, data, source_name="candidate.zip", app_version="4.9.1")
    assert not (data / profile_restore.PENDING_MARKER).exists()
    assert not (data / profile_restore.PENDING_DIR).exists()
    (tmp_path / "plain.txt").write_text("not a zip", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="backup_not_row_bot"):
        backup.inspect_backup(tmp_path / "plain.txt", app_version="4.9.1")


def test_a_restore_applies_on_the_next_start_and_keeps_the_current_profile_aside(tmp_path):
    source = _profile(tmp_path)
    archive = Path(backup.create_backup(source, tmp_path / "Backups")["path"])
    target = tmp_path / "other"
    target.mkdir()
    _db(target / "threads.db", ["CREATE TABLE threads(id TEXT, title TEXT)",
                                 "INSERT INTO threads VALUES ('t9', 'Current conversation')"])
    (target / "api_keys.json").write_text(json.dumps({"openai": "this-computer-key"}), encoding="utf-8")
    (target / "logs").mkdir()
    (target / "logs" / "row_bot.log").write_text("current log", encoding="utf-8")
    staged = backup.stage_restore(archive, target, source_name=archive.name)
    assert staged["staged"] is True
    # Staging never changes the profile in use.
    titles = _rows(target / "threads.db", "SELECT title FROM threads")
    assert titles == [("Current conversation",)]
    applied = profile_restore.apply_pending(target, now=datetime(2026, 9, 29, 8, 0))
    assert applied["status"] == "applied" and applied["kept_aside"] == "before-restore-20260929-080000"
    assert _rows(target / "threads.db", "SELECT title FROM threads") == [("Kept conversation",)]
    aside = target / applied["kept_aside"]
    assert _rows(aside / "threads.db", "SELECT title FROM threads") == [("Current conversation",)]
    # What a backup never holds stays where it was.
    assert json.loads((target / "api_keys.json").read_text(encoding="utf-8")) == {"openai": "this-computer-key"}
    assert (target / "logs" / "row_bot.log").read_text(encoding="utf-8") == "current log"
    assert (target / "designer" / "projects" / "deck.json").exists()
    assert profile_restore.pending(target) is None
    assert profile_restore.result(target)["sign_in_again"]
    # Nothing pending: the next start changes nothing.
    assert profile_restore.apply_pending(target) is None


def test_a_failed_restore_puts_the_current_profile_back(tmp_path, monkeypatch):
    source = _profile(tmp_path)
    archive = Path(backup.create_backup(source, tmp_path / "Backups")["path"])
    target = tmp_path / "other"
    target.mkdir()
    (target / "tools_config.json").write_text(json.dumps({"enabled": ["mine"]}), encoding="utf-8")
    backup.stage_restore(archive, target, source_name=archive.name)
    real_move = profile_restore.shutil.move
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if ".restore-pending" in str(src) and calls["n"] > 2:
            raise PermissionError("file in use")
        return real_move(src, dst)

    monkeypatch.setattr(profile_restore.shutil, "move", flaky)
    outcome = profile_restore.apply_pending(target, now=datetime(2026, 9, 29, 9, 0))
    assert outcome["status"] == "failed"
    assert json.loads((target / "tools_config.json").read_text(encoding="utf-8")) == {"enabled": ["mine"]}
    assert not any(path.name.startswith("before-restore-") for path in target.iterdir())
    assert profile_restore.pending(target) is None


def test_cancel_drops_a_staged_restore_without_touching_the_profile(tmp_path):
    source = _profile(tmp_path)
    archive = Path(backup.create_backup(source, tmp_path / "Backups")["path"])
    target = tmp_path / "other"
    target.mkdir()
    (target / "tools_config.json").write_text("{}", encoding="utf-8")
    backup.stage_restore(archive, target, source_name=archive.name)
    assert profile_restore.cancel(target) is True
    assert profile_restore.apply_pending(target) is None
    assert (target / "tools_config.json").read_text(encoding="utf-8") == "{}"


def test_the_server_applies_a_pending_restore_before_it_opens_the_profile():
    """Source contract: the start hook runs before any other Row-Bot import in the server."""
    source = (Path(__file__).resolve().parents[3] / "src" / "row_bot" / "app.py").read_text(encoding="utf-8")
    source = source.replace("\r\n", "\n")
    hook = source.index("apply_on_start()")
    first_import = min(source.index(line) for line in ("\nfrom row_bot.brand", "\nfrom row_bot.data_paths"))
    assert hook < first_import
    assert 'if __name__ == "__main__":\n    from row_bot.profile_restore import apply_on_start' in source


def test_an_older_backup_with_files_now_left_out_still_restores_without_them(tmp_path):
    """B181: entries a newer version treats as machine-local are skipped, not refused."""
    archive = _zip(tmp_path / "older.zip", {
        backup.MANIFEST: _manifest(),
        "tools_config.json": "{}",
        ".skills_activation.x7k2m9qa.json": "{}",
        ".checkpoint-locks/thread.lock": "",
        "logs/row_bot.log": "old log",
    })
    info = backup.inspect_backup(archive, app_version="4.9.1")
    assert info["bytes"] == len("{}")
    data = tmp_path / "profile"
    data.mkdir()
    backup.stage_restore(archive, data, source_name="older.zip", app_version="4.9.1")
    staged = data / profile_restore.PENDING_DIR / "files"
    assert sorted(path.name for path in staged.rglob("*") if path.is_file()) == ["tools_config.json"]
    # Secrets are still refused outright.
    with pytest.raises(backup.BackupError, match="backup_invalid"):
        backup.inspect_backup(_zip(tmp_path / "keys.zip", {backup.MANIFEST: _manifest(),
                                                           "x/token.json": "{}"}), app_version="4.9.1")
