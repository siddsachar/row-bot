"""Custom endpoints on a hosted Row-Bot and the host's private networks (H16), and what a refresh says
when an endpoint refuses its key.

Everything runs over a fake network below httpx: names resolve through a fake DNS, and every
connection reaches a fake model server from the address it was given (httpcore's network backends
are replaced), so the check sees the address each connection actually reached. Nothing leaves the
machine.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import socket

import httpcore
import pytest
from langchain_core.messages import HumanMessage

from row_bot.application import provider_configuration_controls as controls
from row_bot.providers import auth_store, config, custom
from row_bot.providers.endpoint_addresses import EndpointAddressRefused
from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
from tests.subsystem.providers import test_provider_configuration_controls as configuration
from tests.subsystem.providers import test_provider_settings_controls as fixtures

store = fixtures.store
pytestmark = pytest.mark.subsystem
command, execute = configuration.command, configuration.execute


class _Network:
    """Fake DNS answers (a list, or a callable for a name whose answer changes) and the requests that
    reached a server, as ``(address, request line)``."""

    def __init__(self) -> None:
        self.dns: dict[str, object] = {}
        self.received: list[tuple[str, str]] = []
        self.status = 200

    def answers(self, host: str) -> list[str]:
        answer = self.dns[host]
        return list(answer() if callable(answer) else answer)

    def respond(self, address: str, request: bytes) -> bytes:
        line = request.split(b"\r\n", 1)[0].decode()
        self.received.append((address, line))
        if self.status != 200:
            status, body = f"{self.status} Refused", b'{"error": "refused"}'
        elif " /v1/models " in line:
            status, body = "200 OK", json.dumps({"data": [{"id": "office-model"}]}).encode()
        elif " /v1/chat/completions " in line:
            status, body = "200 OK", json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        else:
            status, body = "404 Not Found", b"{}"
        return (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n").encode() + body


class _Connection:
    def __init__(self, network: _Network, address: str, port: int) -> None:
        self.network, self.address, self.port = network, address, port
        self.request, self.answered = b"", False

    def read(self) -> bytes:
        if self.answered:
            return b""
        self.answered = True
        return self.network.respond(self.address, self.request)

    def extra(self, info: str) -> object:
        return (self.address, self.port) if info == "server_addr" else None


class _Stream(httpcore.NetworkStream):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self.connection.read()

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.connection.request += buffer

    def close(self) -> None:
        pass

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # noqa: ANN001
        return self

    def get_extra_info(self, info: str) -> object:
        return self.connection.extra(info)


class _AsyncStream(httpcore.AsyncNetworkStream):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self.connection.read()

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.connection.request += buffer

    async def aclose(self) -> None:
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # noqa: ANN001
        return self

    def get_extra_info(self, info: str) -> object:
        return self.connection.extra(info)


@pytest.fixture
def network(monkeypatch):
    fake = _Network()
    resolve = socket.getaddrinfo

    def dns(host, port, *args, **kwargs):
        if host not in fake.dns:
            return resolve(host, port, *args, **kwargs)  # an IP literal (the suite refuses real names)
        return [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
                for address in fake.answers(host)]

    def connected(host: str, port: int) -> _Connection:
        return _Connection(fake, str(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]), port)

    def connect(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001
        return _Stream(connected(host, port))

    async def aconnect(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001
        return _AsyncStream(connected(host, port))

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", aconnect)
    return fake


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")


def endpoint(base_url: str, *, allow: bool = False, endpoint_id: str = "office") -> dict:
    value = asdict(controls.ProviderEndpointFields(endpoint_id, "Office GPU", base_url))
    value["allow_private_address"] = allow
    return value


def revision() -> str:
    return config.provider_config_revision(config.load_provider_config(strict=True))


@contextmanager
def refusal(code: str):
    """The request is refused with this code (a refusal's text is its plain reason)."""
    with pytest.raises(config.ProviderConfigError) as error:
        yield error
    assert error.value.code == code


def chat(endpoint_id: str = "office") -> ChatOpenAICompatible:
    saved = custom.get_custom_endpoint(endpoint_id)
    return ChatOpenAICompatible(model_name="office-model", base_url=saved["base_url"], endpoint=saved)


@pytest.mark.parametrize("address", ["10.0.0.5", "169.254.10.20", "192.168.1.20", "[fd12:3456::5]"])
def test_an_endpoint_to_the_hosts_private_network_asks_first_on_a_pod(store, hosted, network, address):
    base_url = f"http://{address}:11434/v1"
    with refusal("endpoint_private_address"):
        controls.review_provider_configuration("provider.endpoint.create", revision(), endpoint(base_url),
                                               validate=lambda: None)

    create = command("provider.endpoint.create", endpoint(base_url))
    with refusal("endpoint_private_address"):
        execute(create)
    assert custom.get_custom_endpoint("office") is None
    receipt = controls.read_provider_configuration_receipt(owner_id="owner", command_id=create["command_id"],
                                                           validate=lambda: None)
    assert receipt["status"] == "rejected"
    assert network.received == []


def test_once_the_owner_allows_it_the_endpoint_saves_and_reaches_its_server(store, hosted, network):
    execute(command("provider.endpoint.create", endpoint("http://10.0.0.5:11434/v1", allow=True)))

    [saved] = controls.read_provider_configuration().items
    assert saved.fields.allow_private_address is True
    execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))
    assert [model["id"] for model in custom.get_custom_endpoint("office")["models"]] == ["office-model"]
    assert chat().invoke([HumanMessage(content="hi")]).content == "hello"
    assert [address for address, _ in network.received] == ["10.0.0.5", "10.0.0.5"]

    # Editing other fields keeps the OK; another private address asks again.
    renamed = endpoint("http://10.0.0.5:11434/v1", allow=True)
    renamed["display_name"] = "Office GPU 2"
    execute(command("provider.endpoint.save", renamed))
    with refusal("endpoint_private_address"):
        execute(command("provider.endpoint.save", endpoint("http://10.0.0.6:11434/v1")))
    assert custom.get_custom_endpoint("office")["base_url"] == "http://10.0.0.5:11434/v1"


