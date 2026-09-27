"""Narrow per-window native adapter; the legacy shared bridge is not authority.

The authenticated v1 handshake advertises a one-shot attestation only to a
direct local owner.  The opt-in ``/app-v2/`` shell composes this adapter after
that independent gate; importing it never opens windows, files, or libraries.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from ipaddress import ip_address
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

_REFERENCE = re.compile(r"[A-Za-z0-9:_-]{1,256}")
_SCOPE_VALUE = re.compile(r"[A-Za-z0-9:_.-]{1,256}")
_OPERATIONS = frozenset({"discover", "select_file", "select_folder", "clipboard_read",
                         "clipboard_write", "open_external", "managed_window", "save",
                         "terminal_open", "buddy_placement", "buddy_follow", "main_window"})
# Main windows tear Buddy off and dock it; the desktop Buddy docks, hides
# itself and reports that its first view is drawn ("ready").
_BUDDY_ACTIONS = frozenset({"status", "dock", "hide", "ready"})
# A document's lease. It runs from the latest attestation the document
# exchanged, so a long-lived window renews it with a fresh one (B99).
LEASE_SECONDS = 1800


def _unavailable(reason: str = "unsupported") -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def safe_external_url(value: object) -> str | None:
    """Permit explicit HTTP(S) browser navigation without credentials or controls."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port
        return value
    except ValueError:
        return None


@dataclass(frozen=True)
class NativeDocumentContext:
    """Host-owned identity for one loaded trusted document."""

    instance_id: str
    window_id: str
    epoch: int


@dataclass(frozen=True)
class NativeDocumentAuthority:
    """Authenticated server authority bound to a native document lease."""

    session_id: str
    policy_revision: str
    authority_grant: str


@dataclass(frozen=True)
class NativeSelectionAuthority:
    """Exact authority supplied to the server-side selection registrar."""

    instance_id: str
    session_id: str
    window_id: str
    window_epoch: int
    policy_revision: str
    authority_grant: str


@dataclass(frozen=True)
class NativePickerRequest:
    """Exact, server-issued picker intent carried by a renderer request."""

    selection_kind: str
    intent_id: str
    intent: str
    conversation_id: str | None
    destination: str


def _valid_authority(value: object) -> bool:
    return (
        isinstance(value, NativeDocumentAuthority)
        and all(_SCOPE_VALUE.fullmatch(item) for item in (
            value.session_id, value.policy_revision, value.authority_grant
        ))
    )


def _picker_request(payload: Mapping[str, Any], selection_kind: str) -> NativePickerRequest | None:
    if set(payload) != {"intentId", "intent", "conversationId", "destination"}:
        return None
    conversation = payload.get("conversationId")
    if conversation is not None and (
        not isinstance(conversation, str) or not _SCOPE_VALUE.fullmatch(conversation)
    ):
        return None
    values = (payload.get("intentId"), payload.get("intent"), payload.get("destination"))
    if not all(isinstance(value, str) and _SCOPE_VALUE.fullmatch(value) for value in values):
        return None
    return NativePickerRequest(
        selection_kind=selection_kind, intent_id=payload["intentId"], intent=payload["intent"],
        conversation_id=conversation, destination=payload["destination"],
    )


class NativeDriver(Protocol):
    """Host operations supplied by the trusted shell, never by browser payloads."""

    def select(self, kind: str) -> str | None: ...
    def clipboard_read(self) -> str | None: ...
    def clipboard_write(self, text: str) -> bool: ...
    def open_external(self, url: str) -> bool: ...
    def managed_window(self, route: str) -> bool: ...
    def buddy_placement(self, action: str, x: float | None, y: float | None) -> dict[str, Any] | None: ...
    def buddy_follow(self, conversation_id: str | None) -> dict[str, Any] | None: ...
    def main_window(self, conversation_id: str | None) -> bool: ...
    def save(self, reference: str, suggested_name: str, authorized: Callable[[], bool]) -> bool | None: ...
    def capabilities(self) -> list[str]: ...


