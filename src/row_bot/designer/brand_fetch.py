"""Brand "From a website": one guarded page fetch, explicitly asked for (parity row 30).

Only http(s) pages on public addresses are read: every address the name
resolves to must be public (never this computer, the local network,
link-local or cloud metadata), the connection goes to the vetted address
itself (no second lookup), redirects are re-checked (at most three), and
only HTML up to 512 KB is read within ten seconds. Nothing is stored; the
panel applies the suggestion through the normal brand control, so Undo works.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
import http.client
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from row_bot.browser.network_security import (
    _canonical_address,
    _default_resolver,
    _is_metadata_address,
    _is_public,
)
from row_bot.designer.client_service import ArtifactError

MAX_BYTES = 512 * 1024
TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
USER_AGENT = "Row-Bot-Designer/1.0 (brand colours)"

Resolver = Callable[[str, int], Iterable[str]]
# (scheme, host, address, port, path+query, timeout) -> (status, headers, body)
Request = Callable[[str, str, str, int, str, float], tuple[int, dict[str, str], bytes]]


def _unavailable() -> ArtifactError:
    return ArtifactError("brand_website_unavailable")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _request(scheme: str, host: str, address: str, port: int, target: str,
             timeout: float) -> tuple[int, dict[str, str], bytes]:
    kind = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    connection = kind(host, address, port, timeout)
    try:
        connection.request("GET", target, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Encoding": "identity",
        })
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(MAX_BYTES + 1)
        return response.status, headers, body
    finally:
        connection.close()


def _target(url: str, resolver: Resolver) -> tuple[str, str, str, int, str]:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise _unavailable() from None
    if (scheme not in {"http", "https"} or not host or parsed.username is not None
            or parsed.password is not None or not 1 <= port <= 65535 or len(url) > 2048):
        raise _unavailable()
    try:
        host = host.encode("idna").decode("ascii")
        addresses = [_canonical_address(item) for item in resolver(host, port)]
    except (OSError, UnicodeError, ValueError):
        raise _unavailable() from None
    if not addresses or any(not _is_public(item) or _is_metadata_address(item) for item in addresses):
        raise _unavailable()
    target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    return scheme, host, str(addresses[0]), port, target


def fetch_public_html(url: str, *, resolver: Resolver = _default_resolver,
                      request: Request = _request) -> str:
    """The HTML of a public web page, or ArtifactError('brand_website_unavailable')."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, address, port, target = _target(current, resolver)
        try:
            status, headers, body = request(scheme, host, address, port, target, TIMEOUT_SECONDS)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            raise _unavailable() from None
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise _unavailable()
            current = urljoin(current, location)
            continue
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if status != 200 or content_type not in {"text/html", "application/xhtml+xml"} or len(body) > MAX_BYTES:
            raise _unavailable()
        charset = "utf-8"
        for part in headers.get("content-type", "").split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.casefold() == "charset" and value.strip():
                charset = value.strip().strip('"')
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")
    raise _unavailable()


def _colour(value: str | None) -> str | None:
    text = (value or "").strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(char * 2 for char in text[1:])
    return text.upper() if len(text) == 7 and all(c in "0123456789abcdefABCDEF" for c in text[1:]) and text[0] == "#" else None


def brand_suggestion(url: str) -> dict:
    """Colours and fonts a website uses, as a suggestion (nothing is saved).

    Colours come back as #RRGGBB; a font only when Row-Bot has it, so the
    brand control can apply everything that is returned.
    """
    from row_bot.designer.brand import brand_from_html
    from row_bot.designer.client_design_controls import _font_items

    html = fetch_public_html(url)
    site = (urlsplit(url).hostname or "").removeprefix("www.")
    found = brand_from_html(html)
    available = {item.id.casefold(): item.id for item in _font_items()}
    colours = {key: _colour(getattr(found, key, None)) for key in ("primary_color", "secondary_color", "accent_color")}
    fonts = {key: available.get((getattr(found, key, None) or "").casefold()) for key in ("heading_font", "body_font")}
    return {"found": any(colours.values()) or any(fonts.values()), "site": site, **colours, **fonts}
