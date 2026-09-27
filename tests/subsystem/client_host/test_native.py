from __future__ import annotations

from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Callable

import pytest

from row_bot.native_client import (
    NativeClientBridge,
    NativeDocumentAuthority,
    NativePickerRequest,
    NativeSelectionAuthority,
    PyWebViewDriver,
    attach_native_client,
    safe_external_url,
)


def _picker_payload(**changes):
    payload = {"intentId": "intent_1", "intent": "open_existing",
               "conversationId": "conversation_1", "destination": "workspace"}
    payload.update(changes)
    return payload


class Driver:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.on_select: Callable[[], None] = lambda: None
        self.on_save: Callable[[], None] = lambda: None
        self.selected: str | None = "/synthetic/selected.txt"

    def capabilities(self) -> list[str]:
        return ["select_file", "select_folder", "clipboard_read", "clipboard_write", "open_external", "managed_window", "save"]

    def select(self, kind: str) -> str | None:
        self.calls.append("select:" + kind)
        self.on_select()
        return self.selected

    def clipboard_read(self) -> str:
        self.calls.append("clipboard_read")
        return "fixture clipboard"

    def clipboard_write(self, text: str) -> bool:
        self.calls.append("clipboard_write:" + text)
        return True

    def open_external(self, url: str) -> bool:
        self.calls.append("open_external:" + url)
        return True

    def managed_window(self, route: str) -> bool:
        self.calls.append("managed_window:" + route)
        return True

    def save(self, reference: str, suggested_name: str, authorized: Callable[[], bool]) -> bool:
        self.on_save()
        if not authorized():
            return False
        self.calls.append("save:" + reference)
        return True


@pytest.fixture
def native():
    driver = Driver()
    state = {"url": "http://localhost:8080/app-v2/", "clock": 100.0, "authorized": True}
    registered: list[tuple[NativePickerRequest, NativeSelectionAuthority, Path]] = []
    def authenticate(attestation, context):
        if attestation != "server_attestation" or context.instance_id != "instance":
            return None
        return NativeDocumentAuthority("session_1", "policy_1", "authority_1")
    def authorize(authority, context):
        return (state["authorized"] and authority.authority_grant == "authority_1"
                and context.instance_id == "instance" and context.window_id == "window")
    def register(request, authority, path: Path) -> str:
        registered.append((request, authority, path))
        return "fixture_reference"
    bridge = NativeClientBridge(instance_id="instance", window_id="window", origin="http://localhost:8080",
                                current_url=lambda: state["url"], driver=driver, register_selection=register,
                                authenticate_document=authenticate, authorize_document=authorize,
                                clock=lambda: state["clock"])
    proof = bridge._bind_loaded_document()
    assert proof
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "server_attestation"})["status"] == "ok"
    return bridge, proof, driver, state, registered


def test_native_selection_registers_backend_ref_and_never_returns_path(native) -> None:
    bridge, proof, driver, _, registered = native
    result = bridge.native_client_dispatch(proof, "select_file", _picker_payload())
    assert result == {"status": "ok", "value": {"reference": "fixture_reference", "kind": "file"}}
    request, authority, path = registered[0]
    assert request == NativePickerRequest("file", "intent_1", "open_existing", "conversation_1", "workspace")
    assert authority == NativeSelectionAuthority("instance", "session_1", "window", proof["epoch"],
                                                  "policy_1", "authority_1")
    assert path == Path("/synthetic/selected.txt")
    assert driver.calls == ["select:file"]
    driver.selected = None
    assert bridge.native_client_dispatch(proof, "select_folder", _picker_payload()) == {"status": "cancelled"}


@pytest.mark.parametrize("field,value", [("instanceId", "foreign"), ("windowId", "foreign"), ("epoch", 90), ("token", "fake")])
def test_spoofed_identity_cannot_invoke_driver(native, field: str, value: object) -> None:
    bridge, proof, driver, _, _ = native
    proof[field] = value
    assert bridge.native_client_dispatch(proof, "select_file", _picker_payload())["status"] == "unavailable"
    assert driver.calls == []


