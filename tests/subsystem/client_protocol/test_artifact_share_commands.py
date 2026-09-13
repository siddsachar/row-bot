"""Sharing authority and durable effects through actual API with fake channels."""
# ruff: noqa: F811 -- reused pytest fixtures.
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from row_bot.designer import publish, storage
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_artifact_export_commands import prepare

pytestmark = pytest.mark.subsystem


@pytest.fixture
def sharing(artifact_service, monkeypatch):
    from row_bot.channels import config, registry
    from row_bot.channels.base import ChannelCapabilities
    from row_bot.designer import share

    state = {'calls': [], 'recipient': 'synthetic-recipient'}
    class FakeChannel:
        name = 'fake'
        display_name = 'Synthetic channel'
        capabilities = ChannelCapabilities(document_out=True)
        def is_running(self): return True
        def is_configured(self): return True
        def get_default_target(self): return state['recipient']
        def send_document(self, target, path, caption=None):
            state['calls'].append((target, path.read_bytes() if hasattr(path, 'read_bytes') else str(path)))
    channel = FakeChannel()
    monkeypatch.setattr(registry, 'get', lambda name: channel if name == 'fake' else None)
    monkeypatch.setattr(share, 'channel_registry', registry)
    monkeypatch.setattr(config, 'get_all', lambda _name: {'scope': 'synthetic'})
    monkeypatch.setattr(publish, 'PUBLISHED_DIR', storage.DESIGNER_DIR / 'published')
    monkeypatch.setattr(publish.tunnel_manager, 'start_tunnel', lambda *a, **k: pytest.fail('Unexpected network tunnel'))
    return artifact_service, state


def review(client, headers, created, options):
    response = client.post(f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}/sharing-review",
                           json=options, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def send(client, headers, created, payload, identity):
    return _command(client, headers, 'artifact.share', payload, target=created['conversation_id'],
                    revision=created['revision'], command_id=identity, key=identity)


def test_review_is_inert_then_exact_send_is_once_and_receipts_omit_recipient(sharing):
    owner, state = sharing
    with _client(owner) as client:
        _, headers = bootstrap(client)
        created, export = prepare(client, headers, 'deck')
        options = {'action': 'channel', 'channel_name': 'fake', 'delivery': 'html'}
        reviewed = review(client, headers, created, options)
        assert not state['calls'] and reviewed['recipient'] == state['recipient']
        payload = {'target': export['target'], 'options': options, 'review_id': reviewed['review_id'], 'nonce': reviewed['nonce']}
        identity = str(uuid4())
        first = send(client, headers, created, payload, identity)
        assert first.status_code == 200, first.text
        assert first.json()['share_outcome']['status'] == 'submitted'
        assert len(state['calls']) == 1
        assert send(client, headers, created, payload, identity).json() == first.json()
        assert len(state['calls']) == 1
        with admissions.transaction() as conn:
            stored = dict(conn.execute('SELECT * FROM client_commands WHERE command_id=?', (identity,)).fetchone())
        assert state['recipient'] not in str(stored) and reviewed['nonce'] not in str(stored)


def test_changed_recipient_and_missing_review_proof_cannot_send(sharing):
    owner, state = sharing
    with _client(owner) as client:
        _, headers = bootstrap(client)
        created, export = prepare(client, headers, 'deck')
        options = {'action': 'channel', 'channel_name': 'fake', 'delivery': 'html'}
        reviewed = review(client, headers, created, options)
        payload = {'target': export['target'], 'options': options, 'review_id': reviewed['review_id'], 'nonce': 'invalid'}
        expired = send(client, headers, created, payload, str(uuid4()))
        assert expired.status_code == 409 and expired.json()['code'] == 'approval_expired'
        payload['nonce'] = reviewed['nonce']
        state['recipient'] = 'changed-synthetic-recipient'
        rejected = send(client, headers, created, payload, str(uuid4()))
        assert rejected.status_code == 409 and rejected.json()['code'] == 'share_review_changed', rejected.text
        assert state['calls'] == []


def test_lost_completed_receipt_never_resends(sharing, monkeypatch):
    owner, state = sharing
    with _client(owner) as client:
        _, headers = bootstrap(client)
        created, export = prepare(client, headers, 'deck')
        options = {'action': 'channel', 'channel_name': 'fake', 'delivery': 'html'}
        reviewed = review(client, headers, created, options)
        payload = {'target': export['target'], 'options': options, 'review_id': reviewed['review_id'], 'nonce': reviewed['nonce']}
        identity = str(uuid4())
        monkeypatch.setattr(admissions, 'complete_command', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Synthetic receipt loss')))
        assert send(client, headers, created, payload, identity).status_code == 503
        assert len(state['calls']) == 1
        replay = send(client, headers, created, payload, identity)
        assert replay.status_code == 200, replay.text
        assert replay.json()['status'] == 'partial' and replay.json()['code'] == 'sharing_outcome_uncertain'
        assert len(state['calls']) == 1


@pytest.mark.parametrize('mode', ['deck', 'document', 'landing', 'app_mockup', 'storyboard'])
def test_local_published_copy_uses_isolated_directory_and_retains_pairing(sharing, mode):
    owner, state = sharing
    with _client(owner) as client:
        _, headers = bootstrap(client)
        created, exported = prepare(client, headers, mode)
        reviewed = review(client, headers, created, {'action': 'publish'})
        assert reviewed['requires_pairing'] and not publish.PUBLISHED_DIR.exists()
        payload = {'target': exported['target'], 'options': {'action': 'publish'},
                   'review_id': reviewed['review_id'], 'nonce': reviewed['nonce']}
        result = send(client, headers, created, payload, str(uuid4()))
        assert result.status_code == 200, result.text
        outcome = result.json()['share_outcome']
        assert outcome['status'] == 'published' and outcome['link_kind'] == 'local'
        assert outcome['url'].startswith('http://127.0.0.1:')
        assert publish.PUBLISHED_DIR.joinpath(created['resource_id'] + '.html').is_file()
        assert state['calls'] == []


def test_channel_pages_cover_registered_adapters_and_reject_changed_snapshot(sharing, monkeypatch):
    from row_bot.channels import registry
    from types import SimpleNamespace
    owner, state = sharing
    adapters = [SimpleNamespace(name=f'fake-{i:03}', display_name=f'Synthetic {i}',
                is_running=lambda: True, is_configured=lambda: True) for i in range(115)]
    monkeypatch.setattr(registry, 'all_channels', lambda: adapters)
    with _client(owner) as client:
        _, headers = bootstrap(client)
        first = client.get('/api/v1/sharing/channels', headers=headers)
        assert first.status_code == 200, first.text
        page = first.json()
        items = list(page['items'])
        original_cursor = page['next_cursor']
        while page['next_cursor']:
            next_page = client.get('/api/v1/sharing/channels', params={'cursor': page['next_cursor']}, headers=headers)
            assert next_page.status_code == 200, next_page.text
            page = next_page.json()
            assert len(page['items']) <= 50
            items.extend(page['items'])
        assert len(items) == 115 and len({item['name'] for item in items}) == 115
        adapters.pop()
        stale = client.get('/api/v1/sharing/channels', params={'cursor': original_cursor}, headers=headers)
        assert stale.status_code == 410 and stale.json()['code'] == 'cursor_expired'
        assert state['calls'] == []