def select_existing_workspace_folder() -> Path | None:
    """Explicit host picker callback; no native library is loaded before invocation."""
    from row_bot.application.client_platform import ClientPlatformError
    try:
        import webview
        windows = list(webview.windows)
        if not windows:
            raise ClientPlatformError("capability_unavailable")
        selected = PyWebViewDriver(windows[0]).select("folder")
        return Path(selected) if selected else None
    except ClientPlatformError:
        raise
    except Exception:
        raise ClientPlatformError("capability_unavailable") from None


class PyWebViewDriver:
    """Use one supplied window; file paths stay inside trusted Python callbacks."""

    def __init__(self, window: Any, *, open_window: Callable[[str], bool] | None = None,
                 read_clipboard: Callable[[], str | None] | None = None,
                 write_clipboard: Callable[[str], bool] | None = None,
                 save_reference: Callable[[str, Path], bool] | None = None,
                 open_external: Callable[[str], bool] | None = None,
                 buddy_placement: Callable[[str, float | None, float | None], dict[str, Any] | None] | None = None,
                 publish_buddy_target: Callable[[str], dict[str, Any] | None] | None = None,
                 read_buddy_target: Callable[[], dict[str, Any] | None] | None = None,
                 show_main_window: Callable[[str | None], bool] | None = None,
                 allowed: frozenset[str] | None = None) -> None:
        if publish_buddy_target is not None and read_buddy_target is not None:
            # One window either publishes what it shows or follows; never both.
            raise ValueError("buddy_follow_role_conflict")
        self._window = window
        self._open_window = open_window
        self._read_clipboard = read_clipboard
        self._write_clipboard = write_clipboard
        self._save_reference = save_reference
        self._open_external = open_external
        self._buddy_placement = buddy_placement
        self._publish_buddy_target = publish_buddy_target
        self._read_buddy_target = read_buddy_target
        self._show_main_window = show_main_window
        self._allowed = allowed

    def capabilities(self) -> list[str]:
        result = ["select_file", "select_folder", "open_external"]
        for name, callback in (("managed_window", self._open_window), ("clipboard_read", self._read_clipboard),
                               ("clipboard_write", self._write_clipboard), ("save", self._save_reference),
                               ("buddy_placement", self._buddy_placement),
                               ("buddy_follow", self._publish_buddy_target or self._read_buddy_target),
                               ("main_window", self._show_main_window)):
            if callback is not None:
                result.append(name)
        # A window role may narrow what its document can reach (the desktop
        # Buddy gets no pickers, clipboard, saves or external navigation).
        return [name for name in result if self._allowed is None or name in self._allowed]

    def select(self, kind: str) -> str | None:
        import webview
        selected = self._window.create_file_dialog(webview.FOLDER_DIALOG if kind == "folder" else webview.OPEN_DIALOG,
                                                   allow_multiple=False)
        return str(selected[0]) if selected else None

    def clipboard_read(self) -> str | None:
        return self._read_clipboard() if self._read_clipboard is not None else None

    def clipboard_write(self, text: str) -> bool:
        return bool(self._write_clipboard and self._write_clipboard(text))

    def open_external(self, url: str) -> bool:
        if self._open_external is not None:
            return self._open_external(url)
        import webbrowser
        return webbrowser.open(url)

    def managed_window(self, route: str) -> bool:
        return bool(self._open_window and self._open_window(route))

    def buddy_placement(self, action: str, x: float | None, y: float | None) -> dict[str, Any] | None:
        return self._buddy_placement(action, x, y) if self._buddy_placement else None

    def buddy_follow(self, conversation_id: str | None) -> dict[str, Any] | None:
        if conversation_id is None:
            return self._read_buddy_target() if self._read_buddy_target else None
        return self._publish_buddy_target(conversation_id) if self._publish_buddy_target else None

    def main_window(self, conversation_id: str | None) -> bool:
        return bool(self._show_main_window and self._show_main_window(conversation_id))

    def save(self, reference: str, suggested_name: str, authorized: Callable[[], bool]) -> bool | None:
        if self._save_reference is None:
            return False
        import webview
        selected = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename=suggested_name)
        if not selected:
            return None
        if not authorized():
            return False
        return self._save_reference(reference, Path(selected[0]))


