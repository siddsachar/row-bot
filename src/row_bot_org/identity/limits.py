"""A small in-memory limit on the unauthenticated sign-in steps, per client address.

``/auth/login`` writes a sign-in attempt before anyone is known, so a flood from one address could fill
``auth_attempts``. ``AddressRateLimit`` allows ``limit`` requests a fixed window (a minute) per client
address, counting an IPv6 address by its /64 (one host usually holds the whole /64). It tracks at most
``MAX_ADDRESSES`` in a window; past that, the new ones share a single count, so memory stays bounded.
Behind a reverse proxy the client address is the proxy's unless ``ROW_BOT_ORG_TRUSTED_PROXIES`` names it.
``spent`` asks without counting, for a limit that counts only refused requests (the device token endpoint).
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections.abc import Callable

WINDOW_SECONDS = 60.0
MAX_ADDRESSES = 10_000
_OVERFLOW = "*"


def _key(address: str | None) -> str:
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return address or ""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class AddressRateLimit:
    """At most ``limit`` requests per window from one client address. Thread-safe."""

    def __init__(self, limit: int, *, window: float = WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._started = clock()
        self._counts: dict[str, int] = {}

    def allow(self, address: str | None) -> bool:
        """Count one request from ``address``; whether it is within the limit."""
        return self._count(address, 1) <= self.limit

    def spent(self, address: str | None) -> bool:
        """Whether ``address`` has used up this window's requests, without counting one (to count only failures)."""
        return self._count(address, 0) >= self.limit

    def _count(self, address: str | None, add: int) -> int:
        key = _key(address)
        with self._lock:
            now = self._clock()
            if now - self._started >= self.window:
                self._counts.clear()
                self._started = now
            if key not in self._counts and len(self._counts) >= MAX_ADDRESSES:
                key = _OVERFLOW
            count = self._counts.get(key, 0) + add
            if add:
                self._counts[key] = count
        return count


__all__ = ["MAX_ADDRESSES", "WINDOW_SECONDS", "AddressRateLimit"]
