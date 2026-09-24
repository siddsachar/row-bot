"""Clone is explicit, locally scoped and never repeated after uncertainty."""

import subprocess
from types import SimpleNamespace

import pytest

from row_bot.developer import client_clone
from tests.subsystem.developer.test_client_empty_workspace import domain as empty_domain, COMMAND

pytestmark = pytest.mark.subsystem


@pytest.fixture
def domain(tmp_path, monkeypatch):
    return empty_domain.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize('source', [
    '--upload-pack=bad', 'file:///private/repo', 'ext::bad',
    'https://user:token@example.test/repo.git', 'https://example.test/repo.git\n',
])
def test_rejects_unsafe_sources_without_touching_parent(domain, source):
    _, storage, grant, _, _ = domain
    with pytest.raises(client_clone.CloneCreationError, match='clone_source_invalid'):
        client_clone.clone_selected_repository(
            grant, source, command_id=COMMAND, persist=lambda _: None,
            validate=lambda: None)
    assert list(grant.path.iterdir()) == []
    assert not storage.WORKSPACES_PATH.exists()


def test_success_clones_once_and_reconciles_registered_workspace(domain, monkeypatch):
    _, storage, grant, _, _ = domain
    source = 'https://example.test/team/demo.git'
    checkpoints = []
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if 'clone' in args:
            (grant.path / 'demo' / '.git').mkdir()
            return SimpleNamespace(stdout='')
        return SimpleNamespace(stdout=source + '\n')

    monkeypatch.setattr(client_clone.subprocess, 'run', fake_run)
    first = client_clone.clone_selected_repository(
        grant, source, command_id=COMMAND, persist=checkpoints.append,
        validate=lambda: None)
    assert first.created and first.workspace.name == 'demo'
    assert [entry.stage for entry in checkpoints] == [
        'empty_created', 'clone_started', 'cloned']
    assert storage.get_workspace(first.workspace.resource_id).repo_url == source
    replay = client_clone.clone_selected_repository(
        grant, source, command_id=COMMAND, persist=checkpoints.append,
        recovery=checkpoints[-1], validate=lambda: None)
    assert replay.workspace.resource_id == first.workspace.resource_id
    assert sum('clone' in args for args in calls) == 1


def test_failed_clone_keeps_partial_files_and_never_retries(domain, monkeypatch):
    _, storage, grant, _, _ = domain
    source = 'https://example.test/team/demo.git'
    checkpoints = []
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if 'clone' in args:
            (grant.path / 'demo' / 'partial.txt').write_text('retained')
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(stdout=source + '\n')

    monkeypatch.setattr(client_clone.subprocess, 'run', fake_run)
    with pytest.raises(client_clone.CloneCreationError, match='workspace_clone_unconfirmed'):
        client_clone.clone_selected_repository(
            grant, source, command_id=COMMAND, persist=checkpoints.append,
            validate=lambda: None)
    assert checkpoints[-1].stage == 'clone_started'
    with pytest.raises(client_clone.CloneCreationError, match='workspace_clone_unconfirmed'):
        client_clone.clone_selected_repository(
            grant, source, command_id=COMMAND, persist=checkpoints.append,
            recovery=checkpoints[-1], validate=lambda: None)
    assert (grant.path / 'demo' / 'partial.txt').read_text() == 'retained'
    assert storage.get_workspace(checkpoints[-1].empty['resource_id']) is not None
    assert sum('clone' in args for args in calls) == 1


def test_lost_response_reconciles_finished_clone_without_network(domain, monkeypatch):
    _, storage, grant, _, _ = domain
    source = 'https://example.test/team/demo.git'
    checkpoints = []
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if 'clone' in args:
            (grant.path / 'demo' / '.git').mkdir()
            return SimpleNamespace(stdout='')
        return SimpleNamespace(stdout=source + '\n')

    monkeypatch.setattr(client_clone.subprocess, 'run', fake_run)
    def lost_after_clone(value):
        checkpoints.append(value)
        if value.stage == 'cloned':
            raise OSError('lost progress write')

    with pytest.raises(client_clone.CloneCreationError, match='workspace_clone_unconfirmed'):
        client_clone.clone_selected_repository(
            grant, source, command_id=COMMAND, persist=lost_after_clone,
            validate=lambda: None)
    assert checkpoints[-2].stage == 'clone_started'
    recovered = client_clone.clone_selected_repository(
        grant, source, command_id=COMMAND, persist=checkpoints.append,
        recovery=checkpoints[-2], validate=lambda: None)
    assert storage.get_workspace(recovered.workspace.resource_id).repo_url == source
    assert sum('clone' in args for args in calls) == 1
