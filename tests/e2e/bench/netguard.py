"""The network hook of a benchmark task: block answer sites and canary strings when a benchmark needs it.

A policy names hosts that must never be reached (a benchmark's answer pages, its
dataset mirrors) and canary strings that must never leave the process (a
benchmark's canary GUID, held-out answers). It is enforced at two places every
Row-Bot network call passes:

- name lookups (``socket.getaddrinfo``): a blocked host is never resolved, whatever
  client asked;
- ``httpx`` sends (sync and async, which the model providers and Row-Bot's own
  tools use): a request to a blocked host, or whose URL or body carries a canary,
  is refused before it is sent.

``allow_hosts``, when given, turns the policy into an allow list: every other host
is refused. Refusals are counted, and the trajectory records them.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


class NetworkBlocked(ConnectionError):
    """A benchmark's network policy refused this request."""


def _host_matches(host: str, patterns: Iterable[str]) -> bool:
    name = str(host or "").strip().lower().rstrip(".")
    for pattern in patterns:
        item = str(pattern or "").strip().lower().rstrip(".")
        if item and (name == item or name.endswith("." + item)):
            return True
    return False


_LOOPBACK = ("localhost", "127.0.0.1", "::1")


@dataclass
class NetworkPolicy:
    blocked_hosts: tuple[str, ...] = ()
    canaries: tuple[str, ...] = ()
    allow_hosts: tuple[str, ...] | None = None
    refused: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.blocked_hosts or self.canaries or self.allow_hosts is not None)

    def host_reason(self, host: str) -> str:
        if _host_matches(host, self.blocked_hosts):
            return f"host {host} is blocked for this benchmark"
        if self.allow_hosts is not None and not _host_matches(host, (*self.allow_hosts, *_LOOPBACK)):
            return f"host {host} is not on this benchmark's allow list"
        return ""

    def payload_reason(self, *parts: str | bytes) -> str:
        for canary in self.canaries:
            if not canary:
                continue
            needle = canary.lower()
            for part in parts:
                text = part.decode("utf-8", "replace") if isinstance(part, bytes) else str(part or "")
                if needle in text.lower():
                    return "a canary string would have left the process"
        return ""

    def check_request(self, request: Any) -> None:
        reason = self.host_reason(str(getattr(request.url, "host", "") or ""))
        if not reason:
            try:
                body = request.content
            except Exception:  # a streamed body is not read here; its URL still is
                body = b""
            reason = self.payload_reason(str(request.url), body)
        if reason:
            self.refused.append(reason)
            raise NetworkBlocked(f"benchmark network policy: {reason}")

    def to_dict(self) -> dict[str, Any]:
        # Canaries are counted, never written out.
        return {"blocked_hosts": list(self.blocked_hosts), "canaries": len(self.canaries),
                "allow_hosts": None if self.allow_hosts is None else list(self.allow_hosts),
                "refused": len(self.refused)}


def install(policy: NetworkPolicy) -> Callable[[], None]:
    """Enforce *policy* in this process; returns the undo. A policy with no rules changes nothing."""
    if not policy.active:
        return lambda: None
    import httpx

    original_lookup = socket.getaddrinfo
    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        text = host.decode() if isinstance(host, bytes) else str(host or "")
        reason = policy.host_reason(text)
        if reason:
            policy.refused.append(reason)
            raise NetworkBlocked(f"benchmark network policy: {reason}")
        return original_lookup(host, *args, **kwargs)

    def send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        policy.check_request(request)
        return original_send(self, request, *args, **kwargs)

    async def async_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        policy.check_request(request)
        return await original_async_send(self, request, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo
    httpx.Client.send = send
    httpx.AsyncClient.send = async_send

    def undo() -> None:
        socket.getaddrinfo = original_lookup
        httpx.Client.send = original_send
        httpx.AsyncClient.send = original_async_send

    return undo