@pytest.mark.parametrize("change", ["foreign", "legacy", "reload", "close", "expiry"])
def test_navigation_reload_close_and_expiry_invalidate_proof(native, change: str) -> None:
    bridge, proof, driver, state, _ = native
    if change in {"foreign", "legacy"}:
        state["url"] = "https://foreign.invalid/app-v2/" if change == "foreign" else "http://localhost:8080/"
    elif change == "reload":
        assert bridge._bind_loaded_document() != proof
    elif change == "close":
        bridge._invalidate()
    else:
        state["clock"] += 1800
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "unavailable"
    assert driver.calls == []


def test_late_picker_and_save_do_not_register_or_write_after_navigation(native) -> None:
    bridge, proof, driver, _, registered = native
    driver.on_select = bridge._invalidate
    assert bridge.native_client_dispatch(proof, "select_file", _picker_payload())["status"] == "unavailable"
    assert not registered
    proof = bridge._bind_loaded_document()
    driver.on_save = bridge._invalidate
    assert bridge.native_client_dispatch(proof, "save", {"reference": "fixture", "name": "fixture.txt"})["status"] == "unavailable"
    assert driver.calls == ["select:file"]


@pytest.mark.parametrize("operation", ["select_file", "clipboard_read", "clipboard_write", "discover"])
def test_reentrant_trusted_callback_cannot_return_data_after_revocation(native, monkeypatch: pytest.MonkeyPatch,
                                                                     operation: str) -> None:
    bridge, proof, driver, _, _ = native
    payload = {}
    def revoked(value):
        bridge._invalidate()
        return value
    if operation == "select_file":
        payload = _picker_payload()
        monkeypatch.setattr(bridge, "_register", lambda _request, _authority, _path: revoked("fixture_reference"))
    elif operation == "clipboard_read":
        monkeypatch.setattr(driver, "clipboard_read", lambda: revoked("private fixture sentinel"))
    elif operation == "clipboard_write":
        monkeypatch.setattr(driver, "clipboard_write", lambda _text: revoked(True))
        payload = {"text": "fixture"}
    else:
        monkeypatch.setattr(driver, "capabilities", lambda: revoked(["discover"]))
    assert bridge.native_client_dispatch(proof, operation, payload) == {
        "status": "unavailable", "reason": "native_proof_required"}


@pytest.mark.parametrize("operation,payload", [
    ("open_external", {"url": "javascript:alert(1)"}), ("open_external", {"url": "https://user:secret@fixture.invalid/"}),
    ("managed_window", {"route": "https://foreign.invalid/"}), ("managed_window", {"route": "/app-v2/../api/launcher-shutdown"}),
    ("select_file", {"initial_dir": "/private"}), ("save", {"reference": "/private/file", "name": "a.txt"}),
    ("save", {"reference": "fixture", "name": "../file"}), ("clipboard_write", {"text": "x" * 65537}),
    ("shell", {"command": "anything"}),
])
def test_invalid_or_arbitrary_native_payloads_have_zero_effect(native, operation: str, payload: dict) -> None:
    bridge, proof, driver, _, registered = native
    assert bridge.native_client_dispatch(proof, operation, payload)["status"] == "unavailable"
    assert driver.calls == [] and registered == []


def test_all_narrow_operations_and_platform_discovery(native) -> None:
    bridge, proof, driver, _, _ = native
    discovery = bridge.native_client_dispatch(proof, "discover", {})
    assert discovery["value"]["kind"] == "pywebview"
    assert "token" not in discovery["value"]
    for operation, payload in [("clipboard_read", {}), ("clipboard_write", {"text": "fixture"}),
                               ("open_external", {"url": "https://example.invalid/help"}),
                               ("managed_window", {"route": "/app-v2/"}),
                               ("save", {"reference": "fixture_reference", "name": "fixture.txt"})]:
        assert bridge.native_client_dispatch(proof, operation, payload)["status"] == "ok"
    assert len(driver.calls) == 5


