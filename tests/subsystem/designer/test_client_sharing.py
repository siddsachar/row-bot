from __future__ import annotations

from uuid import uuid4
import os

import pytest

from row_bot.designer import client_sharing as client, client_service, publish, storage
from row_bot.designer.state import DESIGNER_MODES
from tests.subsystem.designer.test_client_exports import project as _project, isolated as _isolated, renderer as _renderer

project, isolated, renderer = _project, _isolated, _renderer
pytestmark = pytest.mark.subsystem


@pytest.fixture
def channels(project, monkeypatch):
    from row_bot.channels import config, registry
    from row_bot.channels.base import ChannelCapabilities
    from row_bot.designer import share

    state = {'target': 'synthetic-recipient', 'calls': [], 'running': True, 'config': {'scope': 'a'}}
    class Channel:
        name = 'fake'
        display_name = 'Synthetic channel'
        capabilities = ChannelCapabilities(photo_out=True, document_out=True)
        def is_running(self):
            return state['running']
        def is_configured(self):
            return True
        def get_default_target(self):
            return state['target']
        def send_message(self, target, text):
            state['calls'].append(('message', target, text))
        def send_photo(self, target, path, caption=None):
            state['calls'].append(('photo', target, path))
        def send_document(self, target, path, caption=None):
            state['calls'].append(('document', target, path))
    channel = Channel()
    monkeypatch.setattr(registry, 'get', lambda name: channel if name == 'fake' else None)
    monkeypatch.setattr(share, 'channel_registry', registry)
    monkeypatch.setattr(config, 'get_all', lambda _name: state['config'])
    monkeypatch.setattr(publish, 'PUBLISHED_DIR', storage.DESIGNER_DIR / 'published')
    monkeypatch.setattr(publish.tunnel_manager, 'get_url', lambda *_args: 'https://synthetic.invalid')
    monkeypatch.setattr(publish.tunnel_manager, 'start_tunnel', lambda *_args, **_kwargs: pytest.fail('real tunnel boundary'))
    state['channel'] = channel
    return state


def execute(project, options, *, checkpoint=None, validate=None, review=None):
    reviewed = review or client.prepare_share(project.id, **options)
    return client.execute_share(project.id, review_id=reviewed.review_id, command_id=str(uuid4()),
                                validate=validate or (lambda: None), checkpoint=checkpoint or (lambda _: None), **options)


def test_review_is_passive_and_recipient_source_and_options_bound(project, channels):
    options = {'action': 'channel', 'channel_name': 'fake', 'delivery': 'pdf', 'text': 'Reviewed caption'}
    first = client.prepare_share(project.id, **options)
    assert first.recipient == channels['target'] and first.page_count == 2
    assert not channels['calls'] and not publish.PUBLISHED_DIR.exists()
    assert not (storage.DESIGNER_DIR / 'share_recovery').exists()
    channels['target'] = 'different-recipient'
    with pytest.raises(client_service.ArtifactError, match='share_review_changed'):
        execute(project, options, review=first)
    assert not channels['calls']


@pytest.mark.parametrize('mode', list(DESIGNER_MODES))
def test_local_publish_uses_existing_owner_all_modes_and_never_existing_tunnel(project, channels, mode):
    project.mode = mode
    storage.save_project(project)
    progress = []
    result = execute(project, {'action': 'publish'}, checkpoint=progress.append)
    assert result.status == 'published', result
    assert result.link_kind == 'local' and result.url.startswith('http://127.0.0.1:')
    assert publish.PUBLISHED_DIR.joinpath(project.id + '.html').is_file()
    assert client_service.read_artifact(project.id).publish_url == result.url
    assert [item['stage'] for item in progress] == ['publish_file_started', 'publication_prepared',
                                                  'publish_file_completed', 'publish_metadata_completed']
    assert all(set(item) == {'stage', 'effect_id', 'index', 'count', 'outcome'} for item in progress)


