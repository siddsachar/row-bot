"""Read-only Git inspection refuses only filters a repository actually uses."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from row_bot.developer import review

pytestmark = [
    pytest.mark.subsystem,
    pytest.mark.skipif(shutil.which("git") is None, reason="Git unavailable"),
]


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """An isolated Git config with a globally installed filter (like Git LFS)."""
    home = tmp_path / "home"
    (home / ".config" / "git").mkdir(parents=True)
    config = home / "gitconfig"
    config.write_text(
        '[filter "lfs"]\n'
        "\tclean = must-never-execute clean -- %f\n"
        "\tsmudge = must-never-execute smudge -- %f\n"
        "\tprocess = must-never-execute filter-process\n"
        "\trequired = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    return home


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True,
                   capture_output=True, timeout=10)
    (root / "app.py").write_text("print('fixture')\n", encoding="utf-8")
    return root


def test_a_global_filter_that_no_attribute_selects_does_not_block_inspection(git_env, tmp_path):
    root = _repo(tmp_path)
    assert review.workspace_has_custom_read_hooks(str(root)) is False


@pytest.mark.parametrize(
    "place",
    ["root", "nested", "untracked-nested", "info", "global", "macro"],
)
def test_a_filter_selected_by_any_attribute_source_blocks_inspection(git_env, tmp_path, place):
    root = _repo(tmp_path)
    line = "*.bin filter=lfs diff=lfs merge=lfs -text\n"
    if place == "root":
        (root / ".gitattributes").write_text(line, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", ".gitattributes"], check=True,
                       capture_output=True, timeout=10)
    elif place in {"nested", "untracked-nested"}:
        nested = root / "assets" / "images"
        nested.mkdir(parents=True)
        (nested / ".gitattributes").write_text(line, encoding="utf-8")
        if place == "nested":
            subprocess.run(["git", "-C", str(root), "add", "assets"], check=True,
                           capture_output=True, timeout=10)
    elif place == "info":
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "attributes").write_text(line, encoding="utf-8")
    elif place == "global":
        (git_env / ".config" / "git" / "attributes").write_text(line, encoding="utf-8")
    else:
        (root / ".gitattributes").write_text("[attr]large filter=lfs -text\n*.bin large\n",
                                             encoding="utf-8")
    assert review.workspace_has_custom_read_hooks(str(root)) is True


def test_an_attribute_naming_an_unconfigured_driver_does_not_block(git_env, tmp_path):
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("*.txt filter=unknown-driver\n", encoding="utf-8")
    assert review.workspace_has_custom_read_hooks(str(root)) is False


def test_fsmonitor_and_plain_folders_keep_their_rules(git_env, tmp_path):
    root = _repo(tmp_path)
    subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", "must-never-execute"],
                   check=True, capture_output=True, timeout=10)
    assert review.workspace_has_custom_read_hooks(str(root)) is True
    plain = tmp_path / "plain"
    plain.mkdir()
    assert review.workspace_has_custom_read_hooks(str(plain)) is False


def test_an_oversized_attributes_file_counts_as_selecting_a_filter(git_env, tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("*.md text\n" * 10, encoding="utf-8")
    monkeypatch.setattr(review, "_MAX_ATTRIBUTE_FILE", 16)
    assert review.workspace_has_custom_read_hooks(str(root)) is True


def test_inspection_reads_a_repository_with_an_unused_global_filter(git_env, tmp_path):
    root = _repo(tmp_path)
    diff = review.read_bounded_diff(str(root), "app.py")
    assert diff.status in {"text", "missing"}
    assert diff.status != "unavailable"
