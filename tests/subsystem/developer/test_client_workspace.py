from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def domain(tmp_path, monkeypatch):
    from row_bot import threads
    from row_bot.developer import client_workspace as service, storage
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    monkeypatch.setattr(threads, "_thread_write_blocked", lambda _: False)
    threads._ensure_thread_db()
    with sqlite3.connect(threads.DB_PATH) as connection:
        for identifier in ("chat-a", "chat-b", "legacy"):
            connection.execute("INSERT INTO thread_meta(thread_id,name) VALUES (?,?)", (identifier, identifier))
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "registry")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "registry" / "workspaces.json")
    monkeypatch.setattr(service, "workspace_has_custom_read_hooks", lambda _: False)
    def forbidden(*args, **kwargs):
        pytest.fail("Setup invoked an implicit execution or conversation mutation")
    for name in ("clone_repository", "create_workspace_thread", "ensure_workspace_thread", "create_thread_worktree"):
        monkeypatch.setattr(storage, name, forbidden)
    monkeypatch.setattr(threads, "create_thread", forbidden)
    return service, storage, threads


def register(domain, tmp_path, name="fixture"):
    service, _, _ = domain
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return service.register_existing_folder(service.AuthorizedWorkspaceFolder(folder, tmp_path, "selection-1"))


def test_registration_deduplicates_without_source_or_policy_changes(domain, tmp_path):
    service, storage, threads = domain
    folder = tmp_path / "fixture"
    folder.mkdir()
    (folder / "existing.txt").write_bytes(b"untouched\n")
    first = register(domain, tmp_path)
    second = register(domain, tmp_path)
    assert first.created and not second.created
    assert first.workspace == second.workspace
    assert first.workspace.name == "fixture"
    assert list(folder.iterdir()) == [folder / "existing.txt"]
    assert (folder / "existing.txt").read_bytes() == b"untouched\n"
    assert len(storage.list_workspaces()) == 1
    assert first.workspace.association.status == "unassociated"
    assert str(folder) not in json.dumps(asdict(first))
    with sqlite3.connect(threads.DB_PATH) as connection:
        assert connection.execute("SELECT count(*) FROM thread_meta").fetchone()[0] == 3


@pytest.mark.parametrize("selection", ["outside", "missing", "file", "no_grant"])
def test_invalid_folder_selection_does_not_write_registry(domain, tmp_path, selection):
    service, storage, _ = domain
    root = tmp_path / "scope"
    root.mkdir()
    (root / "plain.txt").write_text("data")
    paths = {"outside": tmp_path, "missing": root / "missing", "file": root / "plain.txt", "no_grant": root}
    with pytest.raises(ValueError, match="folder_selection"):
        service.register_existing_folder(service.AuthorizedWorkspaceFolder(paths[selection], root,
            "" if selection == "no_grant" else "grant"))
    assert not storage.WORKSPACES_PATH.exists()


def test_origin_reuse_add_elsewhere_delete_and_explicit_repair(domain, tmp_path):
    from row_bot import conversation_resources
    service, storage, threads = domain
    choice = register(domain, tmp_path).workspace
    associated = service.associate_workspace(choice.resource_id, "chat-a", choice.revision, None)
    conversation_resources.bind("chat-a", "workspace", choice.resource_id, expected_revision=0)
    conversation_resources.bind("chat-b", "workspace", choice.resource_id, expected_revision=0)
    assert service.resolve_workspace_open(choice.resource_id, associated.revision).association.conversation_id == "chat-a"
    storage.clear_thread_references("chat-a")
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("DELETE FROM thread_meta WHERE thread_id='chat-a'")
    missing = service.resolve_workspace_open(choice.resource_id, associated.revision)
    assert missing.association.status == "repair_required"
    assert missing.association.conversation_id == "chat-a"
    with pytest.raises(ValueError, match="origin_repair_required"):
        service.associate_workspace(choice.resource_id, "chat-b", missing.revision, "chat-a")
    repaired = service.associate_workspace(choice.resource_id, "chat-b", missing.revision, "chat-a", repair=True)
    assert repaired.association.conversation_id == "chat-b"
    with pytest.raises(ValueError, match="resource_revision_conflict"):
        service.associate_workspace(choice.resource_id, "legacy", missing.revision, "chat-a", repair=True)