def test_explicit_remote_link_keeps_access_requirements_and_actual_url_class(project, channels):
    review = client.prepare_share(project.id, action='publish', remote=True)
    assert review.remote and review.requires_pairing
    result = execute(project, {'action': 'publish', 'remote': True}, review=review)
    assert result.status == 'published' and result.link_kind == 'remote_access'
    assert result.url.startswith('https://synthetic.invalid/published/')


def test_republication_retains_exact_old_bytes_and_rejects_metadata_unsupported(project, channels, monkeypatch):
    first = execute(project, {'action': 'publish'})
    path = publish.PUBLISHED_DIR / (project.id + '.html')
    before = path.read_bytes()
    current = client_service.read_artifact(project.id)
    current.pages[0].html = '<h1>New approved source</h1>'
    storage.save_project(current)
    second = execute(current, {'action': 'publish'})
    assert first.status == second.status == 'published', second
    assert path.read_bytes() != before
    assert any(item.read_bytes() == before for item in (storage.DESIGNER_DIR / 'publish_recovery').glob('*/previous'))
    from row_bot.developer import edits
    monkeypatch.setattr(edits, 'file_edit_metadata_digest', lambda _: (_ for _ in ()).throw(edits.FileEditError('file_metadata_unavailable')))
    captured = path.read_bytes()
    result = execute(client_service.read_artifact(project.id), {'action': 'publish'})
    assert result.code == 'file_metadata_unavailable' and path.read_bytes() == captured


def test_mockup_name_is_plain_text_in_published_outer_shell(project, channels):
    project.mode = 'app_mockup'
    project.name = '</title><script>unsafe()</script>'
    storage.save_project(project)
    result = execute(project, {'action': 'publish'})
    assert result.status == 'published'
    html = (publish.PUBLISHED_DIR / f'{project.id}.html').read_text()
    assert '<script>unsafe()' not in html and '&lt;/title&gt;' in html
    assert 'sandbox="allow-scripts"' in html


@pytest.mark.parametrize('delivery', ['pdf', 'pptx', 'html', 'slides'])
def test_file_sharing_uses_fake_channel_and_exact_submitted_count(project, channels, renderer, delivery):
    progress = []
    result = execute(project, {'action': 'channel', 'channel_name': 'fake', 'delivery': delivery}, checkpoint=progress.append)
    assert result.status == 'submitted', result
    assert result.submitted_count == len(channels['calls']) == (2 if delivery == 'slides' else 1)
    assert all(item[1] == 'synthetic-recipient' for item in channels['calls'])
    assert [item['stage'] for item in progress] == ['send_started', 'send_submitted'] * result.submitted_count


def test_partial_photo_send_is_uncertain_and_never_continues_or_resends(project, channels, renderer):
    original = channels['channel'].send_photo
    def second_fails(target, path, caption=None):
        original(target, path, caption)
        if len(channels['calls']) == 2:
            raise TimeoutError('synthetic unknown transport result')
    channels['channel'].send_photo = second_fails
    result = execute(project, {'action': 'channel', 'channel_name': 'fake', 'delivery': 'slides'})
    assert result.status == 'uncertain' and result.submitted_count == 1 and result.total_count == 2
    assert len(channels['calls']) == 2
    assert len(list((storage.DESIGNER_DIR / 'share_recovery').glob('*/*.png'))) == 2


def test_revocation_from_durable_pre_send_checkpoint_blocks_transport(project, channels, renderer):
    revoked = False
    def checkpoint(event):
        nonlocal revoked
        if event['stage'] == 'send_started':
            revoked = True
    def validate():
        if revoked:
            raise client_service.ArtifactError('capability_revoked')
    result = execute(project, {'action': 'channel', 'channel_name': 'fake', 'delivery': 'pdf'},
                     checkpoint=checkpoint, validate=validate)
    assert result.status == 'denied' and result.code == 'capability_revoked'
    assert not channels['calls']


