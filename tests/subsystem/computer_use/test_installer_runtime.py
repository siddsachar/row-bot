from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from row_bot.mcp_client import requirements


def _zip(path: Path, members: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pinned_installer_verifies_hash_layout_and_writes_manifest_atomically(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    sha = _zip(archive, {"bundle/cua-driver.exe": b"reviewed-binary", "bundle/LICENSE": b"MIT"})
    runtimes = tmp_path / "runtimes"
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", runtimes)
    monkeypatch.setattr(requirements, "_download", lambda _url, destination, _progress=None: shutil.copyfile(archive, destination))
    result = requirements.install_pinned_archive_runtime(
        "cua-driver", version="0.7.1", url="https://example.invalid/pinned.zip", sha256=sha,
        asset_name="pinned.zip", executable_candidates=("cua-driver.exe",),
    )
    manifest = json.loads((runtimes / "cua-driver" / "manifest.json").read_text(encoding="utf-8"))
    assert result.ok is True
    assert manifest["archive_sha256"] == sha
    assert Path(manifest["executable_path"]).read_bytes() == b"reviewed-binary"
    assert not list((runtimes / "cua-driver").glob("manifest.json.*.tmp"))


def test_corrupt_download_never_activates(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    _zip(archive, {"bundle/cua-driver.exe": b"tampered"})
    runtimes = tmp_path / "runtimes"
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", runtimes)
    monkeypatch.setattr(requirements, "_download", lambda _url, destination, _progress=None: shutil.copyfile(archive, destination))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        requirements.install_pinned_archive_runtime(
            "cua-driver", version="0.7.1", url="https://example.invalid/pinned.zip", sha256="0" * 64,
            asset_name="pinned.zip", executable_candidates=("cua-driver.exe",),
        )
    assert not (runtimes / "cua-driver" / "manifest.json").exists()
    assert not (runtimes / "cua-driver" / "0.7.1").exists()


def test_safe_extraction_rejects_traversal_and_symlinks(tmp_path) -> None:
    bad_zip = tmp_path / "bad.zip"
    _zip(bad_zip, {"../escape.exe": b"bad"})
    with pytest.raises(RuntimeError, match="unsafe path"):
        requirements._extract_archive(bad_zip, tmp_path / "zip-out")

    bad_tar = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_tar, "w:gz") as archive:
        link = tarfile.TarInfo("cua-driver")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../escape"
        archive.addfile(link, io.BytesIO())
    with pytest.raises(RuntimeError, match="special entry"):
        requirements._extract_archive(bad_tar, tmp_path / "tar-out")


def test_cancel_after_download_leaves_no_active_runtime(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    sha = _zip(archive, {"bundle/cua-driver.exe": b"reviewed"})
    runtimes = tmp_path / "runtimes"
    cancelled = {"value": False}
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", runtimes)
    def _download(_url, destination, _progress=None):
        shutil.copyfile(archive, destination)
        cancelled["value"] = True
    monkeypatch.setattr(requirements, "_download", _download)
    result = requirements.install_pinned_archive_runtime(
        "cua-driver", version="0.7.1", url="https://example.invalid/pinned.zip", sha256=sha,
        asset_name="pinned.zip", executable_candidates=("cua-driver.exe",), cancelled=lambda: cancelled["value"],
    )
    assert result.ok is False
    assert not (runtimes / "cua-driver" / "manifest.json").exists()


def test_upgrade_retains_known_good_until_doctor_then_supports_rollback_or_finalize(tmp_path, monkeypatch) -> None:
    old_archive = tmp_path / "old.zip"
    new_archive = tmp_path / "new.zip"
    old_sha = _zip(old_archive, {"bundle/cua-driver.exe": b"old-known-good"})
    new_sha = _zip(new_archive, {"bundle/cua-driver.exe": b"new-candidate"})
    runtimes = tmp_path / "runtimes"
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", runtimes)

    def _download(url, destination, _progress=None):
        shutil.copyfile(new_archive if url.endswith("new.zip") else old_archive, destination)

    monkeypatch.setattr(requirements, "_download", _download)
    requirements.install_pinned_archive_runtime(
        "cua-driver", version="0.7.0", url="https://example.invalid/old.zip", sha256=old_sha,
        asset_name="old.zip", executable_candidates=("cua-driver.exe",),
    )
    old_manifest = requirements._read_manifest("cua-driver")
    old_manifest["doctor_ok"] = True
    requirements._write_manifest("cua-driver", old_manifest)

    requirements.install_pinned_archive_runtime(
        "cua-driver", version="0.7.1", url="https://example.invalid/new.zip", sha256=new_sha,
        asset_name="new.zip", executable_candidates=("cua-driver.exe",),
    )
    candidate = requirements._read_manifest("cua-driver")
    assert candidate["previous_manifest"]["version"] == "0.7.0"
    assert (runtimes / "cua-driver" / "0.7.0").exists()
    assert (runtimes / "cua-driver" / "0.7.1").exists()
    assert requirements.rollback_pinned_archive_runtime("cua-driver") is True
    assert requirements._read_manifest("cua-driver")["version"] == "0.7.0"
    assert not (runtimes / "cua-driver" / "0.7.1").exists()

    requirements.install_pinned_archive_runtime(
        "cua-driver", version="0.7.1", url="https://example.invalid/new.zip", sha256=new_sha,
        asset_name="new.zip", executable_candidates=("cua-driver.exe",),
    )
    assert requirements.finalize_pinned_archive_runtime("cua-driver") is False
    candidate = requirements._read_manifest("cua-driver")
    candidate["doctor_ok"] = True
    requirements._write_manifest("cua-driver", candidate)
    assert requirements.finalize_pinned_archive_runtime("cua-driver") is True
    assert (runtimes / "cua-driver" / "0.7.1").exists()
    assert not (runtimes / "cua-driver" / "0.7.0").exists()
    assert "previous_manifest" not in requirements._read_manifest("cua-driver")


def test_uninstall_is_scoped_to_managed_cua_runtime(tmp_path, monkeypatch) -> None:
    from row_bot.computer_use import readiness as cua_readiness

    runtimes = tmp_path / "runtimes"
    managed = runtimes / "cua-driver"
    managed.mkdir(parents=True)
    (managed / "manifest.json").write_text("{}", encoding="utf-8")
    sibling = runtimes / "playwright-browsers"
    sibling.mkdir()
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", runtimes)
    assert cua_readiness.uninstall_cua_runtime() is True
    assert not managed.exists()
    assert sibling.exists()
