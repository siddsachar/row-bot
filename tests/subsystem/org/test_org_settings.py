"""The Org server's settings: loopback by default, from variables or secret files, errors without values."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from row_bot_org.settings import OrgSettings, SettingsError, load_settings, remove_secret_variables

# Reads secret files from a folder: paths, links and file types differ by OS.
pytestmark = pytest.mark.platform

MARKER = "marker-value-7f3a"


def _secrets(tmp_path: Path, **files: bytes) -> Path:
    folder = tmp_path / "secrets"
    folder.mkdir()
    for name, content in files.items():
        (folder / name).write_bytes(content)
    return folder


def test_defaults_listen_on_loopback_without_openapi() -> None:
    settings = load_settings({})

    assert settings == OrgSettings()
    assert (settings.host, settings.port, settings.log_level, settings.openapi) == ("127.0.0.1", 8100, "INFO", False)
    assert (settings.database_url, settings.auto_migrate) == (None, False)


def test_variables_set_each_setting() -> None:
    settings = load_settings({
        "ROW_BOT_ORG_HOST": "0.0.0.0",
        "ROW_BOT_ORG_PORT": "9000",
        "ROW_BOT_ORG_LOG_LEVEL": "debug",
        "ROW_BOT_ORG_OPENAPI": "true",
        "PATH": "unrelated",
    })

    assert settings == OrgSettings(host="0.0.0.0", port=9000, log_level="DEBUG", openapi=True)


def test_a_blank_variable_is_unset() -> None:
    assert load_settings({"ROW_BOT_ORG_PORT": " "}).port == 8100


def test_every_bad_setting_is_named_and_none_is_echoed() -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({
            "ROW_BOT_ORG_HOST": f"{MARKER}.example",
            "ROW_BOT_ORG_PORT": f"{MARKER}",
            "ROW_BOT_ORG_LOG_LEVEL": MARKER,
            "ROW_BOT_ORG_OPENAPI": MARKER,
            "ROW_BOT_ORG_DATABASE_PASSWORD": MARKER,
        })

    message = str(raised.value)
    assert MARKER not in message
    assert raised.value.problems == [
        "ROW_BOT_ORG_DATABASE_PASSWORD is not a Row-Bot Org setting",
        "ROW_BOT_ORG_HOST must be an IP address or localhost",
        "ROW_BOT_ORG_PORT must be a port number from 1 to 65535",
        "ROW_BOT_ORG_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR",
        "ROW_BOT_ORG_OPENAPI must be 1, true, 0 or false",
    ]


@pytest.mark.parametrize("port", ["0", "65536", "-1", "80.5", "9" * 5000, "٨٠"],
                         ids=["zero", "too-high", "negative", "decimal", "5000-digits", "non-ascii-digits"])
def test_a_port_outside_the_range_is_refused(port: str) -> None:
    with pytest.raises(SettingsError, match="ROW_BOT_ORG_PORT must be a port number"):
        load_settings({"ROW_BOT_ORG_PORT": port})


def test_a_setting_can_come_from_the_secrets_folder(tmp_path: Path) -> None:
    folder = _secrets(tmp_path, ROW_BOT_ORG_PORT=b"9100\n", ROW_BOT_ORG_LOG_LEVEL=b"WARNING\r\n")

    settings = load_settings({"ROW_BOT_SECRETS_DIR": str(folder)})

    assert (settings.port, settings.log_level) == (9100, "WARNING")


def test_a_variable_wins_over_a_secret_file(tmp_path: Path) -> None:
    folder = _secrets(tmp_path, ROW_BOT_ORG_PORT=b"9100")

    assert load_settings({"ROW_BOT_SECRETS_DIR": str(folder), "ROW_BOT_ORG_PORT": "9200"}).port == 9200


def test_a_missing_secrets_folder_or_file_leaves_the_default(tmp_path: Path) -> None:
    assert load_settings({"ROW_BOT_SECRETS_DIR": str(tmp_path / "absent")}) == OrgSettings()
    assert load_settings({"ROW_BOT_SECRETS_DIR": str(_secrets(tmp_path))}) == OrgSettings()


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (MARKER.encode() + b"\xff\xfe", "is not UTF-8"),
        (MARKER.encode() * 5000, "is larger than 64 KiB"),
        (f"{MARKER}\n".encode(), "must be a port number"),
    ],
    ids=["not-utf8", "too-large", "not-a-port"],
)
def test_a_bad_secret_file_is_refused_without_its_content(tmp_path: Path, content: bytes, problem: str) -> None:
    folder = _secrets(tmp_path, ROW_BOT_ORG_PORT=content)

    with pytest.raises(SettingsError) as raised:
        load_settings({"ROW_BOT_SECRETS_DIR": str(folder)})

    assert problem in str(raised.value)
    assert MARKER not in str(raised.value)


def test_a_folder_in_place_of_a_secret_file_is_refused(tmp_path: Path) -> None:
    folder = _secrets(tmp_path)
    (folder / "ROW_BOT_ORG_PORT").mkdir()

    with pytest.raises(SettingsError, match="ROW_BOT_ORG_PORT in the secrets folder must be a regular file"):
        load_settings({"ROW_BOT_SECRETS_DIR": str(folder)})


def test_a_linked_secret_file_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("9100", encoding="utf-8")
    folder = _secrets(tmp_path)
    try:
        os.symlink(outside, folder / "ROW_BOT_ORG_PORT")
    except OSError:
        pytest.skip("this account can't make symbolic links")

    with pytest.raises(SettingsError, match="ROW_BOT_ORG_PORT in the secrets folder must not be a link"):
        load_settings({"ROW_BOT_SECRETS_DIR": str(folder)})


DATABASE_URL = f"postgresql://org_app:{MARKER}@db.internal:5432/org"


MIGRATION_URL = f"postgresql://org_owner:{MARKER}@db.internal:5432/org"


def test_the_database_urls_and_auto_migrate_are_settings() -> None:
    settings = load_settings({
        "ROW_BOT_ORG_DATABASE_URL": f" {DATABASE_URL} ",
        "ROW_BOT_ORG_MIGRATION_DATABASE_URL": MIGRATION_URL,
        "ROW_BOT_ORG_AUTO_MIGRATE": "1",
    })

    assert (settings.database_url, settings.migration_database_url, settings.auto_migrate) == (
        DATABASE_URL, MIGRATION_URL, True)
    assert load_settings({"ROW_BOT_ORG_DATABASE_URL": "postgres://db/org"}).database_url == "postgres://db/org"


def test_auto_migrate_needs_the_migration_url() -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({"ROW_BOT_ORG_DATABASE_URL": DATABASE_URL, "ROW_BOT_ORG_AUTO_MIGRATE": "1"})

    assert raised.value.problems == ["ROW_BOT_ORG_AUTO_MIGRATE needs ROW_BOT_ORG_MIGRATION_DATABASE_URL"]


def test_the_connection_limits_are_settings_in_seconds() -> None:
    assert (OrgSettings().statement_timeout, OrgSettings().lock_timeout, OrgSettings().idle_in_transaction_timeout) \
        == (30, 10, 60)
    settings = load_settings({"ROW_BOT_ORG_STATEMENT_TIMEOUT": "5", "ROW_BOT_ORG_LOCK_TIMEOUT": "2",
                              "ROW_BOT_ORG_IDLE_IN_TRANSACTION_TIMEOUT": "90"})
    assert (settings.statement_timeout, settings.lock_timeout, settings.idle_in_transaction_timeout) == (5, 2, 90)
    with pytest.raises(SettingsError, match="ROW_BOT_ORG_LOCK_TIMEOUT must be a number of seconds from 1 to 86400"):
        load_settings({"ROW_BOT_ORG_LOCK_TIMEOUT": "0"})


def test_a_database_url_with_a_nul_in_its_secret_file_is_refused_without_its_value(tmp_path: Path) -> None:
    folder = _secrets(tmp_path, ROW_BOT_ORG_DATABASE_URL=DATABASE_URL.encode() + b"\x00tail")

    with pytest.raises(SettingsError) as raised:
        load_settings({"ROW_BOT_SECRETS_DIR": str(folder)})

    assert raised.value.problems == ["ROW_BOT_ORG_DATABASE_URL must not contain control characters"]
    assert MARKER not in str(raised.value)


def test_the_database_url_can_come_from_the_secrets_folder(tmp_path: Path) -> None:
    folder = _secrets(tmp_path, ROW_BOT_ORG_DATABASE_URL=f"{DATABASE_URL}\n".encode())

    assert load_settings({"ROW_BOT_SECRETS_DIR": str(folder)}).database_url == DATABASE_URL


def test_the_database_urls_never_show_in_the_settings_repr() -> None:
    settings = load_settings({"ROW_BOT_ORG_DATABASE_URL": DATABASE_URL, "ROW_BOT_ORG_MIGRATION_DATABASE_URL": MIGRATION_URL})

    assert MARKER not in repr(settings) and "db.internal" not in str(settings)


@pytest.mark.parametrize("url", [f"mysql://org:{MARKER}@db/org", f"{MARKER}@db/org", "postgresql://"],
                         ids=["other-scheme", "no-scheme", "nothing-after-scheme"])
def test_a_database_url_that_isnt_postgres_is_refused_without_its_value(url: str) -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({"ROW_BOT_ORG_DATABASE_URL": url})

    assert raised.value.problems == ["ROW_BOT_ORG_DATABASE_URL must be a postgresql:// address"]
    assert MARKER not in str(raised.value)


def test_the_secret_variables_leave_the_environment_and_the_rest_stay() -> None:
    environ = {"ROW_BOT_ORG_DATABASE_URL": DATABASE_URL, "ROW_BOT_ORG_MIGRATION_DATABASE_URL": MIGRATION_URL,
               "ROW_BOT_ORG_PORT": "9000", "PATH": "unrelated"}

    remove_secret_variables(environ)

    assert environ == {"ROW_BOT_ORG_PORT": "9000", "PATH": "unrelated"}


def test_the_console_folder_must_hold_a_built_console(tmp_path: Path) -> None:
    with pytest.raises(SettingsError) as refused:
        load_settings({"ROW_BOT_ORG_CONSOLE_DIR": str(tmp_path)})
    assert refused.value.problems == [
        "ROW_BOT_ORG_CONSOLE_DIR must be the folder of the built console (with console.html and "
        "console-not-admin.html)"]
    with pytest.raises(SettingsError):
        load_settings({"ROW_BOT_ORG_CONSOLE_DIR": str(tmp_path / "missing")})


def test_the_console_folder_is_a_setting(tmp_path: Path) -> None:
    for name in ("console.html", "console-not-admin.html"):
        (tmp_path / name).write_text("<!doctype html>", encoding="utf-8")
    assert load_settings({"ROW_BOT_ORG_CONSOLE_DIR": str(tmp_path)}).console_dir == tmp_path.resolve()
    assert load_settings({}).console_dir is None