def test_failed_progress_commit_after_send_is_uncertain_without_repeat(project, channels, renderer):
    def checkpoint(event):
        if event['stage'] == 'send_submitted':
            raise OSError('synthetic receipt failure')
    result = execute(project, {'action': 'channel', 'channel_name': 'fake', 'delivery': 'pdf'}, checkpoint=checkpoint)
    assert result.status in {'partial', 'uncertain'}
    assert len(channels['calls']) == 1 and result.submitted_count == 1


@pytest.mark.parametrize('media_count', [0, 1])
def test_strict_x_upload_incomplete_never_posts_tweet(project, monkeypatch, media_count):
    from row_bot.tools.x_tool import XTool
    monkeypatch.setattr(XTool, '_get_valid_token', lambda _: 'synthetic-token')
    monkeypatch.setattr(XTool, '_is_op_enabled', lambda *_args: True)
    monkeypatch.setattr(XTool, '_upload_media_files', lambda *_args: ['synthetic-media'] * media_count)
    monkeypatch.setattr(XTool, '_api_request', lambda *_args, **_kwargs: pytest.fail('tweet POST must not occur'))
    result = XTool()._x_post('post', text='Synthetic post', media_paths=['a.png', 'b.png'], require_all_media=True)
    assert result == 'Media upload incomplete; no tweet was posted. Uploaded media may remain.'


def test_strict_x_limit_rejected_at_review_without_render(project, monkeypatch):
    project.pages = project.pages * 3
    storage.save_project(project)
    with pytest.raises(client_service.ArtifactError, match='sharing_media_limit'):
        client.prepare_share(project.id, action='x')


def test_published_copy_edit_after_review_is_not_overwritten(project, channels):
    execute(project, {'action': 'publish'})
    current = client_service.read_artifact(project.id)
    review = client.prepare_share(project.id, action='publish')
    path = publish.PUBLISHED_DIR / f'{project.id}.html'
    path.write_bytes(b'External edited publication')
    with pytest.raises(client_service.ArtifactError, match='share_review_changed'):
        execute(current, {'action': 'publish'}, review=review)
    assert path.read_bytes() == b'External edited publication'


def test_external_publication_edit_during_preparation_is_preserved(project, channels):
    execute(project, {'action': 'publish'})
    current = client_service.read_artifact(project.id)
    path = publish.PUBLISHED_DIR / f'{project.id}.html'
    def checkpoint(event):
        if event['stage'] == 'publication_prepared':
            path.write_bytes(b'Racing external publication')
    result = execute(current, {'action': 'publish'}, checkpoint=checkpoint)
    assert result.status == 'partial' and result.code == 'publish_revision_conflict'
    assert path.read_bytes() == b'Racing external publication'


def test_link_send_does_not_rebase_a_changed_channel_configuration_after_own_publish(project, channels):
    def checkpoint(event):
        if event['stage'] == 'publish_metadata_completed':
            channels['config'] = {'scope': 'different-account'}
    result = execute(project, {'action': 'channel', 'channel_name': 'fake', 'delivery': 'link'}, checkpoint=checkpoint)
    assert result.status == 'partial' and result.code == 'share_review_changed'
    assert not channels['calls']


def test_tunnel_exception_is_uncertain_after_static_copy_and_never_blindly_retried(project, channels, monkeypatch):
    calls = []
    monkeypatch.setattr(publish.tunnel_manager, 'get_url', lambda *_args: None)
    monkeypatch.setattr(publish.tunnel_manager, 'is_available', lambda: True)
    def attempted(*_args, **_kwargs):
        calls.append(1)
        raise TimeoutError('synthetic uncertain tunnel result')
    monkeypatch.setattr(publish.tunnel_manager, 'start_tunnel', attempted)
    result = execute(project, {'action': 'publish', 'remote': True})
    assert result.status == 'uncertain' and calls == [1]
    assert (publish.PUBLISHED_DIR / f'{project.id}.html').is_file()


def test_channel_link_sends_only_after_own_saved_publication(project, channels):
    result = execute(project, {'action': 'channel', 'channel_name': 'fake', 'delivery': 'link'})
    assert result.status == 'submitted', result
    assert result.submitted_count == 1 and result.url in channels['calls'][0][2]


