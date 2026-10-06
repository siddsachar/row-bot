"""Google and X sign-ins move from files into the system keychain once, and only after a verified copy.

Fakes only: an in-memory keychain, synthetic tokens, an isolated data folder."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
from typing import Any

import pytest

from row_bot import account_tokens, secret_store
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

_GMAIL = {"token": "gmail-access", "refresh_token": "fixture-refresh", "client_id": "c", "client_secret": "s",
          "token_uri": "https://oauth2.googleapis.com/token", "expiry": "2099-01-02T00:00:00Z"}
_CALENDAR = {**_GMAIL, "token": "calendar-access", "expiry": "2099-01-01T00:00:00Z"}  # Refreshed earlier.
_X = {"access_token": "x-access", "refresh_token": "x-refresh", "expires_at": 4070908800.0}
_CLIENT = {"installed": {"client_id": "fixture-client", "client_secret": "fixture-secret"}}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(root))
    for relative, value in (("gmail/token.json", _GMAIL), ("calendar/token.json", _CALENDAR),
                            ("x/token.json", _X), ("gmail/credentials.json", _CLIENT)):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(json.dumps(value), encoding="utf-8")
    return root


@pytest.fixture
def keychain():
    keyring = MemoryKeyring()
    secret_store._set_backend_for_tests(keyring)
    account_tokens._RETRY_AT.clear()
    yield keyring
    secret_store._set_backend_for_tests(None)
    account_tokens._RETRY_AT.clear()


def _token_files(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.json"))


def _plaintext_left(root, *values):
    """Any file under the data folder still holding one of the secrets."""
    return [path for path in root.rglob("*") if path.is_file()
            and any(value in path.read_text(encoding="utf-8", errors="ignore") for value in values)]


def test_every_sign_in_moves_into_the_keychain_and_no_plaintext_is_left(profile, keychain):
    assert account_tokens.migrate() == {"migrated": 3, "kept": 0}
    assert _token_files(profile) == []
    assert not _plaintext_left(profile, "gmail-access", "calendar-access", "fixture-refresh", "x-access",
                               "fixture-secret")
    # Gmail and Calendar each refreshed their own copy: the one whose access lasts longest is kept.
    assert account_tokens.read("google") == _GMAIL
    assert account_tokens.read("x") == _X and account_tokens.read("google_client") == _CLIENT
    stored = {name.split(":", 1)[1]: value for (_, name), value in keychain.values.items() if name.startswith("accounts:")}
    assert set(stored) == {"google", "x", "google_client"}
    assert account_tokens.migrate() == {"migrated": 0, "kept": 0}  # Once.


def test_without_a_keychain_nothing_is_deleted_and_sign_ins_keep_working(profile, keychain):
    keychain.fail = True
    assert account_tokens.migrate() == {"migrated": 0, "kept": 3}
    assert _token_files(profile) == ["calendar/token.json", "gmail/credentials.json", "gmail/token.json", "x/token.json"]
    assert account_tokens.read("x") == _X  # The old file still works, read-only.
    with pytest.raises(account_tokens.AccountTokenError):
        account_tokens.write("x", {**_X, "access_token": "x-refreshed"})  # Never written to a file instead.
    assert json.loads((profile / "x/token.json").read_text(encoding="utf-8")) == _X
    keychain.fail = False
    assert account_tokens.migrate()["migrated"] == 3  # Retried later, as at the next start.
    assert _token_files(profile) == []


def test_the_keychain_sign_in_wins_over_a_file_left_behind(profile, keychain):
    """A file whose removal failed never brings an older sign-in back over this version's own."""
    newer = {**_GMAIL, "token": "keychain-access", "expiry": "2030-01-01T00:00:00Z"}
    secret_store.set_secret("google", json.dumps(newer), namespace="accounts")
    account_tokens.migrate()
    assert account_tokens.read("google") == newer  # Even though the files' access lasts longer.
    assert not (profile / "gmail/token.json").exists() and not (profile / "calendar/token.json").exists()


def test_different_sign_ins_in_old_files_are_not_merged(profile, keychain):
    other = {**_CALENDAR, "refresh_token": "another-account"}
    (profile / "calendar/token.json").write_text(json.dumps(other), encoding="utf-8")
    account_tokens.migrate()
    assert account_tokens.read("google") == _GMAIL  # Gmail's own sign-in, not the other account's.
    assert not _plaintext_left(profile, "another-account", "fixture-refresh")