def test_buddy_placement_requires_capability_proof_and_closed_payload(native) -> None:
    bridge, proof, driver, _, _ = native
    calls = []
    driver.capabilities = lambda: ["buddy_placement"]
    driver.buddy_placement = lambda action, x, y: (
        calls.append((action, x, y)) or {"placement": "desktop" if action == "tear_off" else "docked", "visible": True}
    )
    assert bridge.native_client_dispatch(proof, "buddy_placement", {"action": "tear_off", "x": 500, "y": -200}) == {
        "status": "ok", "value": {"placement": "desktop", "visible": True}}
    assert bridge.native_client_dispatch(proof, "buddy_placement", {"action": "dock"})["status"] == "ok"
    # Actions are a closed set of shapes; which actions a window may use is its
    # role's decision (tests/subsystem/buddy/test_native_host.py).
    for payload in ({"action": "tear_off", "x": "500", "y": 1},
                    {"action": "tear_off", "x": 1, "y": 1, "port": 80},
                    {"action": "hide", "x": 1}, {"action": "minimize"},
                    {"action": "status", "path": "/private"}):
        assert bridge.native_client_dispatch(proof, "buddy_placement", payload)["status"] == "unavailable"
    assert calls == [("tear_off", 500, -200), ("dock", None, None)]
    bridge._invalidate()
    assert bridge.native_client_dispatch(proof, "buddy_placement", {"action": "status"})["status"] == "unavailable"
    assert len(calls) == 2


def test_pywebview_driver_advertises_only_injected_buddy_lifecycle() -> None:
    calls = []
    driver = PyWebViewDriver(
        SimpleNamespace(),
        buddy_placement=lambda action, x, y: (
            calls.append((action, x, y)) or {"placement": "desktop", "visible": True}
        ),
    )
    assert "buddy_placement" in driver.capabilities()
    assert driver.buddy_placement("tear_off", 320, -120) == {"placement": "desktop", "visible": True}
    assert calls == [("tear_off", 320, -120)]
    assert "buddy_placement" not in PyWebViewDriver(SimpleNamespace()).capabilities()


def test_no_backend_registrar_means_no_native_picker(native) -> None:
    _, _, driver, _, _ = native
    bridge = NativeClientBridge(
        instance_id="i", window_id="w", origin="http://localhost:8080",
        current_url=lambda: "http://localhost:8080/app-v2/", driver=driver,
        authenticate_document=lambda _attestation, _context: NativeDocumentAuthority("session", "policy", "grant"),
        authorize_document=lambda _authority, _context: True,
    )
    proof = bridge._bind_loaded_document()
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attestation"})["status"] == "ok"
    assert bridge.native_client_dispatch(proof, "select_file", _picker_payload())["status"] == "unavailable"
    assert driver.calls == []


def test_native_exception_never_exposes_private_paths(native) -> None:
    bridge, proof, driver, _, _ = native
    def fail() -> None:
        raise RuntimeError("private path and secret sentinel")
    driver.on_select = fail
    assert bridge.native_client_dispatch(proof, "select_file", _picker_payload()) == {"status": "unavailable", "reason": "operation_failed"}


def test_pywebview_driver_uses_exact_supplied_window_and_backend_save_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(OPEN_DIALOG="file", FOLDER_DIALOG="folder", SAVE_DIALOG="save"))
    calls = []
    window = SimpleNamespace(create_file_dialog=lambda kind, **kwargs: calls.append((kind, kwargs)) or ["/synthetic/file"])
    saved = []
    driver = PyWebViewDriver(window, save_reference=lambda reference, path: saved.append((reference, path)) or True,
                             open_external=lambda url: calls.append(url) or True)
    assert driver.select("file") == "/synthetic/file"
    assert driver.select("folder") == "/synthetic/file"
    assert not driver.save("fixture", "fixture.txt", lambda: False)
    assert saved == []
    assert driver.save("fixture", "fixture.txt", lambda: True)
    assert saved == [("fixture", Path("/synthetic/file"))]
    assert driver.clipboard_read() is None
    assert not driver.managed_window("/app-v2/")


def test_trusted_attach_installs_document_scoped_hook_and_revokes_on_events() -> None:
    class Event:
        def __init__(self):
            self.handlers = []
        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self
        def fire(self):
            for handler in self.handlers:
                handler()
    scripts = []
    exposed = []
    window = SimpleNamespace(uid="window", get_current_url=lambda: "http://localhost:8080/app-v2/",
                             events=SimpleNamespace(before_load=Event(), closed=Event(), loaded=Event()),
                             expose=lambda callback: exposed.append(callback), evaluate_js=scripts.append)
    bridge = attach_native_client(window, instance_id="i", origin="http://localhost:8080", driver=Driver())
    window.events.loaded.fire()
    assert len(exposed) == 1 and exposed[0].__name__ == "native_client_dispatch"
    assert "__ROW_BOT_NATIVE_CLIENT__" in scripts[0] and "localStorage" not in scripts[0]
    assert "row-bot-native-ready" in scripts[0]
    assert bridge._token
    window.events.before_load.fire()
    assert not bridge._token


