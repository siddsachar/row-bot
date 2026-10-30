"""Where a server's secret-store key comes from, and what each start does with it (H2).

In order: the host's ``ROW_BOT_SECRET_STORE_KEY`` (taken out of the environment), the key file in
``ROW_BOT_SECRETS_DIR`` (Compose), or a key a container makes on its data volume when it has
neither. A computer keeps its secrets in its keychain and never gets a key file.
"""
from __future__ import annotations

import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot import profile_restore
from row_bot import secret_store
from row_bot.application import profile_backup

pytestmark = pytest.mark.platform

KEY_NAME = secret_store.PERSISTENT_SERVER_SECRET_KEY_NAME
HOST_KEY = "5e" * 32  # stands for the value a host such as PikaPods generates per pod
OTHER_KEY = "a7" * 32
NAMESPACE = "providers:codex"
SECRET = "fake-refresh-token-for-testing"


@pytest.fixture
def pod(tmp_path, monkeypatch, reload_for_data_dir):
    """The published image on one fresh volume: server mode in a container, its empty /run/secrets."""
    data = tmp_path / "data"
    run_secrets = tmp_path / "run-secrets"
    run_secrets.mkdir()
    monkeypatch.setenv("ROW_BOT_DEPLOYMENT_MODE", "server")
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")
    monkeypatch.setenv("ROW_BOT_SECRETS_DIR", str(run_secrets))
    monkeypatch.delenv(KEY_NAME, raising=False)

    def restart(env_key: str | None = None):
        """A new process's secret store; the host sets its variable again (or doesn't)."""
        (module,) = reload_for_data_dir(data, "row_bot.secret_store")
        if env_key is None:
            monkeypatch.delenv(KEY_NAME, raising=False)
        else:
            monkeypatch.setenv(KEY_NAME, env_key)
        return module

    return SimpleNamespace(
        data=data,
        run_secrets=run_secrets,
        records=data / secret_store.PERSISTENT_SERVER_SECRET_DIR_NAME,
        own_key=data / secret_store.DATA_VOLUME_KEY_DIR_NAME / KEY_NAME,
        restart=restart,
    )


