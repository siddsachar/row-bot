"""One safe path for catalog traffic, display links, short-lived previews and files."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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


def _public_address(host: str, refused: str) -> str:
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
    return str(addresses[0])


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
    try:
        parts = urlsplit(proxy)
        parts.port  # A malformed port raises here.
    except ValueError:
        return None
    return proxy if parts.scheme in {"http", "https"} and parts.hostname else None


class _Pinned(httpx.HTTPTransport):
    """Connect to the checked address while TLS and Host keep the reviewed name."""

    def __init__(self, host: str, address: str) -> None:
        super().__init__()
        self.host, self.address = host, address

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self.host:
            raise httpx.ConnectError("unreviewed host")
        pinned = httpx.Request(request.method, request.url.copy_with(host=self.address), headers=request.headers,
            stream=request.stream, extensions={**request.extensions, "sni_hostname": self.host})
        return super().handle_request(pinned)


def fetch(url: str, *, hosts: Iterable[str] | None, max_bytes: int, timeout: float = 20,
          headers: Mapping[str, str] | None = None, redirects: int = 0,
          exact_redirects: Mapping[str, str] | None = None, meta: dict | None = None,
          refused: str = "package_source_not_supported", too_large: str = "package_download_too_large") -> bytes:
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
    receives the final response's status and headers.
    """
    allowed = None if hosts is None else frozenset(hosts)
    pairs, budget, sent, previous, reviewed = dict(exact_redirects or {}), redirects, dict(headers or {}), "", False
    deadline = time.monotonic() + timeout * 6
    while True:
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
            options["transport"] = _Pinned(parts.hostname, _public_address(parts.hostname, refused))
        # A fresh client per hop: no cookie or connection state crosses origins.
        with httpx.Client(**options) as client:
            with client.stream("GET", url, headers={"User-Agent": "Row-Bot", **sent, "Accept-Encoding": "identity"}) as response:
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
                data = bytearray()
                for chunk in response.iter_bytes():  # Identity only, so nothing is decompressed.
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError(too_large)
                    if time.monotonic() > deadline:
                        raise TimeoutError("source_timeout")
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

    def pop(self, key: Any) -> Any:
        with self._lock:
            entry = self._items.pop(key, None)
            return entry[1] if entry else None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

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
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