def test_latest_workspace_preserves_execution_worktree(domain, tmp_path):
    service, _, threads = domain
    project = register(domain, tmp_path, "project").workspace
    worktree = register(domain, tmp_path, "worktree").workspace
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("UPDATE thread_meta SET thread_type='code', developer_workspace_id=?, project_workspace_id=? WHERE thread_id='legacy'",
                           (worktree.resource_id, project.resource_id))
    opened = service.resolve_workspace_open(project.resource_id, project.revision)
    assert opened.project_workspace_id == project.resource_id
    assert opened.execution_workspace_id == worktree.resource_id
    assert opened.association.conversation_id == "legacy"


def test_concurrent_registration_and_origin_cas(domain, tmp_path):
    service, storage, _ = domain
    for number in range(20):
        (tmp_path / f"workspace-{number}").mkdir()
    def create(number):
        return service.register_existing_folder(service.AuthorizedWorkspaceFolder(tmp_path / f"workspace-{number}", tmp_path, f"grant-{number}"))
    with ThreadPoolExecutor(max_workers=8) as executor:
        registered = list(executor.map(create, range(20)))
    assert len(storage.list_workspaces()) == 20
    choice = registered[0].workspace
    def associate(identifier):
        try:
            return service.associate_workspace(choice.resource_id, identifier, choice.revision, None)
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(associate, ("chat-a", "chat-b")))
    assert outcomes.count("resource_revision_conflict") == 1


def test_path_identity_collision_never_selects_another_folder(domain, tmp_path, monkeypatch):
    service, storage, _ = domain
    first = register(domain, tmp_path, "first").workspace
    monkeypatch.setattr(storage, "_workspace_id_for_path", lambda _: first.resource_id)
    with pytest.raises(ValueError, match="workspace_identity_conflict"):
        register(domain, tmp_path, "different")
    assert storage.get_workspace(first.resource_id).name == "first"


def test_workspace_library_pagination_and_changed_cursor(domain, tmp_path):
    service, _, _ = domain
    for number in range(13):
        register(domain, tmp_path, f"folder-{number}")
    page = service.list_workspace_choices(limit=5)
    identifiers = [item.resource_id for item in page.items]
    first_cursor = page.next_cursor
    while page.next_cursor:
        page = service.list_workspace_choices(cursor=page.next_cursor, limit=5)
        identifiers.extend(item.resource_id for item in page.items)
    assert len(identifiers) == len(set(identifiers)) == 13
    register(domain, tmp_path, "new-folder")
    with pytest.raises(ValueError, match="cursor_revision_conflict"):
        service.list_workspace_choices(cursor=first_cursor)


