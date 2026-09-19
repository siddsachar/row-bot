from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.subsystem
COMMAND = "136e58e3-4e66-4e5c-a365-f6192e436983"


@pytest.fixture
def domain(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    from row_bot.developer import client_workspace as service, storage
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "registry")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "registry" / "workspaces.json")
    monkeypatch.setattr(storage, "latest_workspace_thread", lambda _: None)
    monkeypatch.setattr(service, "_conversation_available", lambda _: True)
    def forbidden(*args, **kwargs):
        pytest.fail("Empty setup invoked execution, a conversation or policy change")
    for name in ("clone_repository", "create_workspace_thread", "ensure_workspace_thread",
                 "create_thread_worktree", "set_workspace_approval_mode", "set_workspace_execution_settings",
                 "detect_git_summary", "remember_clone_parent_folder"):
        monkeypatch.setattr(storage, name, forbidden)
    monkeypatch.setattr(storage.subprocess, "run", forbidden)
    parent = tmp_path / "selected"
    parent.mkdir()
    grant = service.AuthorizedWorkspaceFolder(parent, tmp_path, "fake-authorized-parent")
    receipts = []
    def create(name="new-project", **kwargs):
        return service.create_empty_workspace(grant, name, command_id=kwargs.pop("command_id", COMMAND),
                                              persist_created=kwargs.pop("persist_created", receipts.append), **kwargs)
    return service, storage, grant, receipts, create


def test_creates_only_named_empty_directory_and_record(domain):
    service, storage, grant, receipts, create = domain
    result = create()
    assert result.created
    assert result.workspace.name == "new-project"
    assert result.workspace.project_workspace_id == result.workspace.execution_workspace_id
    assert result.workspace.association.status == "unassociated"
    assert result.workspace.policy.execution_mode == "local"
    assert result.workspace.policy.sandbox_network == "off"
    assert list(grant.path.iterdir()) == [grant.path / "new-project"]
    assert list((grant.path / "new-project").iterdir()) == []
    assert len(storage.list_workspaces()) == 1
    assert len(receipts) == 1
    assert str(grant.path) not in json.dumps(asdict(result))
    assert str(grant.path) not in json.dumps(asdict(receipts[0]))
    assert service.EmptyWorkspaceRecovery(**json.loads(json.dumps(asdict(receipts[0])))) == receipts[0]


@pytest.mark.parametrize("name", ["", " ", ".", "..", "../escape", "a/b", "a\\b", "C:\\folder",
    "\\\\host\\share", "con", "CON.txt", "aux", "nul.json", "LPT1", "COM¹.txt", "conout$",
    "a:", "a?", "a*", "a|", "a<", "a>", 'a"', "a\x00", "a\x1f", "a\x7f", "trail.",
    "trail ", " lead", ".GIT", "x" * 256, "é" * 128, "\ud800"])
def test_invalid_names_have_no_persistent_effect(domain, name):
    _, storage, grant, receipts, create = domain
    with pytest.raises(ValueError, match="workspace_name_invalid"):
        create(name)
    assert not storage.WORKSPACES_PATH.exists()
    assert list(grant.path.iterdir()) == []
    assert receipts == []


@pytest.mark.parametrize("case", ["no_grant", "outside", "missing", "file"])
def test_denied_parent_has_no_effect(domain, tmp_path, case):
    service, storage, grant, receipts, _ = domain
    file = tmp_path / "file"
    file.write_text("retained")
    selection = {"no_grant": replace(grant, selection_id=""),
                 "outside": replace(grant, scope_root=grant.path, path=tmp_path),
                 "missing": replace(grant, path=grant.path / "missing"),
                 "file": replace(grant, path=file)}[case]
    with pytest.raises(ValueError, match="folder_selection"):
        service.create_empty_workspace(selection, "new", command_id=COMMAND, persist_created=receipts.append)
    assert not storage.WORKSPACES_PATH.exists()
    assert list(grant.path.iterdir()) == []
    assert file.read_text() == "retained"


