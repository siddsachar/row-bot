from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from row_bot.developer.review import list_directory_page, read_bounded_file
from tests.subsystem.developer.test_client_workspace import domain, register  # noqa: F401

pytestmark = pytest.mark.subsystem


def test_complete_large_directory_expands_one_level_only(tmp_path, monkeypatch):
    nested = tmp_path / "nested"
    nested.mkdir()
    for number in range(1003):
        (tmp_path / f"file-{number:04}.txt").write_text("fixture")
    (nested / "child.txt").write_text("child")
    scanned = []
    original = os.scandir
    def scan(path):
        scanned.append(Path(path))
        return original(path)
    monkeypatch.setattr(os, "scandir", scan)
    page = list_directory_page(str(tmp_path), limit=83)
    cursor = page.next_cursor
    assert str(tmp_path) not in base64.urlsafe_b64decode(cursor).decode()
    paths = [row.relative_path for row in page.items]
    while page.next_cursor:
        page = list_directory_page(str(tmp_path), cursor=page.next_cursor, limit=83)
        paths.extend(row.relative_path for row in page.items)
    assert len(paths) == len(set(paths)) == 1004
    assert set(scanned) == {tmp_path}
    children = list_directory_page(str(tmp_path), "nested")
    assert children.items[0].relative_path == "nested/child.txt"
    assert scanned[-1] == nested
    (tmp_path / "new.txt").write_text("new")
    with pytest.raises(ValueError, match="directory_revision_conflict"):
        list_directory_page(str(tmp_path), cursor=cursor)


@pytest.mark.parametrize("relative", ["../secret", "/absolute", "C:secret", "\\\\host\\share", ".git/config", "nested/../secret", "a\0b"])
def test_untrusted_path_rejected_without_read(tmp_path, relative):
    result = read_bounded_file(str(tmp_path), relative)
    assert result.status in {"denied", "missing"}
    assert not result.text
    with pytest.raises((ValueError, FileNotFoundError)):
        list_directory_page(str(tmp_path), relative)


def test_bounded_large_file_utf8_continuation_and_stale_revision(tmp_path, monkeypatch):
    target = tmp_path / "text.txt"
    content = "hello€😀\n" * 20000
    target.write_bytes(content.encode("utf-8"))
    original = os.fdopen
    requested = []
    class CountReads:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def read(self, size=-1):
            requested.append(size)
            assert 0 <= size <= 4096
            return self.handle.read(size)
    monkeypatch.setattr(os, "fdopen", lambda *a, **k: CountReads(original(*a, **k)))
    first = read_bounded_file(str(tmp_path), "text.txt", limit_bytes=1003)
    assert first.status == "text" and first.next_offset
    text = first.text
    page = first
    while page.next_offset:
        page = read_bounded_file(str(tmp_path), "text.txt", offset=page.next_offset,
                                 limit_bytes=1003, expected_revision=first.revision)
        assert page.status == "text"
        text += page.text
    assert text == content
    assert max(requested) == 4096
    target.write_text("changed")
    stale = read_bounded_file(str(tmp_path), "text.txt", expected_revision=first.revision)
    assert stale.status == "stale" and not stale.text


def test_binary_missing_and_symlink_previews(tmp_path):
    (tmp_path / "binary").write_bytes(b"\0private")
    assert read_bounded_file(str(tmp_path), "binary").status == "binary"
    assert read_bounded_file(str(tmp_path), "absent").status == "missing"
    target = tmp_path / "safe.txt"
    target.write_text("safe")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("OS does not permit fixture symlink creation")
    assert read_bounded_file(str(tmp_path), "link.txt").status == "denied"
    assert "link.txt" not in [row.name for row in list_directory_page(str(tmp_path)).items]


def test_cursor_scope_and_malformed_input_fail_closed(tmp_path):
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")
    cursor = list_directory_page(str(tmp_path), limit=1).next_cursor
    (tmp_path / "child").mkdir()
    with pytest.raises(ValueError, match="invalid_cursor"):
        list_directory_page(str(tmp_path), "child", cursor=cursor)
    with pytest.raises(ValueError):
        list_directory_page(str(tmp_path), cursor=base64.urlsafe_b64encode(json.dumps({}).encode()).decode())