@pytest.mark.parametrize("value", ["file:///secret", "//example.invalid", "https://example.invalid:bad", "https://example.invalid/\n", "https://a\\b", "data:text/html,test"])
def test_external_url_schemes_and_malformed_values(value: str) -> None:
    assert safe_external_url(value) is None


def test_trusted_shell_composition_cannot_bind_remote_content() -> None:
    with pytest.raises(ValueError, match="native_origin_must_be_loopback"):
        NativeClientBridge(instance_id="i", window_id="w", origin="https://remote.invalid",
                           current_url=lambda: "https://remote.invalid/app-v2/", driver=Driver())


def test_foreign_navigation_rejection_cannot_restore_old_proof_by_returning(native) -> None:
    bridge, proof, driver, state, _ = native
    state["url"] = "https://remote.invalid/"
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "unavailable"
    state["url"] = "http://localhost:8080/app-v2/"
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "unavailable"
    assert driver.calls == []


def test_workspace_picker_is_explicit_and_headless_default_stays_unavailable(monkeypatch, tmp_path):
    import sys
    from row_bot.application.folder_selections import select_existing_folder
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.native_client import select_existing_workspace_folder
    calls = []
    window = SimpleNamespace(create_file_dialog=lambda kind, **kwargs: calls.append((kind, kwargs)) or [str(tmp_path)])
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[window], FOLDER_DIALOG=17))
    with pytest.raises(ClientPlatformError, match="capability_unavailable"):
        select_existing_folder()
    assert not calls
    assert select_existing_workspace_folder() == tmp_path
    assert calls == [(17, {"allow_multiple": False})]
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(windows=[]))
    with pytest.raises(ClientPlatformError, match="capability_unavailable"):
        select_existing_workspace_folder()


def test_native_capabilities_require_current_authenticated_attestation() -> None:
    driver = Driver()
    state = {"authorized": True, "url": "http://localhost:8080/app-v2/"}
    consumed: set[str] = set()

    def authenticate(attestation, context):
        if attestation in consumed or attestation != "one_time_attestation":
            return None
        consumed.add(attestation)
        return NativeDocumentAuthority("session", "policy", "current_grant")

    bridge = NativeClientBridge(
        instance_id="instance", window_id="window", origin="http://localhost:8080",
        current_url=lambda: state["url"], driver=driver,
        authenticate_document=authenticate,
        authorize_document=lambda authority, context: (
            state["authorized"] and authority.authority_grant == "current_grant"
            and context.instance_id == "instance" and context.window_id == "window"
        ),
    )
    proof = bridge._bind_loaded_document()
    assert proof
    assert bridge.native_client_dispatch(proof, "discover", {}) == {
        "status": "unavailable", "reason": "native_authentication_required"}
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "wrong"}) == {
        "status": "unavailable", "reason": "native_authentication_required"}
    assert driver.calls == []
    assert bridge.native_client_dispatch(
        proof, "discover", {"attestation": "one_time_attestation"})["status"] == "ok"
    state["authorized"] = False
    # The document is still bound; only its grant was refused (B102).
    assert bridge.native_client_dispatch(proof, "clipboard_read", {}) == {
        "status": "unavailable", "reason": "native_authentication_required"}
    state["authorized"] = True
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "unavailable"
    assert bridge.native_client_dispatch(
        proof, "discover", {"attestation": "one_time_attestation"})["status"] == "unavailable"
    assert driver.calls == []


def test_picker_cancel_consumes_exact_intent_without_registering_path(native, monkeypatch) -> None:
    bridge, proof, driver, _, registered = native
    cancelled = []
    driver.selected = None
    monkeypatch.setattr(
        bridge, "_cancel_selection",
        lambda request, authority: cancelled.append((request, authority)),
    )
    assert bridge.native_client_dispatch(proof, "select_folder", _picker_payload()) == {
        "status": "cancelled"}
    assert registered == []
    assert cancelled[0][0].selection_kind == "folder"
    assert cancelled[0][0].intent_id == "intent_1"
    assert cancelled[0][1].session_id == "session_1"


