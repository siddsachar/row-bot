"""The Org Compose deployment's secrets helper (deploy/org/make_secrets.py).

It writes every secret file or none, never over an existing one; each database address carries the
password the database is made with; and no value is weak or empty, except the identity provider's client
secret, which the admin pastes in.
"""
from __future__ import annotations

import base64
import importlib.util
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.installer, pytest.mark.platform]

HELPER = Path(__file__).resolve().parents[3] / "deploy" / "org" / "make_secrets.py"
URL_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@pytest.fixture(scope="module")
def helper():
    spec = importlib.util.spec_from_file_location("org_make_secrets", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(folder: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in folder.iterdir()}


def test_the_addresses_carry_the_passwords_the_database_is_made_with(helper, tmp_path: Path) -> None:
    assert helper.main(["--dir", str(tmp_path / "secrets")]) == 0
    files = _read(tmp_path / "secrets")

    for url_file, role, password_file in (("ROW_BOT_ORG_MIGRATION_DATABASE_URL", "row_bot_org_owner", "owner_password"),
                                          ("ROW_BOT_ORG_DATABASE_URL", "row_bot_org_app", "app_password")):
        address = urlsplit(files[url_file])
        assert address.scheme == "postgresql"
        assert (address.username, address.password) == (role, files[password_file])
        assert (address.hostname, address.port, address.path) == ("postgres", 5432, "/row_bot_org")


def test_no_value_is_weak_or_empty_but_the_client_secret_the_admin_pastes(helper, tmp_path: Path) -> None:
    helper.main(["--dir", str(tmp_path / "one")])
    helper.main(["--dir", str(tmp_path / "two")])
    one, two = _read(tmp_path / "one"), _read(tmp_path / "two")

    assert one.pop("ROW_BOT_ORG_IDP_CLIENT_SECRET") == ""
    passwords = [one[name] for name in ("postgres_password", "owner_password", "app_password")]
    assert all(len(password) >= 40 and set(password) <= URL_SAFE for password in passwords)
    assert len(set(passwords)) == 3
    assert len(one["ROW_BOT_ORG_HMAC_PEPPER"]) >= 32
    assert len(base64.urlsafe_b64decode(one["ROW_BOT_ORG_GATEWAY_KEY"])) == 32
    assert all("\n" not in value for value in one.values())
    # Each run draws new values.
    assert not {name for name, value in one.items() if two[name] == value}


def test_it_never_writes_over_a_secret_file_and_then_writes_nothing(helper, tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    folder = tmp_path / "secrets"
    folder.mkdir()
    (folder / "app_password").write_text("in use", encoding="utf-8")

    assert helper.main(["--dir", str(folder)]) == 1
    assert _read(folder) == {"app_password": "in use"}
    assert "app_password" in capsys.readouterr().err

    other = tmp_path / "other"
    assert helper.main(["--dir", str(other)]) == 0
    before = _read(other)
    assert helper.main(["--dir", str(other)]) == 1
    assert _read(other) == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_folder_is_its_owners_alone_and_the_containers_can_read_the_files(helper, tmp_path: Path) -> None:
    folder = tmp_path / "secrets"
    helper.main(["--dir", str(folder)])

    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert {stat.S_IMODE(path.stat().st_mode) for path in folder.iterdir()} == {0o644}
