"""The networked Developer sandbox's way out: an HTTP proxy that opens public internet addresses only.

It runs in its own small container, the sandbox's network gate (``sandbox_egress``), which is the
only other member of the sandbox's internal Docker network. A program in the sandbox reaches the
internet through here (``HTTP_PROXY`` / ``HTTPS_PROXY``) or not at all.

Each destination is resolved here and refused unless every address it has is a public one: this
computer (loopback, ``host.docker.internal``, Docker's gateways), the local network and other
private or shared ranges, link-local addresses (cloud metadata) and anything reserved never open.
The connection is made to the address that was checked, so a name can't be re-pointed in between.
HTTPS goes through CONNECT unopened: only its host name and port are seen.

Standard library only, Python 3.8 or later, and short: the whole file is passed to ``python3 -c``
on the docker command line, which Windows limits to about 32,000 characters.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlsplit

LOCAL_NAMES = ("localhost", "host.docker.internal", "gateway.docker.internal", "metadata.google.internal")
LOCAL_SUFFIXES = (".localhost", ".internal", ".local")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
HEAD_LIMIT = 64 * 1024
logger = logging.getLogger("row_bot.sandbox_gate")
DROPPED_HEADERS = {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "host"}


def public_address(text: str) -> bool:
    """True only for an address on the public internet; IPv6 forms of IPv4 are judged as IPv4."""
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if address.version == 6:
        embedded = address.ipv4_mapped or address.sixtofour or (address.teredo[1] if address.teredo else None)
        if embedded is None and address in NAT64:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded is not None:
            return public_address(str(embedded))
    return address.is_global and not (
        address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
        or address.is_reserved or address.is_unspecified
    )


def refusal(host: str, port: int, addresses: list, allow=public_address) -> str:
    """Why a connection to ``host:port`` (resolved to ``addresses``) is refused, or ``""``."""
    name = host.rstrip(".").lower()
    if not name:
        return "it names no host"
    if name in LOCAL_NAMES or name.endswith(LOCAL_SUFFIXES):
        return "it names this computer or the local network"
    if not 0 < port < 65536:
        return "its port isn't valid"
    if not addresses:
        return "its name doesn't resolve"
    if not all(allow(address) for address in addresses):
        return "it is on this computer, the local network or a cloud metadata address"
    return ""


async def _resolve(host: str, port: int) -> list:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _reply(writer, status: str, text: str) -> None:
    body = (text + "\n").encode("utf-8")
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode("ascii") + body
    )


async def _pipe(reader, writer, close_all) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, RuntimeError):
        close_all()


def _target(method: str, target: str):
    """(host, port, request path) from a CONNECT authority or an absolute http URL; None when malformed."""
    if method == "CONNECT":
        parts = urlsplit("//" + target)
        if parts.path or parts.query or parts.fragment:
            return None
        path = ""
    else:
        parts = urlsplit(target)
        if parts.scheme.lower() != "http":
            return None
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if "@" in parts.netloc or not parts.hostname:
        return None
    port = parts.port or (None if method == "CONNECT" else 80)
    return (parts.hostname, port, path) if port else None


async def _handle(reader, writer, resolve, allow) -> None:
    upstream = None
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, OSError):
            return
        lines = head.decode("latin-1").split("\r\n")
        request = lines[0].split(" ")
        try:
            parsed = _target(request[0].upper(), request[1]) if len(request) == 3 else None
        except ValueError:
            parsed = None
        if parsed is None:
            _reply(writer, "400 Bad Request", "Row-Bot's sandbox network gate takes CONNECT or an http:// URL.")
            return
        method, version = request[0].upper(), request[2]
        host, port, path = parsed
        try:
            addresses = await asyncio.wait_for(resolve(host, port), 15)
        except (OSError, asyncio.TimeoutError):
            addresses = []
        reason = refusal(host, port, addresses, allow)
        if reason:
            logger.warning("refused %s:%s: %s", host, port, reason)
            _reply(writer, "403 Forbidden", f"Row-Bot's sandbox network refused {host}: {reason}. "
                                            "The sandbox opens public internet sites only.")
            return
        for address in addresses:
            try:
                upstream = await asyncio.wait_for(asyncio.open_connection(address, port), 15)
                break
            except (OSError, asyncio.TimeoutError):
                continue
        if upstream is None:
            _reply(writer, "502 Bad Gateway", f"Row-Bot's sandbox network couldn't connect to {host}.")
            return
        up_reader, up_writer = upstream
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            kept = [line for line in lines[1:] if line and line.split(":", 1)[0].strip().lower() not in DROPPED_HEADERS]
            authority = host if port == 80 else f"{host}:{port}"
            if ":" in host:
                authority = f"[{host}]" if port == 80 else f"[{host}]:{port}"
            up_writer.write("\r\n".join([f"{method} {path} {version}", f"Host: {authority}", *kept,
                                         "Connection: close", "", ""]).encode("latin-1"))

        def close_all() -> None:
            writer.close()
            up_writer.close()

        await asyncio.gather(_pipe(reader, up_writer, close_all), _pipe(up_reader, writer, close_all))
    finally:
        writer.close()
        if upstream is not None:
            upstream[1].close()


async def start(host: str, port: int, *, resolve=_resolve, allow=public_address):
    """The gate's server, listening; ``resolve`` and ``allow`` are seams for tests."""
    return await asyncio.start_server(lambda r, w: _handle(r, w, resolve, allow), host, port, limit=HEAD_LIMIT)


async def _serve(host: str, port: int) -> None:
    server = await start(host, port)
    async with server:
        await server.serve_forever()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Row-Bot's sandbox network gate")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3128)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_serve(args.host, args.port))


if __name__ == "__main__":
    main()