@pytest.mark.parametrize('succeeds', [True, False])
def test_x_service_preserves_complete_or_unknown_outcome_without_real_requests(project, renderer, monkeypatch, succeeds):
    from row_bot.tools.x_tool import XTool
    monkeypatch.setattr(XTool, 'get_config', lambda *_args: ['x_post_tweet'])
    monkeypatch.setattr(XTool, '_get_valid_token', lambda _: 'synthetic-token')
    monkeypatch.setattr(XTool, '_is_op_enabled', lambda *_args: True)
    monkeypatch.setattr(XTool, '_upload_media_files', lambda *_args: ['first', 'second'])
    calls = []
    def request(*_args, **_kwargs):
        calls.append(1)
        if not succeeds:
            raise RuntimeError('Synthetic ambiguous transport')
        return {'data': {'id': 'synthetic-tweet'}}
    monkeypatch.setattr(XTool, '_api_request', request)
    result = execute(project, {'action': 'x'})
    assert result.status == ('submitted' if succeeds else 'uncertain')
    assert calls == [1]


@pytest.mark.parametrize('destination', ['link', 'pdf', 'x'])
def test_strict_share_cannot_fall_back_to_unretained_or_unreviewed_legacy_owner(project, channels, destination):
    from row_bot.designer import share
    with pytest.raises(ValueError, match='sharing_'):
        if destination == 'x':
            share.share_project_to_x(project, strict=True, validate=lambda: None, checkpoint=lambda *_args: None)
        else:
            share.share_project_to_channel(project, 'fake', delivery=destination, strict=True,
                                           validate=lambda: None, checkpoint=lambda *_args: None)
    assert not channels['calls'] and not publish.PUBLISHED_DIR.exists()


def test_leaf_replacement_during_publication_retirement_is_retained_and_never_overwritten(project, channels, monkeypatch):
    from row_bot.developer import edits
    execute(project, {'action': 'publish'})
    path = publish.PUBLISHED_DIR / f'{project.id}.html'
    original = path.read_bytes()
    rename = edits._rename_edit_no_replace
    def swap_before_retire(source, destination, **kwargs):
        if source == path or str(source) == path.name:
            path.write_bytes(b'Concurrent external publication')
        rename(source, destination, **kwargs)
    monkeypatch.setattr(edits, '_rename_edit_no_replace', swap_before_retire)
    result = execute(client_service.read_artifact(project.id), {'action': 'publish'})
    assert result.status == 'partial' and result.code == 'publish_revision_conflict'
    assert path.read_bytes() == b'Concurrent external publication'
    assert path.read_bytes() != original


@pytest.mark.skipif(os.name == 'nt', reason='Native POSIX descriptor-relative published-parent swap proof')
def test_posix_parent_swap_during_retirement_never_writes_substituted_directory(project, channels, monkeypatch, tmp_path):
    from row_bot.developer import edits
    execute(project, {'action': 'publish'})
    path = publish.PUBLISHED_DIR / f'{project.id}.html'
    original = path.read_bytes()
    retained = storage.DESIGNER_DIR / 'retained-published'
    foreign = tmp_path / 'foreign-published'
    foreign.mkdir()
    rename = edits._rename_edit_no_replace
    def swap(source, destination, **kwargs):
        if str(source) == path.name:
            publish.PUBLISHED_DIR.rename(retained)
            publish.PUBLISHED_DIR.symlink_to(foreign, target_is_directory=True)
        return rename(source, destination, **kwargs)
    monkeypatch.setattr(edits, '_rename_edit_no_replace', swap)
    result = execute(client_service.read_artifact(project.id), {'action': 'publish'})
    assert result.status == 'partial'
    assert list(foreign.iterdir()) == []
    preserved = list(retained.iterdir()) + list((storage.DESIGNER_DIR / 'publish_recovery').glob('*/previous'))
    assert any(item.read_bytes() == original for item in preserved)
