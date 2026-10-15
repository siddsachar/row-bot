"""Workspace paths for video edits and images (F3, F5): links below the workspace are refused, while
cloud-file placeholders (OneDrive's "online only" files and folders, which are also reparse points
on Windows) are not mistaken for links. Stat results are faked; nothing needs Windows.
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot.media_sources import SourceError, workspace_file, workspace_target

pytestmark = pytest.mark.subsystem

CLOUD_FILE = 0x9000701A  # A cloud-file placeholder's reparse tag.
JUNCTION = 0xA0000003
SYMBOLIC_LINK = 0xA000000C


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    folder = tmp_path / "workspace"
    (folder / "clips").mkdir(parents=True)
    (folder / "clips" / "demo.mp4").write_bytes(b"video")
    return folder


def _tag(monkeypatch, name: str, tag: int) -> None:
    real = Path.lstat

    def lstat(self, *args, **kwargs):
        info = real(self, *args, **kwargs)
        if self.name != name:
            return info
        return SimpleNamespace(st_mode=info.st_mode, st_reparse_tag=tag, st_size=info.st_size)

    monkeypatch.setattr(Path, "lstat", lstat)


def test_a_cloud_placeholder_folder_is_read_and_written_like_any_folder(root, monkeypatch):
    _tag(monkeypatch, "clips", CLOUD_FILE)

    assert workspace_file(root, "clips/demo.mp4") == root / "clips" / "demo.mp4"
    assert workspace_target(root, "clips/out.mp4") == (root / "clips" / "out.mp4", "clips/out.mp4", False)


@pytest.mark.parametrize("tag", [JUNCTION, SYMBOLIC_LINK])
def test_a_junction_or_symbolic_link_below_the_workspace_is_refused(root, monkeypatch, tag):
    _tag(monkeypatch, "clips", tag)

    with pytest.raises(SourceError, match="go through links"):
        workspace_file(root, "clips/demo.mp4")
    with pytest.raises(SourceError, match="goes through a link"):
        workspace_target(root, "clips/out.mp4")


def test_a_posix_symbolic_link_is_refused_too(root, monkeypatch):
    real = Path.lstat

    def lstat(self, *args, **kwargs):
        info = real(self, *args, **kwargs)
        return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_size=0) if self.name == "clips" else info

    monkeypatch.setattr(Path, "lstat", lstat)

    with pytest.raises(SourceError, match="go through links"):
        workspace_file(root, "clips/demo.mp4")
