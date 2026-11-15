"""Moving between a hosted Row-Bot and the desktop app (U9), through Settings › Data's HTTP API.

Two profiles on scratch folders: a pod (server mode, a known host, the encrypted secret store with
the host's key) and the desktop app (its keychain, a fake one here). A backup made on one restores on
the other, both ways: conversations and settings arrive, and what needs signing in again is named from
the backup's own manifest. A secret never travels in clear: not in the archive, not in the new profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
import pytest

from row_bot import profile_restore
from row_bot import secret_store
from row_bot.application import client_data_backup as data
from tests.subsystem.client_protocol.test_data_backups_api import DOWNLOAD, _command, _pick, _settled, _uploaded
from tests.subsystem.client_protocol.test_protocol_security import _native_proof, bootstrap, client_app

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

KEY_NAME = secret_store.PERSISTENT_SERVER_SECRET_KEY_NAME
POD_KEY = "5e" * 32  # stands for the key a host such as PikaPods generates per pod
LEFT_OUT = ["API keys, sign-in tokens and passwords", "Browser, WhatsApp and device sessions",
            "Caches, logs and downloaded runtimes"]
OPENAI = {"kind": "provider", "name": "openai"}
ANTHROPIC = {"kind": "provider", "name": "anthropic"}
MODULES = ("row_bot.secret_store", "row_bot.providers.config", "row_bot.identity", "row_bot.threads")


class Keychain:
    """The desktop's keychain: it keeps what the desktop app saves there, and nothing else sees it."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@dataclass
class Profile:
    data: Path
    workspace: Path
    hosted: bool
    keychain: Keychain = field(default_factory=Keychain)


@pytest.fixture
def places(tmp_path, monkeypatch, reload_for_data_dir):
    """A pod's data volume (its workspace inside it) and a computer's profile (its workspace in Documents)."""
    run_secrets = tmp_path / "run-secrets"
    run_secrets.mkdir()
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    pod = Profile(tmp_path / "pod" / "data", tmp_path / "pod" / "data" / "workspace", hosted=True)
    desktop = Profile(tmp_path / "computer" / "row-bot", tmp_path / "computer" / "Documents" / "Row-Bot",
                      hosted=False)

    def start(profile: Profile):
        """Row-Bot starts on that profile, as a new process would: its environment and its modules."""
        if profile.hosted:
            monkeypatch.setenv("ROW_BOT_DEPLOYMENT_MODE", "server")
            monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
            monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")
            monkeypatch.setenv("ROW_BOT_SECRETS_DIR", str(run_secrets))
        else:
            monkeypatch.setenv("ROW_BOT_DEPLOYMENT_MODE", "desktop")
            for name in ("ROW_BOT_HOSTED_BY", "ROW_BOT_CONTAINERIZED", "ROW_BOT_SECRETS_DIR", KEY_NAME):
                monkeypatch.delenv(name, raising=False)
        profile.workspace.mkdir(parents=True, exist_ok=True)
        store, config, identity, threads = reload_for_data_dir(profile.data, *MODULES)
        if profile.hosted:
            monkeypatch.setenv(KEY_NAME, POD_KEY)  # the host sets it on every start
            store.prepare_server_store_at_start()
        else:
            store._set_backend_for_tests(profile.keychain)
        monkeypatch.setattr(data, "_workspace", lambda: profile.workspace)
        monkeypatch.setattr(data, "_folder_words", lambda: "Row-Bot › Backups")
        data._JOB.clear()
        data._REVIEWS.clear()
        data.close_uploads()
        from row_bot.providers import auth_store

        return auth_store, identity, threads

    yield pod, desktop, start
    data.close_uploads()


def _use(start, profile: Profile, *, name: str, conversation: str, words: str, provider: str, key: str):
    """Someone uses Row-Bot there: a model key, a name for the assistant, and a conversation."""
    auth_store, identity, threads = start(profile)
    assert auth_store.replace_provider_api_key(provider, key)["configured"] is True
    identity.save_identity_config({"name": name, "personality": "Calm and brief."})
    threads.create_thread(f"{name}'s plans", thread_id=conversation)
    assert threads.append_checkpoint_messages(conversation, [HumanMessage(words), AIMessage("Noted.")])
    threads.conn.close()  # Row-Bot stops: nothing holds its databases open while it moves.


def _everything_in(folder: Path) -> bytes:
    return b"".join(path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file())


def _arrived(start, profile: Profile, *, name: str, conversation: str, words: str):
    """The next start after the restore: the conversation and the settings are there."""
    _auth_store, identity, threads = start(profile)
    assert identity.get_identity_config() == {"name": name, "personality": "Calm and brief."}
    said = [message.content for message in threads.get_latest_checkpoint_messages(conversation)]
    assert said == [words, "Noted."]