class NativeClientBridge:
    """One document lease with revocation and current-window checks at dispatch.

    Only ``native_client_dispatch`` is exposed to JavaScript. Underscored lease
    methods must be called by the shell's before_load/loaded/closed handlers.
    ``discover`` exchanges an opaque server attestation before any capability
    is exposed.  Every later operation revalidates that authenticated authority.
    Selection registration receives the exact picker request and document
    authority and must return only an opaque, one-shot backend reference.
    """

    def __init__(self, *, instance_id: str, window_id: str, origin: str,
                 current_url: Callable[[], str | None], driver: NativeDriver,
                 authenticate_document: Callable[
                     [str, NativeDocumentContext], NativeDocumentAuthority | None
                 ] | None = None,
                 authorize_document: Callable[
                     [NativeDocumentAuthority, NativeDocumentContext], bool
                 ] | None = None,
                 register_selection: Callable[
                     [NativePickerRequest, NativeSelectionAuthority, Path], str
                 ] | None = None,
                 cancel_selection: Callable[
                     [NativePickerRequest, NativeSelectionAuthority], None
                 ] | None = None,
                 revoke_document: Callable[
                     [NativeDocumentAuthority, NativeDocumentContext], None
                 ] | None = None,
                 open_terminal: Callable[
                     [NativeSelectionAuthority, str | None], str
                 ] | None = None,
                 shell_path: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        parsed = urlsplit(origin)
        if not safe_external_url(origin) or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("invalid_native_origin")
        try:
            loopback = parsed.hostname == "localhost" or ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("native_origin_must_be_loopback")
        self._origin = origin.rstrip("/")
        self._instance = instance_id
        self._window = window_id
        self._current_url = current_url
        self._driver = driver
        self._authenticate = authenticate_document
        self._authorize = authorize_document
        self._register = register_selection
        self._cancel_selection = cancel_selection
        self._revoke_document = revoke_document
        self._open_terminal = open_terminal
        # A window bound to one document (the desktop Buddy) keeps its
        # bridge only while it shows exactly that document.
        if shell_path is not None and not re.fullmatch(r"/app-v2/(?:[A-Za-z0-9_-]+/?)*", shell_path):
            raise ValueError("invalid_native_shell_path")
        self._shell_path = shell_path
        self._clock = clock
        self._lock = threading.RLock()
        self._token = ""
        self._epoch = 0
        self._expires = 0.0
        self._attestation = ""
        self._authority: NativeDocumentAuthority | None = None

    def _at_shell(self) -> bool:
        try:
            url = self._current_url()
            if not isinstance(url, str) or not safe_external_url(url):
                return False
            parsed = urlsplit(url)
            if f"{parsed.scheme}://{parsed.netloc}" != self._origin:
                return False
            if self._shell_path is not None:
                return parsed.path.rstrip("/") == self._shell_path.rstrip("/")
            return bool(re.fullmatch(r"/app-v2/(?:[A-Za-z0-9_-]+/?)*", parsed.path))
        except Exception:
            return False

    def _invalidate(self, *_args: Any) -> None:
        with self._lock:
            authority = self._authority
            context = self._context()
            self._token = ""
            self._authority = None
            self._attestation = ""
            self._epoch += 1
        if authority is not None and self._revoke_document is not None:
            try:
                self._revoke_document(authority, context)
            except Exception:
                pass

    def _bind_loaded_document(self) -> dict[str, Any] | None:
        self._invalidate()
        with self._lock:
            if not self._at_shell():
                return None
            self._token = secrets.token_urlsafe(32)
            self._expires = self._clock() + LEASE_SECONDS
            return {"instanceId": self._instance, "windowId": self._window,
                    "epoch": self._epoch, "token": self._token}

    def _context(self) -> NativeDocumentContext:
        return NativeDocumentContext(self._instance, self._window, self._epoch)

    def _valid_document(self, proof: Mapping[str, Any]) -> bool:
        if not self._at_shell() or self._clock() >= self._expires:
            self._invalidate()
            return False
        return bool(self._token
                    and set(proof) == {"instanceId", "windowId", "epoch", "token"}
                    and proof.get("instanceId") == self._instance and proof.get("windowId") == self._window
                    and type(proof.get("epoch")) is int and proof["epoch"] == self._epoch
                    and isinstance(proof.get("token"), str)
                    and hmac.compare_digest(proof["token"], self._token))

    def _valid(self, proof: Mapping[str, Any]) -> bool:
        if not self._valid_document(proof) or self._authority is None or self._authorize is None:
            return False
        authority = self._authority
        context = self._context()
        try:
            allowed = self._authorize(authority, context)
        except Exception:
            allowed = False
        if not allowed:
            self._authority = None
            return False
        return bool(
            self._valid_document(proof)
            and self._authority == authority
            and self._context() == context
        )

    def _selection_authority(self) -> NativeSelectionAuthority:
        assert self._authority is not None
        return NativeSelectionAuthority(
            instance_id=self._instance,
            session_id=self._authority.session_id,
            window_id=self._window,
            window_epoch=self._epoch,
            policy_revision=self._authority.policy_revision,
            authority_grant=self._authority.authority_grant,
        )

    def _authenticate_discovery(
        self, proof: Mapping[str, Any], payload: Mapping[str, Any], epoch: int,
    ) -> bool:
        if self._authenticate is None or self._authorize is None:
            return False
        if (set(payload) != {"attestation"}
                or not isinstance(payload.get("attestation"), str)
                or not _REFERENCE.fullmatch(payload["attestation"])):
            return False
        context = NativeDocumentContext(self._instance, self._window, epoch)
        # The server callback can block or revoke while exchanging the opaque
        # attestation, so do not rely on the first document check.
        try:
            authority = self._authenticate(payload["attestation"], context)
        except Exception:
            return False
        with self._lock:
            if (not _valid_authority(authority) or not self._valid_document(proof)
                    or self._epoch != epoch or self._context() != context):
                return False
            previous = self._authority
            self._authority = authority
            if not self._valid(proof):
                # A failed renewal leaves the current authority in place.
                self._authority = previous
                return False
            self._attestation = payload["attestation"]
            self._expires = self._clock() + LEASE_SECONDS
            return True

    def native_client_dispatch(self, proof: object, operation: object, payload: object) -> dict[str, Any]:
        """Validate the lease and closed request before any native effect."""
        if not isinstance(proof, dict) or not isinstance(payload, dict) or not isinstance(operation, str):
            return _unavailable("invalid_request")
        try:
            if len(json.dumps(payload)) > 64 * 1024 or operation not in _OPERATIONS:
                return _unavailable("invalid_request")
            with self._lock:
                if not self._valid_document(proof):
                    return _unavailable("native_proof_required")
                epoch = self._epoch
                authenticated = self._valid(proof)
            # A fresh attestation renews an authenticated document's lease;
            # the one it already exchanged is simply a discovery (B99).
            renewing = (operation == "discover" and authenticated
                        and payload.get("attestation") not in (None, self._attestation))
            if operation == "discover" and (not authenticated or renewing):
                if not self._authenticate_discovery(proof, payload, epoch) and not authenticated:
                    return _unavailable("native_authentication_required")
            with self._lock:
                if not self._valid(proof) or epoch != self._epoch:
                    # Nothing has run yet. A document that is still bound
                    # but whose grant was refused (the server's policy
                    # revision moved on) may exchange a fresh attestation
                    # through discover and try again (B102); a lost document
                    # can only load again.
                    return _unavailable(
                        "native_authentication_required"
                        if epoch == self._epoch and self._valid_document(proof)
                        else "native_proof_required")
            available = self._driver.capabilities()
            if self._open_terminal is not None:
                available.append("terminal_open")
            with self._lock:
                if not self._valid(proof) or epoch != self._epoch:
                    return _unavailable("native_proof_required")
                if self._register is None:
                    available = [name for name in available if name not in {"select_file", "select_folder"}]
                if operation == "discover" and (not payload or set(payload) == {"attestation"}):
                    platform = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform, "unknown")
                    return {"status": "ok", "value": {"kind": "pywebview", "platform": platform,
                            "capabilities": available, "instanceId": self._instance, "windowId": self._window,
                            "epoch": epoch}}
                if operation not in available:
                    return _unavailable()
            # A picker can stay open through navigation. Do not hold the lock:
            # navigation revokes immediately and the completion is checked again.
            kind = "folder" if operation == "select_folder" else "file"
            request = _picker_request(payload, kind) if operation in {"select_file", "select_folder"} else None
            if operation in {"select_file", "select_folder"} and request is not None:
                path = self._driver.select(kind)
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                    authority = self._selection_authority()
                    if not path:
                        cancel = self._cancel_selection
                    else:
                        cancel = None
                if not path:
                    if cancel is not None:
                        cancel(request, authority)
                    with self._lock:
                        if not self._valid(proof) or epoch != self._epoch:
                            return _unavailable("native_proof_required")
                    return {"status": "cancelled"}
                assert self._register is not None
                reference = self._register(request, authority, Path(path))
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                    if not _REFERENCE.fullmatch(reference):
                        return _unavailable("invalid_reference")
                    return {"status": "ok", "value": {"reference": reference, "kind": kind}}
            if (
                operation == "terminal_open"
                and set(payload) == {"conversationId"}
                and (
                    payload["conversationId"] is None
                    or isinstance(payload["conversationId"], str)
                    and _SCOPE_VALUE.fullmatch(payload["conversationId"])
                )
                and self._open_terminal is not None
            ):
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                    authority = self._selection_authority()
                reference = self._open_terminal(authority, payload["conversationId"])
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                    if not _REFERENCE.fullmatch(reference):
                        return _unavailable("invalid_reference")
                return {"status": "ok", "value": {"terminalId": reference}}
            if (operation == "save" and set(payload) == {"reference", "name"}
                    and isinstance(payload["reference"], str) and _REFERENCE.fullmatch(payload["reference"])
                    and isinstance(payload["name"], str)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}", payload["name"])):
                def authorized() -> bool:
                    with self._lock:
                        return self._valid(proof)
                result = self._driver.save(payload["reference"], payload["name"], authorized)
                if not authorized():
                    return _unavailable("native_proof_required")
                return {"status": "cancelled"} if result is None else ({"status": "ok", "value": None} if result else _unavailable())
            if operation == "buddy_follow":
                publish = set(payload) == {"conversationId"}
                if not (publish or not payload) or (
                    publish and not (isinstance(payload["conversationId"], str)
                                     and _SCOPE_VALUE.fullmatch(payload["conversationId"]))):
                    return _unavailable("invalid_request")
                value = self._driver.buddy_follow(payload["conversationId"] if publish else None)
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                target = value.get("conversationId") if isinstance(value, dict) else None
                return ({"status": "ok", "value": value}
                        if isinstance(value, dict) and set(value) == {"conversationId", "revision"}
                        and (target is None or isinstance(target, str) and _SCOPE_VALUE.fullmatch(target))
                        and type(value["revision"]) is int and value["revision"] >= 0 else _unavailable())
            if operation == "main_window":
                target = payload.get("conversationId")
                if set(payload) != {"conversationId"} or not (
                        target is None or isinstance(target, str) and _SCOPE_VALUE.fullmatch(target)):
                    return _unavailable("invalid_request")
                result = self._driver.main_window(target)
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                return {"status": "ok", "value": None} if result else _unavailable()
            if operation == "buddy_placement":
                action = payload.get("action")
                point = action == "tear_off" and set(payload) == {"action", "x", "y"}
                if not ((action in _BUDDY_ACTIONS and set(payload) == {"action"})
                        or (point and all(isinstance(payload[key], (int, float))
                                          and not isinstance(payload[key], bool)
                                          and abs(payload[key]) <= 1000000 for key in ("x", "y")))):
                    return _unavailable("invalid_request")
                value = self._driver.buddy_placement(
                    action, payload.get("x") if point else None, payload.get("y") if point else None)
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                return ({"status": "ok", "value": value}
                        if isinstance(value, dict) and set(value) == {"placement", "visible"}
                        and value["placement"] in {"docked", "desktop"}
                        and isinstance(value["visible"], bool) else _unavailable())
            with self._lock:
                if not self._valid(proof) or epoch != self._epoch:
                    return _unavailable("native_proof_required")
            if operation == "clipboard_read" and not payload:
                value = self._driver.clipboard_read()
                with self._lock:
                    if not self._valid(proof) or epoch != self._epoch:
                        return _unavailable("native_proof_required")
                if value is None or len(value.encode("utf-8")) > 64 * 1024:
                    return _unavailable()
                return {"status": "ok", "value": value}
            if operation == "clipboard_write" and set(payload) == {"text"} and isinstance(payload["text"], str):
                result = self._driver.clipboard_write(payload["text"])
            elif operation == "open_external" and set(payload) == {"url"} and safe_external_url(payload["url"]):
                result = self._driver.open_external(payload["url"])
            elif (operation == "managed_window" and set(payload) == {"route"}
                  and isinstance(payload["route"], str)
                  and re.fullmatch(r"/app-v2/(?:[A-Za-z0-9_-]+/?)*", payload["route"])):
                result = self._driver.managed_window(payload["route"])
            else:
                return _unavailable("invalid_request")
            with self._lock:
                if not self._valid(proof) or epoch != self._epoch:
                    return _unavailable("native_proof_required")
                return {"status": "cancelled"} if result is None else ({"status": "ok", "value": None} if result else _unavailable())
        except Exception:
            # Neither native exception details nor picker paths cross to JS.
            return _unavailable("operation_failed")