@pytest.mark.parametrize("field", ["intentId", "intent", "conversationId", "destination"])
def test_picker_rejects_missing_exact_scope_field(native, field: str) -> None:
    bridge, proof, driver, _, registered = native
    payload = _picker_payload()
    del payload[field]
    assert bridge.native_client_dispatch(proof, "select_folder", payload) == {
        "status": "unavailable", "reason": "invalid_request"}
    assert driver.calls == [] and registered == []


def test_late_registrar_completion_after_cross_thread_revocation_returns_no_reference(native) -> None:
    bridge, proof, driver, _, registered = native
    entered = threading.Event()
    release = threading.Event()
    result = []

    def register(request, authority, path):
        registered.append((request, authority, path))
        entered.set()
        assert release.wait(2)
        return "late_reference"

    bridge._register = register
    worker = threading.Thread(
        target=lambda: result.append(
            bridge.native_client_dispatch(proof, "select_folder", _picker_payload())
        )
    )
    worker.start()
    assert entered.wait(2)
    bridge._invalidate()
    release.set()
    worker.join(2)
    assert result == [{"status": "unavailable", "reason": "native_proof_required"}]
    assert "late_reference" not in str(result)


def test_terminal_open_is_exact_native_authority_and_revocation_closes_document() -> None:
    opened = []
    revoked = []
    bridge = NativeClientBridge(
        instance_id="instance",
        window_id="window",
        origin="http://localhost:8080",
        current_url=lambda: "http://localhost:8080/app-v2/",
        driver=Driver(),
        authenticate_document=lambda _token, _context: NativeDocumentAuthority(
            "session", "policy", "grant"
        ),
        authorize_document=lambda _authority, _context: True,
        open_terminal=lambda authority, conversation: opened.append(
            (authority, conversation)
        )
        or "terminal_reference",
        revoke_document=lambda authority, context: revoked.append((authority, context)),
    )
    proof = bridge._bind_loaded_document()
    assert proof
    discovery = bridge.native_client_dispatch(
        proof, "discover", {"attestation": "server_attestation"}
    )
    assert "terminal_open" in discovery["value"]["capabilities"]
    assert bridge.native_client_dispatch(
        proof, "terminal_open", {"conversationId": "conversation_1"}
    ) == {"status": "ok", "value": {"terminalId": "terminal_reference"}}
    authority, conversation = opened[0]
    assert authority == NativeSelectionAuthority(
        "instance", "session", "window", proof["epoch"], "policy", "grant"
    )
    assert conversation == "conversation_1"
    bridge._invalidate()
    assert len(revoked) == 1
    assert revoked[0][0] == NativeDocumentAuthority("session", "policy", "grant")


@pytest.mark.parametrize(
    "payload",
    [{}, {"conversationId": "bad/path"}, {"conversationId": None, "extra": True}],
)
def test_terminal_open_rejects_unscoped_or_malformed_payload(payload) -> None:
    opened = []
    bridge = NativeClientBridge(
        instance_id="instance",
        window_id="window",
        origin="http://localhost:8080",
        current_url=lambda: "http://localhost:8080/app-v2/",
        driver=Driver(),
        authenticate_document=lambda _token, _context: NativeDocumentAuthority(
            "session", "policy", "grant"
        ),
        authorize_document=lambda _authority, _context: True,
        open_terminal=lambda authority, conversation: opened.append(
            (authority, conversation)
        )
        or "terminal_reference",
    )
    proof = bridge._bind_loaded_document()
    assert proof
    assert bridge.native_client_dispatch(
        proof, "discover", {"attestation": "server_attestation"}
    )["status"] == "ok"
    assert bridge.native_client_dispatch(proof, "terminal_open", payload)["status"] == "unavailable"
    assert opened == []


def _buddy_bridge(url: str, driver: PyWebViewDriver) -> tuple[NativeClientBridge, dict]:
    state = {"url": url}
    bridge = NativeClientBridge(
        instance_id="instance", window_id="window", origin="http://localhost:8080",
        current_url=lambda: state["url"], driver=driver,
        authenticate_document=lambda _token, _context: NativeDocumentAuthority("session", "policy", "grant"),
        authorize_document=lambda _authority, _context: True,
        shell_path="/app-v2/buddy-overlay",
    )
    return bridge, state