def test_inspector_complete_changes_cache_and_revocation(domain, tmp_path, monkeypatch):
    from row_bot import conversation_resources
    from row_bot.developer import inspector_snapshot as snapshots
    from row_bot.developer.devcontainer import DevcontainerInfo
    from row_bot.developer.review import ChangedFile
    from row_bot.developer.change_ledger import ChangeSet, FileChange
    from row_bot.developer.sandbox_runtime import SandboxProbe
    service, storage, _ = domain
    choice = register(domain, tmp_path).workspace
    bound = conversation_resources.bind("chat-a", "workspace", choice.resource_id, expected_revision=0)
    monkeypatch.setattr(snapshots, "_snapshots", {})
    monkeypatch.setattr(snapshots, "_states", {})
    calls = []
    snapshot = snapshots.InspectorSnapshot(choice.resource_id, "chat-a", 0, 0,
        storage.get_workspace(choice.resource_id), {"is_git": True, "branch": "fixture", "remote": "secret", "repo_root": str(tmp_path)}, [],
        [ChangedFile(f"file-{i:04}.txt", "M") for i in range(1003)], None,
        [ChangeSet(f"set-{i}", choice.resource_id, "chat-a", i, f"Fixture {i}",
                   [FileChange(f"changed-{j}.txt", "update", "old", "new", before_text="private-before-text")
                    for j in range(203)]) for i in range(17)], [],
        DevcontainerInfo(present=False), SandboxProbe(False), None, [])
    def collect(*args):
        calls.append(args)
        return snapshot
    monkeypatch.setattr(snapshots, "_collect_snapshot_sync", collect)
    async def scenario():
        first = await service.get_workspace_inspector(choice.resource_id, "chat-a")
        for _ in range(10):
            assert await service.get_workspace_inspector(choice.resource_id, "chat-a") == first
        assert len(calls) == 1
        assert first.changed_total == 1003
        assert "secret" not in json.dumps(asdict(first))
        page = service.list_inspector_changes(choice.resource_id, "chat-a", limit=71)
        paths = [item.path for item in page.items]
        while page.next_cursor:
            page = service.list_inspector_changes(choice.resource_id, "chat-a", cursor=page.next_cursor, limit=71)
            paths.extend(item.path for item in page.items)
        assert len(paths) == len(set(paths)) == 1003
        ledger = service.list_inspector_change_sets(choice.resource_id, "chat-a", limit=5)
        identifiers = [item.id for item in ledger.items]
        while ledger.next_cursor:
            ledger = service.list_inspector_change_sets(choice.resource_id, "chat-a", cursor=ledger.next_cursor, limit=5)
            identifiers.extend(item.id for item in ledger.items)
        assert len(identifiers) == len(set(identifiers)) == 17
        files = service.list_inspector_change_set_files(choice.resource_id, "chat-a", "set-16", limit=33)
        paths = [item.path for item in files.items]
        assert "private-before-text" not in json.dumps(asdict(files))
        while files.next_cursor:
            files = service.list_inspector_change_set_files(choice.resource_id, "chat-a", "set-16", cursor=files.next_cursor, limit=33)
            paths.extend(item.path for item in files.items)
        assert len(paths) == len(set(paths)) == 203
        def failed(*args):
            raise OSError("private path")
        monkeypatch.setattr(snapshots, "_collect_snapshot_sync", failed)
        failed_view = await service.get_workspace_inspector(choice.resource_id, "chat-a", refresh=True)
        assert failed_view.status == "stale"
        assert failed_view.snapshot_revision == first.snapshot_revision
        assert failed_view.error == "inspector_refresh_failed"
        conversation_resources.unbind("chat-a", bound.bindings[0].binding_id, expected_revision=1)
        with pytest.raises(ValueError, match="resource_binding_revoked"):
            await service.get_workspace_inspector(choice.resource_id, "chat-a")
        await snapshots.shutdown_snapshot_refreshes()
    asyncio.run(scenario())


def test_inspector_custom_git_hooks_are_not_executed(domain, tmp_path, monkeypatch):
    from row_bot import conversation_resources
    from row_bot.developer import inspector_snapshot as snapshots
    service, _, _ = domain
    choice = register(domain, tmp_path).workspace
    conversation_resources.bind("chat-a", "workspace", choice.resource_id, expected_revision=0)
    monkeypatch.setattr(snapshots, "_snapshots", {})
    monkeypatch.setattr(service, "workspace_has_custom_read_hooks", lambda _: True)
    monkeypatch.setattr(snapshots, "request_snapshot_refresh", lambda *args, **kwargs: pytest.fail("Unsafe Git collection dispatched"))
    with pytest.raises(ValueError, match="workspace_read_hooks_unavailable"):
        asyncio.run(service.get_workspace_inspector(choice.resource_id, "chat-a"))