def attach_native_client(
    window: Any, *, instance_id: str, origin: str, driver: NativeDriver,
    authenticate_document: Callable[
        [str, NativeDocumentContext], NativeDocumentAuthority | None
    ] | None = None,
    authorize_document: Callable[
        [NativeDocumentAuthority, NativeDocumentContext], bool
    ] | None = None,
    register_selection: Callable[
        [NativePickerRequest, NativeSelectionAuthority, Path], str
    ] | None = None,
    cancel_selection: Callable[
        [NativePickerRequest, NativeSelectionAuthority], None
    ] | None = None,
    revoke_document: Callable[
        [NativeDocumentAuthority, NativeDocumentContext], None
    ] | None = None,
    open_terminal: Callable[
        [NativeSelectionAuthority, str | None], str
    ] | None = None,
    shell_path: str | None = None,
) -> NativeClientBridge:
    """Attach only to a newly created trusted /app-v2 window, never legacy API.

    The caller must gate composition on independently negotiated native support.
    A managed-window callback must create another independently bound window.
    """
    bridge = NativeClientBridge(instance_id=instance_id, window_id=str(window.uid), origin=origin,
                                current_url=window.get_current_url, driver=driver,
                                authenticate_document=authenticate_document,
                                authorize_document=authorize_document,
                                register_selection=register_selection,
                                cancel_selection=cancel_selection,
                                revoke_document=revoke_document,
                                open_terminal=open_terminal,
                                shell_path=shell_path)

    def loaded(*_args: Any) -> None:
        proof = bridge._bind_loaded_document()
        if proof is not None:
            # Token is a closure value, never a storage item, URL or public flag.
            script = "(() => { if (window !== window.top) return; const proof = " + json.dumps(proof) + "; "
            script += "Object.defineProperty(window, '__ROW_BOT_NATIVE_CLIENT__', { configurable: true, "
            script += "value: { dispatch: (operation, payload) => window.pywebview.api.native_client_dispatch(proof, operation, payload) } }); "
            script += "window.dispatchEvent(new Event('row-bot-native-ready')); })();"
            window.evaluate_js(script)

    window.events.before_load += bridge._invalidate
    window.events.closed += bridge._invalidate
    window.events.loaded += loaded
    window.expose(bridge.native_client_dispatch)
    return bridge