def test_a_name_that_leads_to_a_private_address_asks_too(store, hosted, network):
    network.dns["models.example.com"] = ["93.184.216.34", "10.1.2.3"]

    with refusal("endpoint_private_address"):
        execute(command("provider.endpoint.create", endpoint("https://models.example.com/v1")))
    assert network.received == []


def test_a_name_that_answers_differently_after_the_check_is_refused_before_anything_is_sent(store, hosted, network):
    answers = iter([["93.184.216.34"]])
    network.dns["models.example.com"] = lambda: next(answers, ["10.1.2.3"])  # DNS rebinding
    execute(command("provider.endpoint.create", endpoint("http://models.example.com/v1")))

    # The person's refresh, a background refresh, and a chat turn all stop with a plain reason.
    with refusal("endpoint_private_address"):
        execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))
    with pytest.raises(EndpointAddressRefused) as background:
        custom.refresh_custom_endpoint_models("office")
    assert str(background.value) == (
        "This custom endpoint's address (10.1.2.3) is on your host's private network. To use it, "
        "edit the endpoint in Settings › Providers and allow it.")
    with pytest.raises(EndpointAddressRefused, match="private network"):
        chat().invoke([HumanMessage(content="hi")])
    assert network.received == []
    assert custom.get_custom_endpoint("office")["last_probe"]["classification"] == "unavailable"


def test_a_cloud_metadata_address_is_never_reached_even_when_allowed(store, hosted, network):
    with refusal("endpoint_address_blocked"):
        execute(command("provider.endpoint.create", endpoint("http://169.254.169.254/v1", allow=True)))

    network.dns["models.example.com"] = lambda: ["10.1.2.3"] if not network.received else ["169.254.169.254"]
    execute(command("provider.endpoint.create", endpoint("http://models.example.com/v1", allow=True)))
    execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))
    with pytest.raises(EndpointAddressRefused, match="hosting company's own computer") as refused:
        chat().invoke([HumanMessage(content="hi")])
    assert refused.value.code == "endpoint_address_blocked"
    assert [address for address, _ in network.received] == ["10.1.2.3"]