def test_a_backup_made_on_a_pod_restores_on_the_desktop(places):
    pod, desktop, start = places
    key = "synthetic-openai-key-pod-0001"
    _use(start, pod, name="Juniper", conversation="moving-to-desktop", words="Pack the blue tent",
         provider="openai", key=key)
    assert key.encode() not in _everything_in(pod.data)  # encrypted on the pod, with the host's key

    # On the pod, in its browser: Move Row-Bot makes a backup and downloads it.
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        assert _command(client, headers, "backup").json()["status"] == "accepted"
        job = _settled(client, headers)["job"]
        assert job["status"] == "completed"
        # What moves, in the backup's own words.
        assert job["left_out"] == LEFT_OUT
        assert OPENAI in job["sign_in_again"]
        archive = client.get(DOWNLOAD, params={"name": job["name"]}, headers=headers).content
    assert key.encode() not in archive and bytes.fromhex(POD_KEY) not in archive and POD_KEY.encode() not in archive
    downloaded = desktop.data.parent / "Downloads" / job["name"]
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(archive)

    # On the computer (a fresh install): the desktop app's Restore from backup picks the downloaded file.
    _auth_store, _identity, threads = start(desktop)
    client, _service, _active = client_app()
    with client:
        proof, headers = _native_proof(client)
        reviewed = _command(client, headers, "inspect_restore", file_grant=_pick(client, proof, downloaded))
        assert reviewed.status_code == 200, reviewed.text
        review = reviewed.json()["review"]
        assert review["left_out"] == LEFT_OUT
        assert review["sign_in_again"] == job["sign_in_again"]
        assert _command(client, headers, "restore", review_id=review["review_id"]).status_code == 200
        assert _settled(client, headers)["pending_restore"]["source_name"] == downloaded.name
    threads.conn.close()  # Row-Bot restarts to apply it, before anything opens a database.
    assert profile_restore.apply_pending(desktop.data)["status"] == "applied"

    _arrived(start, desktop, name="Juniper", conversation="moving-to-desktop", words="Pack the blue tent")
    client, _service, _active = client_app()
    with client:
        _proof, headers = _native_proof(client)
        result = _settled(client, headers)["restore_result"]
    assert result["status"] == "applied"
    assert result["sign_in_again"] == job["sign_in_again"]
    # The key stayed behind: not in the computer's profile, not in its keychain.
    assert key.encode() not in _everything_in(desktop.data)
    assert key not in "".join(desktop.keychain.values.values())


def test_a_backup_made_on_the_desktop_restores_on_a_pod(places):
    pod, desktop, start = places
    key = "synthetic-anthropic-key-desktop-0002"
    _use(start, desktop, name="Basil", conversation="moving-to-pod", words="Book the train",
         provider="anthropic", key=key)
    assert key in desktop.keychain.values.values()  # in the keychain, never in the profile
    assert key.encode() not in _everything_in(desktop.data)

    # On the computer: Back up now saves the archive in the Backups folder.
    client, _service, _active = client_app()
    with client:
        _proof, headers = _native_proof(client)
        assert _command(client, headers, "backup").json()["status"] == "accepted"
        job = _settled(client, headers)["job"]
        assert job["left_out"] == LEFT_OUT
        assert ANTHROPIC in job["sign_in_again"]
    archive = (desktop.workspace / "Backups" / job["name"]).read_bytes()
    assert key.encode() not in archive

    # A pod that already holds someone's start: its browser sends the archive, then confirms.
    _use(start, pod, name="Pod default", conversation="first-pod-chat", words="Hello", provider="openai",
         key="synthetic-openai-key-pod-0003")
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        reviewed = _command(client, headers, "inspect_restore", upload_id=_uploaded(client, headers, archive))
        assert reviewed.status_code == 200, reviewed.text
        review = reviewed.json()["review"]
        assert review["sign_in_again"] == job["sign_in_again"]
        assert _command(client, headers, "restore", review_id=review["review_id"]).status_code == 200
        assert _settled(client, headers)["pending_restore"] is not None
    applied = profile_restore.apply_pending(pod.data)  # the pod restarts to apply it
    assert applied["status"] == "applied"

    _arrived(start, pod, name="Basil", conversation="moving-to-pod", words="Book the train")
    assert key.encode() not in _everything_in(pod.data)
    # The pod's earlier profile is kept aside; its secret store stays with the host's key.
    assert (pod.data / applied["kept_aside"] / "user_config.json").is_file()
    assert (pod.data / secret_store.PERSISTENT_SERVER_SECRET_DIR_NAME).is_dir()