def test_reparse_point_is_denied_without_reading_target(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from row_bot.developer.review import scoped_workspace_path
    folder = tmp_path / "reparse"
    folder.mkdir()
    original = Path.lstat
    def metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == folder:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(ValueError, match="workspace_path_denied"):
        scoped_workspace_path(tmp_path, "reparse")


def test_diff_pages_bound_output_and_disable_external_handlers(tmp_path, monkeypatch):
    import io
    from row_bot.developer import review
    (tmp_path / "file.txt").write_text("modified")
    content = ("+fixture €\n" * 100).encode()
    commands = []
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            commands.append((command, kwargs))
            if "config" in command:
                output = b""
            elif "--is-inside-work-tree" in command:
                output = b"true\n"
            elif "--git-path" in command:
                output = b".git/index\n"
            elif "--verify" in command:
                output = b"fixture-head\n"
            else:
                output = content
            self.stdout = io.BytesIO(output)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stdout.close()
        def poll(self):
            return self.returncode
        def kill(self):
            pass
        def wait(self, **kwargs):
            return 0
    monkeypatch.setattr(review.subprocess, "Popen", Process)
    page = review.read_bounded_diff(str(tmp_path), "file.txt", limit_bytes=101)
    assert page.truncated
    text = page.text
    while page.next_offset:
        page = review.read_bounded_diff(str(tmp_path), "file.txt", offset=page.next_offset,
                                       limit_bytes=101, expected_revision=page.revision)
        text += page.text
    assert text == content.decode()
    assert all("--no-ext-diff" in command and "--no-textconv" in command
               for command, _ in commands if "diff" in command)
    assert all(options["env"]["GIT_OPTIONAL_LOCKS"] == "0" for _, options in commands)


def test_custom_git_clean_filter_never_runs_from_diff(tmp_path, monkeypatch):
    import io
    from row_bot.developer import review
    (tmp_path / "file.txt").write_text("fixture")
    calls = []
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            calls.append(command)
            assert "config" in command or "--is-inside-work-tree" in command
            self.stdout = io.BytesIO(b"true\n" if "--is-inside-work-tree" in command else b"filter.fixture.clean\n")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stdout.close()
        def poll(self):
            return 0
        def kill(self):
            pass
        def wait(self, **kwargs):
            return 0
    monkeypatch.setattr(review.subprocess, "Popen", Process)
    assert review.read_bounded_diff(str(tmp_path), "file.txt").status == "unavailable"
    assert len(calls) == 2


def test_plain_folder_ignores_global_filters_but_actual_repository_remains_guarded(domain, tmp_path, monkeypatch):
    import asyncio
    import shutil
    from types import SimpleNamespace
    from row_bot import conversation_resources
    from row_bot.developer import client_workspace, review, inspector_snapshot
    if shutil.which("git") is None:
        pytest.skip("Git metadata capability unavailable")
    service, storage, _ = domain
    choice = register(domain, tmp_path).workspace
    folder = Path(storage.get_workspace(choice.resource_id).path)
    (folder / "source.txt").write_text("Retained plain folder")
    config = tmp_path / "synthetic-gitconfig"
    config.write_text('[filter "fixture"]\nclean = must-never-execute\nprocess = must-never-execute\n[core]\nfsmonitor = must-never-execute\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    commands = []
    actual_process = review.subprocess.Popen
    def process(command, *args, **kwargs):
        commands.append(command)
        assert "rev-parse" in command or "config" in command
        return actual_process(command, *args, **kwargs)
    monkeypatch.setattr(review.subprocess, "Popen", process)
    assert review.workspace_has_custom_read_hooks(str(folder)) is False
    assert all("config" not in command for command in commands)
    conversation_resources.bind("chat-a", "workspace", choice.resource_id, expected_revision=0)
    monkeypatch.setattr(client_workspace, "workspace_has_custom_read_hooks", review.workspace_has_custom_read_hooks)
    monkeypatch.setattr(inspector_snapshot, "get_snapshot", lambda *_: None)
    monkeypatch.setattr(inspector_snapshot, "get_snapshot_refresh_error", lambda *_: "")
    requested = []
    monkeypatch.setattr(inspector_snapshot, "request_snapshot_refresh", lambda *args, **kwargs: requested.append(args))
    async def snapshot(*_):
        return SimpleNamespace(version=1, error="", git_summary={"is_git": False},
            diff_stats=review.DiffStats(0, 0, 0), changed_files=[], command_specs=[], todos=[])
    monkeypatch.setattr(inspector_snapshot, "wait_for_snapshot_refresh", snapshot)
    result = asyncio.run(service.get_workspace_inspector(choice.resource_id, "chat-a"))
    assert not result.is_git and requested
    assert read_bounded_file(str(folder), "source.txt").text == "Retained plain folder"
    # Read-only metadata against this existing checkout: no test Git mutation.
    repository = Path(__file__).resolve().parents[3]
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES")
    # The private QA environment intentionally drops the user's Git trust
    # configuration. Trust only this fixture checkout for this subprocess.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", repository.as_posix())
    assert review.workspace_has_custom_read_hooks(str(repository)) is True
    assert any("config" in command for command in commands)


def test_replaced_file_is_not_read_after_open(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("original")
    original = os.fdopen
    class ChangedHandle:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            target.write_text("changed before validation")
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def fileno(self):
            return self.handle.fileno()
        def read(self, size):
            pytest.fail("A changed file was read before revision revalidation")
    monkeypatch.setattr(os, "fdopen", lambda *a, **k: ChangedHandle(original(*a, **k)))
    assert read_bounded_file(str(tmp_path), "file.txt").status == "stale"


def test_git_changes_preserve_renames_unicode_and_embedded_newlines(tmp_path, monkeypatch):
    from row_bot.developer import review
    destination = "nested/renamed €\nfile.txt"
    def git(path, arguments, **kwargs):
        assert "-z" in arguments
        if "status" in arguments:
            return f"R  {destination}\0original.txt\0 M spaced name.txt\0"
        if "--cached" in arguments:
            return f"3\t1\t\0original.txt\0{destination}\0"
        return "2\t0\tspaced name.txt\0"
    monkeypatch.setattr(review, "_git", git)
    result = review.list_changed_files(str(tmp_path))
    assert [(item.path, item.status, item.additions, item.deletions) for item in result] == [
        (destination, "R", 3, 1), ("spaced name.txt", "M", 2, 0)]


def test_diff_continuation_rejects_index_only_revision_change(tmp_path, monkeypatch):
    from row_bot.developer import review
    import hashlib
    target = tmp_path / "file.txt"
    target.write_text("same working file")
    file_revision = review._file_revision(target.stat())
    previous = hashlib.sha256(f"file.txt:{file_revision}:old-index".encode()).hexdigest()
    monkeypatch.setattr(review, "workspace_has_custom_read_hooks", lambda _: False)
    monkeypatch.setattr(review, "_diff_base_revision", lambda _: "new-index")
    result = review.read_bounded_diff(str(tmp_path), "file.txt", offset=10, expected_revision=previous)
    assert result.status == "stale" and result.text == ""