def test_desktop_buddy_bridge_is_bound_to_its_document_and_its_operations() -> None:
    calls: list[tuple] = []
    driver = PyWebViewDriver(
        SimpleNamespace(),
        read_clipboard=lambda: calls.append(("clipboard",)) or "secret",
        buddy_placement=lambda action, x, y: calls.append(("placement", action)) or {
            "placement": "desktop", "visible": True},
        read_buddy_target=lambda: {"conversationId": "conversation-1", "revision": 4},
        show_main_window=lambda conversation: calls.append(("main", conversation)) or True,
        allowed=frozenset({"buddy_placement", "buddy_follow", "main_window"}),
    )
    bridge, state = _buddy_bridge("http://localhost:8080/app-v2/buddy-overlay", driver)
    proof = bridge._bind_loaded_document()
    discovered = bridge.native_client_dispatch(proof, "discover", {"attestation": "server_attestation"})
    assert sorted(discovered["value"]["capabilities"]) == ["buddy_follow", "buddy_placement", "main_window"]
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "unavailable"
    assert bridge.native_client_dispatch(proof, "open_external", {"url": "https://fixture.invalid/"})[
        "status"] == "unavailable"
    assert bridge.native_client_dispatch(proof, "buddy_follow", {}) == {
        "status": "ok", "value": {"conversationId": "conversation-1", "revision": 4}}
    # This window only follows: publishing is refused before the driver acts.
    assert bridge.native_client_dispatch(proof, "buddy_follow", {"conversationId": "conversation-2"})[
        "status"] == "unavailable"
    assert bridge.native_client_dispatch(proof, "main_window", {"conversationId": None})["status"] == "ok"
    for payload in ({"conversationId": "a b"}, {"conversationId": 1}, {}, {"conversationId": None, "x": 1}):
        assert bridge.native_client_dispatch(proof, "main_window", payload)["status"] == "unavailable"
    for payload in ({"action": "ready"}, {"action": "hide"}):
        assert bridge.native_client_dispatch(proof, "buddy_placement", payload)["status"] == "ok"
    for payload in ({"action": "ready", "x": 1}, {"action": "collapse"}, {"action": "tear_off", "x": True, "y": 1}):
        assert bridge.native_client_dispatch(proof, "buddy_placement", payload)["status"] == "unavailable"
    assert calls == [("main", None), ("placement", "ready"), ("placement", "hide")]
    # Any other /app-v2 document in this window has no bridge at all.
    state["url"] = "http://localhost:8080/app-v2/"
    assert bridge.native_client_dispatch(proof, "buddy_follow", {})["status"] == "unavailable"
    assert bridge._bind_loaded_document() is None


def test_buddy_follow_answers_and_roles_are_validated() -> None:
    with pytest.raises(ValueError):
        PyWebViewDriver(SimpleNamespace(), publish_buddy_target=lambda _c: None,
                        read_buddy_target=lambda: None)
    with pytest.raises(ValueError):
        NativeClientBridge(instance_id="i", window_id="w", origin="http://localhost:8080",
                           current_url=lambda: None, driver=Driver(), shell_path="/elsewhere")
    answers = iter([{"conversationId": "bad id", "revision": 1}, {"conversationId": "c", "revision": -1},
                    {"conversationId": "c", "revision": True}, {"conversationId": None, "revision": 0}])
    driver = PyWebViewDriver(SimpleNamespace(), read_buddy_target=lambda: next(answers))
    bridge, _ = _buddy_bridge("http://localhost:8080/app-v2/buddy-overlay", driver)
    proof = bridge._bind_loaded_document()
    bridge.native_client_dispatch(proof, "discover", {"attestation": "server_attestation"})
    results = [bridge.native_client_dispatch(proof, "buddy_follow", {})["status"] for _ in range(4)]
    assert results == ["unavailable", "unavailable", "unavailable", "ok"]


