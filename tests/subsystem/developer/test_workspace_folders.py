"""The workspace root's folder browser (F19): folders only, never anything outside the root.

A browser or phone attaches a code folder by clicking through the folders inside Row-Bot's
workspace root. Every listing and every choice is checked on the server: ``..``, absolute
paths, drive letters, odd names, links and Windows junctions that lead out are refused, and a
choice registers through the same function the desktop picker's choice does.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from row_bot.developer import workspace_folders
from row_bot.developer.workspace_folders import FolderRefused

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def root(tmp_path, monkeypatch):
    workspace = (tmp_path / "Row-Bot").resolve()
    (workspace / "Projects" / "tide-app" / ".git").mkdir(parents=True)
    (workspace / "Projects" / "notes").mkdir()
    (workspace / "Drafts").mkdir()
    (workspace / ".cache").mkdir()
    (workspace / "readme.txt").write_text("not a folder", encoding="utf-8")
    (workspace / "Projects" / "tide-app" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(workspace_folders, "workspace_root", lambda: workspace)
    return workspace


def _outside(tmp_path: Path) -> Path:
    secret = tmp_path / "outside"
    (secret / "private").mkdir(parents=True, exist_ok=True)
    return secret


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this computer can't make symbolic links")


def _junction(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def test_the_root_lists_its_folders_only_sorted_without_files_hidden_folders_or_host_paths(root):
    page = workspace_folders.browse("")
    assert page["is_root"] and page["path"] == "" and page["name"] == "Row-Bot"
    assert [folder["name"] for folder in page["folders"]] == ["Drafts", "Projects"]
    assert page["crumbs"] == [{"name": "Row-Bot", "path": ""}]
    inner = workspace_folders.browse("Projects")
    assert inner["folders"] == [{"name": "notes", "path": "Projects/notes", "is_git": False},
                                {"name": "tide-app", "path": "Projects/tide-app", "is_git": True}]
    assert inner["crumbs"] == [{"name": "Row-Bot", "path": ""}, {"name": "Projects", "path": "Projects"}]
    assert str(root) not in repr(inner)


def test_an_empty_folder_lists_nothing(root):
    page = workspace_folders.browse("Projects/notes")
    assert page["folders"] == [] and not page["truncated"]


@pytest.mark.parametrize("path", [
    "..", "../", "Projects/..", "Projects/../..", ".", "./Projects", "/", "/etc", "\\Windows", "C:", "C:/",
    "C:\\Users", "Projects\\tide-app", "Projects//tide-app", "Projects/", "/Projects", ".git",
    "Projects/tide-app/.git", "Projects/tide-app.", "Projects/tide-app ", " Projects", "Projects/CON",
    "Projects/nul.txt", "Projects/a:b", "Projects/a\x00b", "Projects/a\nb", "Projects/*", "Projects/?",
    "\\\\?\\C:\\", "//server/share", "a/" * 30, "x" * 1100, None, 3, ["Projects"],
])
def test_paths_that_could_leave_the_root_or_mean_something_else_are_refused(root, path):
    with pytest.raises(FolderRefused) as refused:
        workspace_folders.browse(path)
    assert refused.value.code == "workspace_folder_refused"
    with pytest.raises(FolderRefused):
        workspace_folders.authorize(path, parent=True)


def test_a_folder_that_isnt_there_or_a_file_is_unavailable(root):
    for path in ("Projects/missing", "readme.txt", "Projects/tide-app/app.py"):
        with pytest.raises(FolderRefused) as refused:
            workspace_folders.browse(path)
        assert refused.value.code == "workspace_folder_unavailable"


def test_odd_but_harmless_names_are_just_names(root):
    (root / "Projects" / "%2e%2e").mkdir()
    (root / "Projects" / "．．").mkdir()  # Full-width dots are not "..".
    names = [folder["name"] for folder in workspace_folders.browse("Projects")["folders"]]
    assert "%2e%2e" in names and "．．" in names
    assert workspace_folders.authorize("Projects/%2e%2e", parent=False).path == root / "Projects" / "%2e%2e"
    assert workspace_folders.authorize("Projects/．．", parent=False).path == root / "Projects" / "．．"


def test_a_link_that_leads_out_is_not_listed_and_cant_be_opened_or_chosen(root, tmp_path):
    _link(root / "Projects" / "escape", _outside(tmp_path))
    assert "escape" not in [folder["name"] for folder in workspace_folders.browse("Projects")["folders"]]
    for path in ("Projects/escape", "Projects/escape/private"):
        with pytest.raises(FolderRefused) as refused:
            workspace_folders.browse(path)
        assert refused.value.code == "workspace_folder_refused"
        with pytest.raises(FolderRefused):
            workspace_folders.authorize(path, parent=False)


def test_a_link_inside_the_root_is_refused_too(root):
    _link(root / "Projects" / "alias", root / "Projects" / "tide-app")
    with pytest.raises(FolderRefused):
        workspace_folders.authorize("Projects/alias", parent=False)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_a_windows_junction_that_leads_out_is_not_listed_and_cant_be_chosen(root, tmp_path):
    _junction(root / "Projects" / "junction", _outside(tmp_path))
    assert "junction" not in [folder["name"] for folder in workspace_folders.browse("Projects")["folders"]]
    for path in ("Projects/junction", "Projects/junction/private"):
        with pytest.raises(FolderRefused) as refused:
            workspace_folders.authorize(path, parent=True)
        assert refused.value.code == "workspace_folder_refused"


def test_the_root_itself_is_a_place_to_clone_into_but_never_the_code_folder(root):
    with pytest.raises(FolderRefused) as refused:
        workspace_folders.authorize("", parent=False)
    assert refused.value.code == "workspace_folder_root"
    parent = workspace_folders.authorize("", parent=True)
    assert parent.path == root and parent.scope_root == root
    assert workspace_folders.label("Projects/tide-app") == "Row-Bot › Projects › tide-app"


def test_a_choice_registers_through_the_pickers_own_validation(root, tmp_path, monkeypatch):
    from row_bot.developer import storage
    from row_bot.developer.client_workspace import register_existing_folder

    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    chosen = workspace_folders.authorize("Projects/tide-app", parent=False)
    assert chosen.scope_root == root and chosen.path == root / "Projects" / "tide-app"
    registration = register_existing_folder(chosen)
    assert registration.created
    assert Path(storage.get_workspace(registration.workspace.resource_id).path) == root / "Projects" / "tide-app"


def test_a_folder_swapped_for_a_link_after_the_choice_is_refused_at_registration(root, tmp_path, monkeypatch):
    import shutil

    from row_bot.developer import storage
    from row_bot.developer.client_workspace import register_existing_folder

    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    chosen = workspace_folders.authorize("Projects/notes", parent=False)
    shutil.rmtree(root / "Projects")
    _link(root / "Projects", _outside(tmp_path))
    (tmp_path / "outside" / "notes").mkdir()
    with pytest.raises(ValueError, match="folder_selection_denied"):
        register_existing_folder(chosen)
    assert storage.list_workspaces() == []


def test_a_crowded_folder_says_more_are_there(root, monkeypatch):
    monkeypatch.setattr(workspace_folders, "LIST_LIMIT", 3)
    for index in range(5):
        (root / "Projects" / f"repo-{index}").mkdir()
    page = workspace_folders.browse("Projects")
    assert len(page["folders"]) == 3 and page["truncated"]


def test_a_root_that_is_itself_a_link_lists_nothing(tmp_path, monkeypatch):
    real = _outside(tmp_path)
    link = tmp_path / "linked-root"
    _link(link, real)
    monkeypatch.setattr(workspace_folders, "workspace_root", lambda: link)
    with pytest.raises(FolderRefused) as refused:
        workspace_folders.browse("")
    assert refused.value.code == "workspace_folder_unavailable"
    assert os.path.isdir(real / "private")