@pytest.mark.parametrize("kind", ["empty", "nonempty", "file"])
def test_destination_collision_preserves_every_existing_byte(domain, kind):
    _, storage, grant, receipts, create = domain
    destination = grant.path / "new-project"
    if kind == "file":
        destination.write_text("retained")
    else:
        destination.mkdir()
        if kind == "nonempty":
            (destination / "owned.txt").write_text("retained")
    with pytest.raises(ValueError, match="workspace_destination_exists"):
        create()
    assert not storage.WORKSPACES_PATH.exists()
    assert receipts == []
    assert list(grant.path.iterdir()) == [destination]
    if kind != "empty":
        assert (destination if kind == "file" else destination / "owned.txt").read_text() == "retained"


def test_record_failure_recovers_same_directory_and_identity(domain, monkeypatch):
    _, storage, grant, receipts, create = domain
    save = storage.save_workspace
    monkeypatch.setattr(storage, "save_workspace", lambda _: (_ for _ in ()).throw(OSError("private path")))
    with pytest.raises(ValueError, match="^workspace_registration_failed$") as failure:
        create()
    assert failure.value.recovery == receipts[0]
    directory = grant.path / "new-project"
    original = directory.stat()
    monkeypatch.setattr(storage, "save_workspace", save)
    result = create(recovery=receipts[0])
    assert result.created
    assert result.workspace.resource_id == receipts[0].resource_id
    assert directory.stat().st_ino == original.st_ino
    assert len(receipts) == len(storage.list_workspaces()) == 1
    assert list(directory.iterdir()) == []


def test_response_loss_reuses_record_and_preserves_association_policy(domain):
    _, storage, _, receipts, create = domain
    first = create()
    workspace = storage.get_workspace(first.workspace.resource_id)
    workspace.origin_conversation_id = "origin-chat"
    workspace.approval_mode = "block"
    storage.save_workspace(workspace)
    second = create(recovery=receipts[0])
    assert not second.created
    assert second.workspace.resource_id == first.workspace.resource_id
    assert second.workspace.association.conversation_id == "origin-chat"
    assert storage.get_workspace(workspace.id).to_dict() == workspace.to_dict()


def test_unconfirmed_receipt_failure_never_adopts_existing_folder(domain):
    _, storage, grant, _, create = domain
    def fail(_):
        raise OSError("private receipt failure")
    with pytest.raises(ValueError, match="^workspace_creation_unconfirmed$") as failure:
        create(persist_created=fail)
    assert failure.value.recovery is None
    assert (grant.path / "new-project").is_dir()
    with pytest.raises(ValueError, match="workspace_destination_exists"):
        create()
    assert not storage.WORKSPACES_PATH.exists()


@pytest.mark.parametrize("field,value", [("command_id", str(uuid.UUID(int=1))),
    ("folder_name", "another"), ("resource_id", "dev_other"), ("parent_identity", "wrong"),
    ("directory_identity", "wrong")])
def test_changed_recovery_identity_is_rejected(domain, field, value):
    _, storage, _, receipts, create = domain
    first = create()
    with pytest.raises(ValueError, match="workspace_recovery_conflict"):
        create(recovery=replace(receipts[0], **{field: value}))
    assert len(storage.list_workspaces()) == 1
    assert storage.get_workspace(first.workspace.resource_id).name == "new-project"


def test_directory_changed_before_registration_is_not_empty_success(domain):
    _, storage, grant, _, create = domain
    def persist(_):
        (grant.path / "new-project" / "other.txt").write_text("retained")
    with pytest.raises(ValueError, match="workspace_(destination_not_empty|recovery_conflict)"):
        create(persist_created=persist)
    assert not storage.WORKSPACES_PATH.exists()
    assert (grant.path / "new-project" / "other.txt").read_text() == "retained"