def _at_rest(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def test_the_hosts_key_opens_saved_secrets_after_a_restart_and_never_lands_on_the_volume(pod) -> None:
    store = pod.restart(HOST_KEY)
    store.prepare_server_store_at_start()

    assert KEY_NAME not in os.environ
    assert store.set_secret("refresh_token", SECRET, namespace=NAMESPACE) == "encrypted_file"

    store = pod.restart(HOST_KEY)
    store.prepare_server_store_at_start()

    assert store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET
    assert not pod.own_key.exists()
    at_rest = _at_rest(pod.data)
    assert at_rest and all(path.startswith(secret_store.PERSISTENT_SERVER_SECRET_DIR_NAME) for path in at_rest)
    for content in at_rest.values():
        assert HOST_KEY.encode() not in content and bytes.fromhex(HOST_KEY) not in content
        assert SECRET.encode() not in content


def test_a_container_without_a_key_makes_its_own_on_the_volume_and_keeps_it(pod) -> None:
    store = pod.restart()
    store.prepare_server_store_at_start()
    made = pod.own_key.read_bytes()

    assert store.set_secret("refresh_token", SECRET, namespace=NAMESPACE) == "encrypted_file"
    # Beside the records, never among them.
    assert pod.records not in pod.own_key.parents
    if os.name != "nt":
        assert pod.own_key.stat().st_mode & 0o777 == 0o400
        assert pod.own_key.parent.stat().st_mode & 0o777 == 0o700

    store = pod.restart()
    store.prepare_server_store_at_start()

    assert pod.own_key.read_bytes() == made
    assert store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET


def test_a_compose_key_still_wins_and_row_bot_makes_no_key_of_its_own(pod) -> None:
    # What Compose's secret-store-init service makes in the row_bot_secrets volume.
    secret_store.initialize_persistent_server_secret_store(pod.run_secrets)
    store = pod.restart()
    # Saved by an earlier version, before any start checked the key.
    assert store.set_secret("refresh_token", SECRET, namespace=NAMESPACE) == "encrypted_file"

    store.prepare_server_store_at_start()
    store = pod.restart()
    store.prepare_server_store_at_start()

    assert store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET
    assert not pod.own_key.parent.exists()


def test_a_key_that_doesnt_open_the_saved_secrets_stops_the_start_and_deletes_nothing(pod, caplog) -> None:
    store = pod.restart()
    store.prepare_server_store_at_start()  # no key from the host yet: Row-Bot made its own
    store.set_secret("refresh_token", SECRET, namespace=NAMESPACE)
    own = pod.own_key.read_text(encoding="ascii")
    before = _at_rest(pod.data)

    # The host starts setting a different key.
    store = pod.restart(OTHER_KEY)
    with pytest.raises(store.SecretStoreError) as refused:
        store.prepare_server_store_at_start()

    message = str(refused.value)
    assert f"They were saved with the key Row-Bot made in {pod.own_key}" in message
    assert f"remove {KEY_NAME}" in message and "Nothing was deleted." in message
    assert OTHER_KEY not in message and own not in message
    assert _at_rest(pod.data) == before

    # The owner sets the variable to the file's contents: the secrets open, and the file can go.
    store = pod.restart(own)
    with caplog.at_level(logging.WARNING, logger="row_bot.secret_store"):
        store.prepare_server_store_at_start()

    assert store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET
    assert f"{pod.own_key} is no longer used" in caplog.text
    assert own not in caplog.text


def test_saved_secrets_without_their_key_never_get_a_new_one(pod) -> None:
    store = pod.restart(HOST_KEY)
    store.prepare_server_store_at_start()
    store.set_secret("refresh_token", SECRET, namespace=NAMESPACE)
    before = _at_rest(pod.data)

    # The host's variable is gone: a key made now would never open them.
    store = pod.restart()
    with pytest.raises(store.SecretStoreError) as refused:
        store.prepare_server_store_at_start()

    assert "need the secret-store key they were saved with" in str(refused.value)
    assert "Nothing was deleted." in str(refused.value)
    assert not pod.own_key.exists()
    assert _at_rest(pod.data) == before


def test_a_containers_key_never_rides_along_in_a_backup_and_a_restore_keeps_it_with_the_records(pod) -> None:
    store = pod.restart()
    store.prepare_server_store_at_start()
    store.set_secret("refresh_token", SECRET, namespace=NAMESPACE)
    (pod.data / "tools_config.json").write_text("{}", encoding="utf-8")
    own = pod.own_key.read_bytes()

    made = profile_backup.create_backup(pod.data, pod.data.parent / "Backups", now=datetime(2026, 10, 30, 9, 0))
    with zipfile.ZipFile(made["path"]) as archive:
        names = archive.namelist()
        held = b"".join(archive.read(name) for name in names)
    assert not [name for name in names if name.startswith(("secret-store-key", "secure-secrets"))]
    assert own not in held and bytes.fromhex(own.decode()) not in held

    profile_backup.stage_restore(Path(made["path"]), pod.data, source_name="backup.zip")
    assert profile_restore.apply_pending(pod.data)["status"] == "applied"
    store = pod.restart()
    store.prepare_server_store_at_start()

    assert pod.own_key.read_bytes() == own
    assert store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET


def test_a_computer_keeps_its_keychain_and_never_gets_a_key_file(tmp_path, monkeypatch) -> None:
    class Keychain:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], str] = {}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            self.values[service, account] = value

        def delete_password(self, service, account):
            self.values.pop((service, account), None)

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    monkeypatch.setenv("ROW_BOT_DEPLOYMENT_MODE", "desktop")
    monkeypatch.delenv("ROW_BOT_CONTAINERIZED", raising=False)
    monkeypatch.delenv("ROW_BOT_SECRETS_DIR", raising=False)
    keychain = Keychain()
    secret_store._set_backend_for_tests(keychain)
    try:
        secret_store.take_environment_key()
        # Even `row-bot serve` on a computer: its keychain keeps the secrets.
        secret_store.prepare_server_store_at_start()
        assert secret_store.set_secret("refresh_token", SECRET, namespace=NAMESPACE) == "keyring"
        assert secret_store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET
    finally:
        secret_store._set_backend_for_tests(None)

    assert SECRET in keychain.values.values()
    assert list(data.rglob("*")) == []


def test_memory_only_secrets_never_use_a_key_or_touch_the_disk(pod, monkeypatch) -> None:
    # Every key source is there: the host's variable and a Compose key file.
    secret_store.initialize_persistent_server_secret_store(pod.run_secrets)
    store = pod.restart(HOST_KEY)
    pod.data.mkdir()
    store.use_memory_backend()
    store.take_environment_key()
    store.prepare_server_store_at_start()
    changes = store.change_count()

    assert store.set_secret("refresh_token", SECRET, namespace=NAMESPACE) == "keyring"
    assert store.get_secret("refresh_token", namespace=NAMESPACE) == SECRET
    assert store.change_count() == changes + 1
    assert store.persistent_server_store_configured() is False
    assert list(pod.data.rglob("*")) == []
