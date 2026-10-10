"""One safe path for catalog traffic, display links, short-lived previews and files."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
import os
from pathlib import Path
import socket
import stat
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

_REDIRECTS = {301, 302, 303, 307, 308}
_CREDENTIALS = {"authorization", "cookie", "proxy-authorization"}
# IPv6 forms that carry an IPv4 address: NAT64 and the deprecated IPv4-compatible block.
_EMBEDDED = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("::/96"))
# The check of the work a fetch belongs to (a plan step): its stop or a sign-out ends the download.
_CHECK: ContextVar[Callable[[], None] | None] = ContextVar("row_bot_fetch_check", default=None)


@contextmanager
def checked(check: Callable[[], None]) -> Iterator[None]:
    """Every fetch inside (however deep in a catalog adapter) calls ``check`` per hop and while it
    reads, so stopping a plan or signing out stops a download that is already running."""
    token = _CHECK.set(check)
    try:
        yield
    finally:
        _CHECK.reset(token)


def public_url(value: object, *, limit: int = 2048) -> str:
    """A credential-free https link that is safe to display, otherwise ''."""
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
        valid = (parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                 and len(text) <= limit and not any(ord(c) < 33 for c in text))
    except ValueError:
        valid = False
    return text if valid else ""


def _global(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global:
        return False
    if address.version == 6:
        embedded = address.ipv4_mapped
        if embedded is None and any(address in network for network in _EMBEDDED):
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded is not None and not embedded.is_global:
            return False
    return True


def _public_addresses(host: str, refused: str) -> list[str]:
    """Resolve once; a name reaching loopback, private or link-local space is refused."""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError(refused)
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ConnectionError("source_unreachable") from exc
    addresses = [ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in found]
    if not addresses or not all(_global(address) for address in addresses):
        raise ValueError(refused)
    return list(dict.fromkeys(str(address) for address in addresses))


def proxy_for(host: str) -> str | None:
    """The system or environment proxy for a reviewed host, or None to connect directly.

    Reads the environment, the Windows registry or macOS settings through the
    standard library; only http(s) proxies are used, and PAC scripts are not run."""
    import urllib.request
    try:
        proxies = urllib.request.getproxies()
        if not proxies or urllib.request.proxy_bypass(host):
            return None
    except (OSError, ValueError, TypeError):
        return None
    proxy = proxies.get("https") or proxies.get("all") or ""
    proxy = proxy if "://" in proxy or not proxy else "http://" + proxy  # HTTPS_PROXY=proxy.corp:8080
    try:
        parts = urlsplit(proxy)
        parts.port  # A malformed port raises here.
    except ValueError:
        return None
    return proxy if parts.scheme in {"http", "https"} and parts.hostname else None


# Addresses that couldn't be connected to lately go last, so a dead one costs one wait, not one per fetch.
_UNREACHABLE: dict[str, float] = {}
_UNREACHABLE_FOR = 600.0
_UNREACHABLE_LOCK = threading.Lock()
_PROBE = 5.0  # Seconds to connect while another address could answer instead.


class _Pinned(httpx.HTTPTransport):
    """Connect to a checked address while TLS and Host keep the reviewed name. One that can't be connected
    to (found live: one of a host's four addresses never answered) gives way to the next, until the fetch's
    deadline; nothing was sent to it, so nothing is repeated."""

    def __init__(self, host: str, addresses: list[str], deadline: float) -> None:
        super().__init__()
        self.host, self.addresses, self.deadline = host, addresses, deadline

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self.host:
            raise httpx.ConnectError("unreviewed host")
        with _UNREACHABLE_LOCK:
            now = time.monotonic()
            for address, failed in list(_UNREACHABLE.items()):
                if now - failed >= _UNREACHABLE_FOR:
                    del _UNREACHABLE[address]
            addresses = sorted(self.addresses, key=lambda address: address in _UNREACHABLE)
        for number, address in enumerate(addresses):
            extensions = {**request.extensions, "sni_hostname": self.host}
            if number < len(addresses) - 1 and isinstance(extensions.get("timeout"), dict):
                # Another address waits: this one gets a short while to connect; the last gets it all.
                extensions["timeout"] = {**extensions["timeout"],
                                         "connect": min(extensions["timeout"].get("connect") or _PROBE, _PROBE)}
            pinned = httpx.Request(request.method, request.url.copy_with(host=address), headers=request.headers,
                stream=request.stream, extensions=extensions)
            try:
                return super().handle_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                with _UNREACHABLE_LOCK:
                    _UNREACHABLE[address] = time.monotonic()
                if number == len(addresses) - 1 or time.monotonic() > self.deadline:
                    raise
        raise httpx.ConnectError("no address")


def fetch(url: str, *, hosts: Iterable[str] | None, max_bytes: int, timeout: float = 20,
          headers: Mapping[str, str] | None = None, redirects: int = 0,
          exact_redirects: Mapping[str, str] | None = None, meta: dict | None = None,
          refused: str = "package_source_not_supported", too_large: str = "package_download_too_large",
          method: str = "GET", check: Callable[[], None] | None = None) -> bytes:
    """Bounded https GET without automatic redirects.

    ``hosts`` is a reviewed allow-list; ``None`` admits any public name. A direct
    connection is made only to a checked global address. A reviewed host (and only
    a reviewed host) goes through the system or environment proxy when one is set:
    the proxy then resolves the name, so the address check cannot apply there and
    the allow-list is the guard. Each redirect hop is checked again, its proxy is
    chosen again, and credentials never cross to another host. An exact
    ``source -> destination`` pair allows one reviewed migration off the list.
    Bodies are never decompressed, so ``max_bytes`` bounds what is held, and the
    whole fetch has one deadline as well as the per-read ``timeout``. ``meta``
    receives the final response's status and headers. ``check`` (or the one set by
    ``checked``) runs before each hop and about four times a second while reading,
    and stops the fetch by raising. ``HEAD`` returns no body.
    """
    check = check or _CHECK.get() or (lambda: None)
    if method not in {"GET", "HEAD"}:
        raise ValueError(refused)
    allowed = None if hosts is None else frozenset(hosts)
    pairs, budget, sent, previous, reviewed = dict(exact_redirects or {}), redirects, dict(headers or {}), "", False
    deadline = time.monotonic() + timeout * 6
    while True:
        check()
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.fragment or parts.port not in {None, 443} or (allowed is not None and not reviewed and parts.hostname not in allowed)):
            raise ValueError(refused)
        if previous and urlsplit(previous).hostname != parts.hostname:
            sent = {key: value for key, value in sent.items() if key.lower() not in _CREDENTIALS}
        options: dict[str, Any] = {"timeout": timeout, "follow_redirects": False, "trust_env": False}
        proxy = proxy_for(parts.hostname) if allowed is not None else None
        if proxy:
            options["proxy"] = proxy
        else:
            options["transport"] = _Pinned(parts.hostname, _public_addresses(parts.hostname, refused), deadline)
        # A fresh client per hop: no cookie or connection state crosses origins.
        with httpx.Client(**options) as client:
            with client.stream(method, url, headers={"User-Agent": "Row-Bot", **sent, "Accept-Encoding": "identity"}) as response:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location", "")
                    reviewed = pairs.pop(url, None) == location
                    if not reviewed and (budget < 1 or not location):
                        raise ValueError(refused)
                    budget -= 0 if reviewed else 1
                    previous, url = url, location if reviewed else urljoin(url, location)
                    continue
                response.raise_for_status()
                if meta is not None:
                    meta.update(status=response.status_code, headers={k.lower(): v for k, v in response.headers.items()})
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise ValueError(refused)
                data, checked_at = bytearray(), time.monotonic()
                for chunk in response.iter_bytes() if method == "GET" else ():  # Identity only: nothing is decompressed.
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError(too_large)
                    if time.monotonic() > deadline:
                        raise TimeoutError("source_timeout")
                    if time.monotonic() - checked_at >= 0.25:
                        check()
                        checked_at = time.monotonic()
                check()
                return bytes(data)


class TtlCache:
    """Short-lived, owner-keyed values. Full caches evict the oldest, or refuse."""

    def __init__(self, ttl: float, capacity: int, *, full: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.capacity, self.full, self.clock = ttl, capacity, full, clock
        self._items: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: Any) -> Any:
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and self.clock() - entry[0] <= self.ttl:
                return entry[1]
            self._items.pop(key, None)
            return None

    def room(self) -> None:
        """Expire old values; a refusing cache raises before any costly work."""
        with self._lock:
            now = self.clock()
            for old in [k for k, (stamp, _) in self._items.items() if now - stamp > self.ttl]:
                del self._items[old]
            if self.full and len(self._items) >= self.capacity:
                raise ValueError(self.full)

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            self._items.pop(key, None)
            self.room()
            while len(self._items) >= self.capacity:
                del self._items[next(iter(self._items))]
            self._items[key] = (self.clock(), value)

    def __len__(self) -> int:
        return len(self._items)


def write_atomic(path: Path, data: bytes | str, *, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Publish a whole file readable only by this account, never through a link;
    only its own temporary is removed."""
    for candidate in (path.parent, path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("unsafe_publication_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data.encode("utf-8") if isinstance(data, str) else data)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise ValueError("integration_search_cancelled")
        # Antivirus can hold a just-written file for a moment (WinError 5 or 32): try again briefly rather
        # than lose the write (a plugin's saved state, a catalog).
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