def test_concurrent_same_name_has_exactly_one_creation(domain):
    _, storage, grant, receipts, create = domain
    def submit(number):
        try:
            return create(command_id=str(uuid.UUID(int=number + 1)))
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(16)))
    assert sum(not isinstance(result, str) for result in results) == 1
    assert len(storage.list_workspaces()) == len(receipts) == 1
    assert len(list(grant.path.iterdir())) == 1


def test_execution_time_name_collision_never_overwrites(domain, monkeypatch):
    _, storage, grant, _, create = domain
    original = os.mkdir
    def collide(path, *args, **kwargs):
        if path == grant.path / "new-project" or path == "new-project":
            (grant.path / "new-project").write_text("concurrent file")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", collide)
    with pytest.raises(ValueError, match="workspace_destination_exists"):
        create()
    assert not storage.WORKSPACES_PATH.exists()
    assert (grant.path / "new-project").read_text() == "concurrent file"


def test_reparse_parent_is_denied_at_execution(domain, monkeypatch):
    service, storage, grant, _, create = domain
    original = service.scoped_workspace_path
    calls = []
    def revoked(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise ValueError("workspace_path_denied")
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "scoped_workspace_path", revoked)
    with pytest.raises(ValueError, match="folder_selection_denied"):
        create()
    assert not storage.WORKSPACES_PATH.exists()
    assert list(grant.path.iterdir()) == []


def test_symlink_destination_is_never_followed(domain, tmp_path):
    _, storage, grant, _, create = domain
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, grant.path / "new-project", target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating symbolic links")
    with pytest.raises(ValueError, match="workspace_destination_exists"):
        create()
    assert list(outside.iterdir()) == []
    assert not storage.WORKSPACES_PATH.exists()


@pytest.mark.parametrize("location", ["scope", "parent"])
def test_reparse_metadata_denies_parent_and_ancestors(domain, monkeypatch, location):
    _, storage, grant, _, create = domain
    marked = grant.scope_root if location == "scope" else grant.path
    original = Path.lstat
    def metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == marked:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(ValueError, match="folder_selection_denied"):
        create()
    assert list(grant.path.iterdir()) == []
    assert not storage.WORKSPACES_PATH.exists()


def test_replaced_created_directory_cannot_be_recovered(domain, monkeypatch):
    _, storage, grant, receipts, create = domain
    save = storage.save_workspace
    monkeypatch.setattr(storage, "save_workspace", lambda _: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ValueError, match="workspace_registration_failed"):
        create()
    target = grant.path / "new-project"
    target.rename(grant.path / "retained-original")
    target.mkdir()
    monkeypatch.setattr(storage, "save_workspace", save)
    with pytest.raises(ValueError, match="workspace_recovery_conflict"):
        create(recovery=receipts[0])
    assert not storage.WORKSPACES_PATH.exists()
    assert len(list(grant.path.iterdir())) == 2


def test_creation_permission_failure_has_no_registration(domain, monkeypatch):
    _, storage, grant, receipts, create = domain
    original = os.mkdir
    def denied(path, *args, **kwargs):
        if path == grant.path / "new-project" or path == "new-project":
            raise PermissionError("private filesystem details")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", denied)
    with pytest.raises(ValueError, match="^workspace_creation_denied$"):
        create()
    assert not storage.WORKSPACES_PATH.exists()
    assert list(grant.path.iterdir()) == []
    assert receipts == []


def test_duplicate_submission_without_trusted_receipt_cannot_claim_creation(domain):
    _, storage, grant, _, create = domain
    create()
    with pytest.raises(ValueError, match="workspace_identity_conflict"):
        create()
    assert len(storage.list_workspaces()) == len(list(grant.path.iterdir())) == 1


@pytest.mark.parametrize("command", ["", "not-a-command", None])
def test_command_identity_required_before_creation(domain, command):
    _, storage, grant, _, create = domain
    with pytest.raises(ValueError, match="invalid_command"):
        create(command_id=command)
    assert not storage.WORKSPACES_PATH.exists()
    assert list(grant.path.iterdir()) == []