def test_reads_do_not_retry_a_failed_keychain_copy_every_time(profile, keychain, monkeypatch):
    attempts = []
    original = secret_store.set_secret
    monkeypatch.setattr(secret_store, "set_secret", lambda *a, **k: attempts.append(a[0]) or original(*a, **k))
    keychain.fail = True
    for _ in range(3):
        assert account_tokens.read("x") == _X  # The old file keeps working meanwhile.
    assert attempts == ["x"]
    monkeypatch.setattr(account_tokens.time, "monotonic", lambda: 10**9)  # Some minutes later.
    keychain.fail = False
    assert account_tokens.read("x") == _X and not (profile / "x/token.json").exists()


def test_a_copy_that_does_not_read_back_the_same_keeps_the_file(profile, keychain, monkeypatch):
    real = keychain.get_password
    monkeypatch.setattr(keychain, "get_password", lambda service, name: "{}" if name == "accounts:x" else real(service, name))
    account_tokens.migrate()
    assert (profile / "x/token.json").is_file()  # Not verified: not deleted.
    assert not (profile / "gmail/token.json").exists() and not (profile / "calendar/token.json").exists()


def test_an_unreadable_file_is_left_alone_and_not_used(profile, keychain):
    (profile / "x/token.json").write_text("{not json", encoding="utf-8")
    account_tokens.migrate()
    assert (profile / "x/token.json").read_text(encoding="utf-8") == "{not json"
    assert account_tokens.read("x") is None


def test_a_new_sign_in_goes_only_to_the_keychain_and_clears_a_stale_file(profile, keychain):
    keychain.fail = True
    account_tokens.migrate()  # Keychain unavailable at start: files stay.
    keychain.fail = False
    account_tokens.write("google", {**_GMAIL, "token": "newest"})
    assert not (profile / "gmail/token.json").exists() and not (profile / "calendar/token.json").exists()
    assert account_tokens.read("google")["token"] == "newest"
    account_tokens.delete("google")
    assert account_tokens.read("google") is None and account_tokens.state("google") == "not_authenticated"


def test_a_client_file_the_person_keeps_elsewhere_is_read_where_it_is_and_never_moved(profile, keychain, tmp_path):
    own = tmp_path / "elsewhere" / "client_secret.json"
    own.parent.mkdir()
    own.write_text(json.dumps(_CLIENT), encoding="utf-8")
    (profile / "gmail/credentials.json").unlink()
    (profile / "tools_config.json").write_text(json.dumps({"tool_configs": {"gmail": {"credentials_path": str(own)}}}),
                                               encoding="utf-8")
    account_tokens.migrate()
    assert account_tokens.google_client() == _CLIENT
    assert own.is_file()  # Theirs: read, never deleted or copied.
    assert account_tokens.read("google_client") is None


def test_concurrent_token_refresh_is_single_flight_and_keychain_only(tmp_path, monkeypatch) -> None:
    """Two requests that find Google's access run out refresh it once; the new token goes back to the
    keychain, never to a file."""
    from google.auth.transport import requests as google_requests
    from google.oauth2.credentials import Credentials

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    secret_store._set_backend_for_tests(MemoryKeyring())
    monkeypatch.setattr(secret_store, "_backend_override", secret_store._backend_override)
    account_tokens.write("google", {"valid": False, "refresh_token": "refresh-token"})
    initial_load_barrier = threading.Barrier(2)
    calls_guard = threading.Lock()
    loader_calls = 0
    refresh_calls = 0

    class FakeCredentials:
        def __init__(self, valid: bool) -> None:
            self.valid = valid
            self.refresh_token = "refresh-token"

        def refresh(self, _request: Any) -> None:
            nonlocal refresh_calls
            with calls_guard:
                refresh_calls += 1
            self.valid = True

        def to_json(self) -> str:
            return '{"valid": true, "refresh_token": "refresh-token"}'

    def load_credentials(info: dict, *_scopes: Any) -> FakeCredentials:
        nonlocal loader_calls
        with calls_guard:
            loader_calls += 1
            call_number = loader_calls
        if call_number <= 2:
            initial_load_barrier.wait(timeout=2)
        return FakeCredentials(info.get("valid") is True)

    monkeypatch.setattr(Credentials, "from_authorized_user_info", staticmethod(load_credentials))
    monkeypatch.setattr(google_requests, "Request", lambda: object())
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            credentials = list(executor.map(lambda _index: account_tokens.google_credentials(), range(2)))
        assert all(credential.valid for credential in credentials)
        assert refresh_calls == 1
        assert account_tokens.read("google") == {"valid": True, "refresh_token": "refresh-token"}
        assert not [path for path in tmp_path.rglob("*") if path.is_file() and "refresh-token" in path.read_text(
            encoding="utf-8", errors="ignore")]
    finally:
        secret_store._set_backend_for_tests(None)
