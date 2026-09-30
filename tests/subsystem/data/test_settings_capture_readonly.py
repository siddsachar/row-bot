from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


pytestmark = pytest.mark.platform


def _enable_real_capture(monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    monkeypatch.setenv("ROW_BOT_DOCS_REAL_DATA", "1")


def test_stability_reports_are_suppressed_for_real_data_capture(monkeypatch) -> None:
    import row_bot.stability as stability

    with TemporaryDirectory(prefix="row-bot-settings-capture-") as temp:
        tmp_path = Path(temp)
        _enable_real_capture(monkeypatch)
        monkeypatch.setattr(stability, "_CRASH_DIR", tmp_path / "crashes")
        monkeypatch.setattr(stability, "_INSTALLED", False)

        stability.setup_stability_monitoring()
        assert stability._write_report("test", "must not persist") is None
        assert not (tmp_path / "crashes").exists()


def test_document_service_uses_query_only_connection_for_real_capture(monkeypatch) -> None:
    from row_bot.document_jobs import DocumentJobService

    with TemporaryDirectory(prefix="row-bot-settings-capture-") as temp:
        data = Path(temp) / "data"
        owner = DocumentJobService(data, read_only=False)
        before = owner.db_path.stat()
        _enable_real_capture(monkeypatch)

        passive = DocumentJobService(data)
        assert passive.read_only is True
        assert passive.list_batches() == []
        after = owner.db_path.stat()
        assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def test_passive_github_status_never_invokes_cli_or_network(monkeypatch) -> None:
    import row_bot.github_account as github

    monkeypatch.setattr(
        github,
        "resolve_github_token",
        lambda **_kwargs: github.GitHubToken("configured", "keyring", "fingerprint"),
    )
    monkeypatch.setattr(github, "_github_cli_status", lambda: (_ for _ in ()).throw(AssertionError("CLI invoked")))
    monkeypatch.setattr(github, "_fetch_github_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network invoked")))

    status = github.get_passive_github_account_status()
    assert status.state == github.GITHUB_STATE_CONFIGURED_UNCHECKED
    assert status.source == "keyring"
    assert status.connected is False


@pytest.mark.slow
def test_task_database_is_query_only_for_real_capture() -> None:
    with TemporaryDirectory(prefix="row-bot-settings-capture-") as temp:
        data = Path(temp) / "data"
        environment = dict(os.environ)
        environment["ROW_BOT_DATA_DIR"] = str(data)
        environment.pop("ROW_BOT_DOCS_CAPTURE", None)
        environment.pop("ROW_BOT_DOCS_REAL_DATA", None)
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import row_bot.tasks; "
                    "from row_bot.runtime import admissions; "
                    "admissions.instance_identity()"
                ),
            ],
            check=True,
            cwd=Path(__file__).resolve().parents[3],
            env=environment,
            capture_output=True,
            text=True,
        )
        database = data / "tasks.db"
        before = (database.read_bytes(), database.stat().st_mtime_ns)

        environment["ROW_BOT_DOCS_CAPTURE"] = "1"
        environment["ROW_BOT_DOCS_REAL_DATA"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sqlite3; import row_bot.tasks as tasks; "
                    "from row_bot.runtime import admissions; "
                    "assert admissions.instance_identity(); "
                    "connection = tasks._get_conn(); "
                    "assert connection.execute('PRAGMA query_only').fetchone()[0] == 1; "
                    "\ntry:\n connection.execute(\"DELETE FROM tasks\")\n"
                    "except sqlite3.OperationalError:\n pass\n"
                    "else:\n raise AssertionError('task database was writable')\n"
                    "connection.close()"
                ),
            ],
            check=False,
            cwd=Path(__file__).resolve().parents[3],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (database.read_bytes(), database.stat().st_mtime_ns) == before