def test_the_pod_itself_needs_no_ok(store, hosted, network):
    execute(command("provider.endpoint.create", endpoint("http://127.0.0.1:8123/v1")))
    execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))

    assert chat().invoke([HumanMessage(content="hi")]).content == "hello"
    assert [address for address, _ in network.received] == ["127.0.0.1", "127.0.0.1"]


def test_row_bots_own_loopback_server_never_asks_on_a_pod_and_saves_as_before(store, hosted, network, monkeypatch):
    """Learning's personal-model server: a managed loopback endpoint keeps working on a hosted server,
    with nothing to ask and its saved shape unchanged."""
    monkeypatch.setattr(custom, "managed_connection", lambda found, start=True: ("http://127.0.0.1:8124/v1", "k"))
    saved = {"id": "row-bot-personal-row-bot", "name": "Personal model", "base_url": "http://127.0.0.1:8124/v1",
             "profile": "llama_cpp", "execution_location": "local", "auth_required": False, "managed": "learning",
             "manual_capabilities": {"context_window": 32768}}
    custom.save_custom_endpoint(saved)
    [stored] = config.load_provider_config(strict=True)["custom_endpoints"]
    assert "private_address_allowed" not in stored

    custom.refresh_custom_endpoint_models("row-bot-personal-row-bot")
    probe = custom.probe_custom_endpoint("custom_openai_row-bot-personal-row-bot", "office-model")
    model = ChatOpenAICompatible(model_name="office-model", base_url=saved["base_url"],
                                 endpoint=custom.get_custom_endpoint("row-bot-personal-row-bot"))

    assert probe["chat_ok"] is True
    assert model.invoke([HumanMessage(content="hi")]).content == "hello"
    assert {address for address, _ in network.received} == {"127.0.0.1"}


def test_the_desktop_reaches_a_server_on_its_network_as_before(store, network):
    network.dns["gpu.lan"] = ["192.168.1.20"]
    execute(command("provider.endpoint.create", endpoint("http://gpu.lan:11434/v1")))
    execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))

    assert chat().invoke([HumanMessage(content="hi")]).content == "hello"
    [saved] = controls.read_provider_configuration().items
    assert saved.fields.allow_private_address is False
    assert [address for address, _ in network.received] == ["192.168.1.20", "192.168.1.20"]


@pytest.mark.parametrize("operation", ["provider.endpoint.refresh", "provider.endpoint.probe"])
def test_a_refused_key_says_so_instead_of_couldnt_reach(store, network, operation):
    execute(command("provider.endpoint.create", endpoint("http://127.0.0.1:8123/v1")))
    network.status = 401

    refresh = command(operation, {"endpoint_id": "office"})
    with refusal("endpoint_key_rejected"):
        execute(refresh)
    receipt = controls.read_provider_configuration_receipt(owner_id="owner", command_id=refresh["command_id"],
                                                           validate=lambda: None)
    assert receipt["status"] == "rejected"
    assert custom.get_custom_endpoint("office")["last_probe"]["classification"] == "unavailable"


def test_a_missing_key_says_so_instead_of_couldnt_reach(store, network):
    keyed = endpoint("http://127.0.0.1:8123/v1")
    keyed["auth_required"] = True
    execute(command("provider.endpoint.create", keyed))
    assert auth_store.get_provider_secret("custom_openai_office", "api_key") == ""

    with refusal("endpoint_key_missing"):
        execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))
    assert network.received == []


def test_a_server_that_doesnt_answer_still_reads_as_unreachable(store, network, monkeypatch):
    async def nobody_listening(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ANN001
        raise httpcore.ConnectError("connection refused")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", nobody_listening)
    execute(command("provider.endpoint.create", endpoint("http://127.0.0.1:8123/v1")))

    with refusal("endpoint_unreachable"):
        execute(command("provider.endpoint.refresh", {"endpoint_id": "office"}))