def test_parent_swap_at_mkdir_cannot_redirect_creation(domain, monkeypatch):
    _, storage, grant, receipts, create = domain
    original = os.mkdir
    held = grant.path.with_name("original-retained")
    def swap(path, *args, **kwargs):
        if path == grant.path / "new-project" or path == "new-project":
            grant.path.rename(held)
            original(grant.path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", swap)
    with pytest.raises(ValueError, match="workspace_creation_(denied|unconfirmed)"):
        create()
    assert not storage.WORKSPACES_PATH.exists()
    assert receipts == []
    assert list(grant.path.iterdir()) == []
    if os.name == "nt":
        assert not held.exists()  # Parent handle denies the rename itself.
    else:
        assert list(held.iterdir()) == [held / "new-project"]  # mkdirat stays bound.
    # No guard handle leaks beyond failure.
    grant.path.rename(grant.path.with_name("released-parent"))


@pytest.mark.skipif(os.name != "nt", reason="Windows directory sharing semantics")
@pytest.mark.parametrize("ancestor", ["scope", "parent"])
def test_windows_ancestor_handles_block_replacement_until_completion(domain, monkeypatch, ancestor):
    _, _, grant, _, create = domain
    directory = grant.scope_root if ancestor == "scope" else grant.path
    retained = directory.with_name(directory.name + "-retained")
    original = os.mkdir
    attempts = []
    def attempt(path, *args, **kwargs):
        if path == grant.path / "new-project":
            with pytest.raises(PermissionError):
                directory.rename(retained)
            attempts.append(True)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", attempt)
    assert create().created
    assert attempts == [True]
    assert not retained.exists()
    directory.rename(retained)
    retained.rename(directory)


class _AuthorityDenied(Exception):
    code = "capability_revoked"


def test_authority_is_revalidated_after_waiting_for_registry_writer(domain, monkeypatch):
    _, storage, grant, receipts, create = domain
    transaction = storage.workspace_transaction
    expired = False
    @contextmanager
    def waited():
        nonlocal expired
        with transaction():
            expired = True
            yield
    def validate():
        if expired:
            raise _AuthorityDenied()
    monkeypatch.setattr(storage, "workspace_transaction", waited)
    with pytest.raises(_AuthorityDenied):
        create(validate=validate)
    assert list(grant.path.iterdir()) == []
    assert receipts == []
    assert not storage.WORKSPACES_PATH.exists()


@pytest.mark.parametrize("deny_at", [2, 3, 4])
def test_authority_denial_at_effect_boundaries_preserves_only_actual_progress(domain, deny_at):
    service, storage, grant, receipts, create = domain
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == deny_at:
            raise _AuthorityDenied()
    if deny_at == 2:
        with pytest.raises(_AuthorityDenied):
            create(validate=validate)
        assert receipts == []
        assert list(grant.path.iterdir()) == []
    else:
        with pytest.raises(service.EmptyWorkspaceCreationError, match="^capability_revoked$") as failed:
            create(validate=validate)
        assert failed.value.recovery == receipts[0]
        assert (grant.path / "new-project").is_dir()
    assert not storage.WORKSPACES_PATH.exists()
    if deny_at > 2:
        assert create(recovery=receipts[0], validate=lambda: None).created


def test_authority_denial_before_reusing_registered_identity_preserves_receipt(domain):
    service, storage, _, receipts, create = domain
    first = create()
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise _AuthorityDenied()
    with pytest.raises(service.EmptyWorkspaceCreationError, match="^capability_revoked$") as failed:
        create(recovery=receipts[0], validate=validate)
    assert failed.value.recovery == receipts[0]
    assert len(storage.list_workspaces()) == 1
    assert storage.get_workspace(first.workspace.resource_id) is not None