def test_a_fresh_attestation_renews_the_document_lease_before_it_lapses() -> None:
    """Long-lived windows (the main window, the desktop Buddy) kept their
    bridge for only 30 minutes; a fresh attestation renews it (B99)."""
    state = {"clock": 100.0}
    exchanged: list[str] = []

    def authenticate(attestation, _context):
        if not attestation.startswith("attest-") or attestation in exchanged:
            return None  # one-shot and unknown attestations are refused
        exchanged.append(attestation)
        return NativeDocumentAuthority("session", "policy", "grant-" + attestation)

    bridge = NativeClientBridge(
        instance_id="instance", window_id="window", origin="http://localhost:8080",
        current_url=lambda: "http://localhost:8080/app-v2/", driver=Driver(),
        authenticate_document=authenticate, authorize_document=lambda _a, _c: True,
        clock=lambda: state["clock"])
    proof = bridge._bind_loaded_document()
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-1"})["status"] == "ok"
    # Re-sending the exchanged attestation is only a discovery.
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-1"})["status"] == "ok"
    assert exchanged == ["attest-1"]
    state["clock"] += 1700
    # A refused renewal keeps the current lease.
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "bogus"})["status"] == "ok"
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-2"})["status"] == "ok"
    state["clock"] += 1700  # past the first lease, inside the renewed one
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "ok"
    state["clock"] += 200  # the renewed lease lapses too
    assert bridge.native_client_dispatch(proof, "clipboard_read", {}) == {
        "status": "unavailable", "reason": "native_proof_required"}
    # A lapsed document cannot renew itself; only a reload binds a new one.
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-3"})["status"] == "unavailable"
    assert "attest-3" not in exchanged


def test_a_lapsed_server_grant_is_replaced_by_a_fresh_attestation() -> None:
    state = {"clock": 100.0, "granted": set()}

    def authenticate(attestation, _context):
        state["granted"].add("grant-" + attestation)
        return NativeDocumentAuthority("session", "policy", "grant-" + attestation)

    bridge = NativeClientBridge(
        instance_id="instance", window_id="window", origin="http://localhost:8080",
        current_url=lambda: "http://localhost:8080/app-v2/", driver=Driver(),
        authenticate_document=authenticate,
        authorize_document=lambda authority, _c: authority.authority_grant in state["granted"],
        clock=lambda: state["clock"])
    proof = bridge._bind_loaded_document()
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-1"})["status"] == "ok"
    state["granted"].clear()  # the server grant expired (e.g. the machine slept)
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "unavailable"
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-2"})["status"] == "ok"
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "ok"


def test_a_refused_grant_asks_for_a_fresh_attestation_only_before_any_effect() -> None:
    """A policy change (an MCP server connecting after start-up) refused every
    grant, and the window could not tell that from a lost document (B102)."""
    state = {"granted": set(), "url": "http://localhost:8080/app-v2/"}

    def authenticate(attestation, _context):
        state["granted"].add("grant-" + attestation)
        return NativeDocumentAuthority("session", "policy", "grant-" + attestation)

    driver = Driver()
    bridge = NativeClientBridge(
        instance_id="instance", window_id="window", origin="http://localhost:8080",
        current_url=lambda: state["url"], driver=driver,
        authenticate_document=authenticate,
        authorize_document=lambda authority, _c: authority.authority_grant in state["granted"])
    proof = bridge._bind_loaded_document()
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-1"})["status"] == "ok"
    state["granted"].clear()
    for operation, payload in [("clipboard_read", {}), ("clipboard_write", {"text": "fixture"}),
                               ("select_file", _picker_payload()), ("open_external", {"url": "https://example.com/"})]:
        assert bridge.native_client_dispatch(proof, operation, payload) == {
            "status": "unavailable", "reason": "native_authentication_required"}
    assert driver.calls == []
    # Without a fresh attestation nothing is granted.
    assert bridge.native_client_dispatch(proof, "discover", {}) == {
        "status": "unavailable", "reason": "native_authentication_required"}
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-2"})["status"] == "ok"
    assert bridge.native_client_dispatch(proof, "clipboard_read", {})["status"] == "ok"

    # A grant refused after the effect ran is not a retry signal.
    def revoke_during(text):
        state["granted"].clear()
        return True
    driver.clipboard_write = revoke_during
    assert bridge.native_client_dispatch(proof, "clipboard_write", {"text": "fixture"}) == {
        "status": "unavailable", "reason": "native_proof_required"}

    # A document that navigated away cannot re-attest.
    state["url"] = "http://localhost:8080/legacy"
    assert bridge.native_client_dispatch(proof, "clipboard_read", {}) == {
        "status": "unavailable", "reason": "native_proof_required"}
    state["url"] = "http://localhost:8080/app-v2/"
    assert bridge.native_client_dispatch(proof, "discover", {"attestation": "attest-3"}) == {
        "status": "unavailable", "reason": "native_proof_required"}
    assert "grant-attest-3" not in state["granted"]
