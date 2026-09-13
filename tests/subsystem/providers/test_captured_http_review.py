"""Independent captured HTTP owner review; all transports are in-memory fakes."""
import asyncio

import httpx
import pytest

from row_bot.providers.runtime import captured_http_clients
from row_bot.providers.transports import cancellable_http

pytestmark = pytest.mark.subsystem


@pytest.fixture
def clients(monkeypatch):
    requests, created = [], []
    def response(request):
        requests.append(request)
        return httpx.Response(200, json={'accepted': True})
    def sync(**kwargs):
        client = httpx.Client(transport=httpx.MockTransport(response), **kwargs)
        created.append(client)
        return client
    def asynchronous(**kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(response), **kwargs)
        created.append(client)
        return client
    monkeypatch.setattr(cancellable_http, 'cancellable_http_client', sync)
    monkeypatch.setattr(cancellable_http, 'cancellable_async_http_client', asynchronous)
    return requests, created


def test_async_constructor_failure_closes_the_already_created_sync_client(clients, monkeypatch):
    requests, created = clients
    sentinel = RuntimeError('synthetic async constructor failure')
    def fail(**_kwargs):
        raise sentinel
    monkeypatch.setattr(cancellable_http, 'cancellable_async_http_client', fail)
    try:
        with pytest.raises(RuntimeError) as failure:
            with captured_http_clients('https://provider.invalid/v1', lambda: None):
                pytest.fail('Incomplete client pair was exposed')
        assert failure.value is sentinel
        assert created[0].is_closed
        assert not requests
    finally:
        created[0].close()


@pytest.mark.parametrize('base', ['https://provider.invalid:443/v1', 'http://provider.invalid:80/v1'])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_explicit_default_port_matches_httpx_canonical_request_origin(clients, base, asynchronous):
    requests, created = clients
    with captured_http_clients(base, lambda: None) as (sync, async_client):
        if asynchronous:
            result = asyncio.run(async_client.get(base + '/chat'))
        else:
            result = sync.get(base + '/chat')
        assert result.json() == {'accepted': True}
    assert len(requests) == 1 and all(client.is_closed for client in created)


@pytest.mark.parametrize('url', ['https://other.invalid/v1/chat', 'http://provider.invalid/v1/chat',
    'https://provider.invalid:444/v1/chat', 'https://provider.invalid/v11/chat',
    'https://provider.invalid/v1/%2e%2e/outside', 'https://provider.invalid/v1/%2e%2e%2foutside',
    'https://provider.invalid/v1/%252e%252e/outside', 'https://provider.invalid/v1/%252e%252e%252foutside',
    'https://provider.invalid/v1/%2e%2e%5coutside', 'https://provider.invalid/v1/%252e%252e%255coutside'])
def test_changed_origin_or_endpoint_prefix_is_rejected_before_transport(clients, url):
    requests, created = clients
    with captured_http_clients('https://provider.invalid/v1', lambda: None) as (sync, _):
        with pytest.raises(ValueError, match='document_provider_endpoint_changed'):
            sync.get(url)
    assert not requests and all(client.is_closed for client in created)


@pytest.mark.parametrize('base,url', [
    ('https://provider.invalid/v1', 'https://provider.invalid/v1/models/a%20b'),
    ('https://provider.invalid/v1', 'https://provider.invalid/v1/models/%E2%9C%93'),
    ('https://provider.invalid/v1/tenant%20name', 'https://provider.invalid/v1/tenant%20name/chat'),
])
def test_nonstructural_encoded_identifiers_remain_supported(clients, base, url):
    requests, created = clients
    with captured_http_clients(base, lambda: None) as (sync, _):
        assert sync.get(url).json() == {'accepted': True}
    assert len(requests) == 1 and all(client.is_closed for client in created)


def test_revoked_initial_authority_creates_neither_client(clients):
    requests, created = clients
    sentinel = RuntimeError('synthetic revoked authority')
    def refuse():
        raise sentinel
    with pytest.raises(RuntimeError) as failure:
        with captured_http_clients('https://provider.invalid/v1', refuse):
            pytest.fail('Revoked context exposed')
    assert failure.value is sentinel and not created and not requests
