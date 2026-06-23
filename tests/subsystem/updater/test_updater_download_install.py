from __future__ import annotations

import hashlib
import importlib
import sys
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.subsystem


def _reload_updater(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    sys.modules.pop("row_bot.updater", None)
    import row_bot.updater as updater

    return importlib.reload(updater)


def _info(updater, *, payload: bytes = b"installer", asset_url: str | None = None, sha256: str | None = None):
    return updater.UpdateInfo(
        version="9.0.0",
        channel="stable",
        published_at="",
        notes_md="",
        notes_summary="",
        asset_name="Row-Bot-9.0.0-Windows-x64.exe",
        asset_url=(
            asset_url
            if asset_url is not None
            else "https://github.com/siddsachar/row-bot/releases/download/v9.0.0/Row-Bot.exe"
        ),
        asset_size=len(payload),
        sha256=sha256 if sha256 is not None else hashlib.sha256(payload).hexdigest(),
        html_url="",
        is_prerelease=False,
    )


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_download_update_rejects_missing_manifest_and_non_https_before_network(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("rejected downloads should not open the network"),
    )

    with pytest.raises(updater.UpdateError, match="no downloadable asset"):
        updater.download_update(_info(updater, asset_url=""))
    with pytest.raises(updater.UpdateError, match="missing a SHA256"):
        updater.download_update(_info(updater, sha256=""))
    with pytest.raises(updater.UpdateError, match="non-https"):
        updater.download_update(_info(updater, asset_url="http://github.com/example.exe"))


def test_download_update_streams_progress_verifies_hash_and_finalizes_atomically(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    payload = b"verified installer payload"
    progress: list[tuple[int, int]] = []
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *_args, **_kwargs: _FakeResponse(payload))

    path = updater.download_update(_info(updater, payload=payload), progress=lambda done, total: progress.append((done, total)))

    assert path == updater._DOWNLOAD_DIR / "Row-Bot-9.0.0-Windows-x64.exe"
    assert path.read_bytes() == payload
    assert not path.with_suffix(path.suffix + ".part").exists()
    assert progress[-1] == (len(payload), len(payload))


def test_download_update_removes_partial_file_on_hash_mismatch(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    payload = b"tampered installer payload"
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *_args, **_kwargs: _FakeResponse(payload))

    with pytest.raises(updater.UpdateError, match="SHA256 mismatch"):
        updater.download_update(_info(updater, payload=payload, sha256="0" * 64))

    assert not (updater._DOWNLOAD_DIR / "Row-Bot-9.0.0-Windows-x64.exe.part").exists()
    assert not (updater._DOWNLOAD_DIR / "Row-Bot-9.0.0-Windows-x64.exe").exists()


def test_install_and_restart_uses_mocked_platform_launchers_and_quit_hook(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    installer = tmp_path / "Row-Bot-test.dmg"
    installer.write_bytes(b"installer")
    calls: list[object] = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append((cmd, kwargs))

    monkeypatch.setattr(updater.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(updater, "verify_os_signature", lambda _path: (True, "ok"))
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    monkeypatch.setitem(sys.modules, "row_bot.launcher", SimpleNamespace(quit_for_update=lambda: calls.append("quit")))

    updater.install_and_restart(installer)

    assert calls[0][0] == ["open", str(installer)]
    assert calls[1] == "quit"


def test_install_and_restart_rejects_missing_bad_signature_and_unsupported_platform(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    installer = tmp_path / "Row-Bot-test.exe"
    installer.write_bytes(b"installer")

    with pytest.raises(updater.UpdateError, match="Installer not found"):
        updater.install_and_restart(tmp_path / "missing.exe")

    monkeypatch.setattr(updater, "verify_os_signature", lambda _path: (False, "bad signature"))
    with pytest.raises(updater.UpdateError, match="Signature verification failed"):
        updater.install_and_restart(installer)

    monkeypatch.setattr(updater, "verify_os_signature", lambda _path: (True, "ok"))
    monkeypatch.setattr(updater.platform, "system", lambda: "Plan9")
    with pytest.raises(updater.UpdateError, match="Unsupported platform"):
        updater.install_and_restart(installer)


def test_signature_verification_is_mockable_and_non_destructive(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    installer = tmp_path / "Row-Bot-test.exe"
    installer.write_bytes(b"installer")
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)

    ok, message = updater.verify_os_signature(installer)

    assert ok is True
    assert "SHA256" in message
