"""URL / Webpage Reader tool — fetch and extract text from any URL."""

from __future__ import annotations

import io
import re
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.integrations.safe import _Pinned, _public_addresses
from row_bot.tools.base import BaseTool
from row_bot.tools import registry


# One page of a fetched page's text: fixed, not scaled to the model's context window.
PAGE_CHARS = 40_000
PAGE_CHARS_MAX = 100_000


class _ReadURLInput(BaseModel):
    url: str = Field(description="The full URL to read (must start with http:// or https://)")
    offset: int = Field(default=0, description="Character offset to start from, to read on in a long page.")
    limit: int = Field(default=PAGE_CHARS, description=f"Characters to return, at most {PAGE_CHARS_MAX}.")


_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
_PDF_MAX_BYTES = 200 * 1024 * 1024
_PDF_MAX_PAGES = 2000
_TIMEOUT = 15
_MAX_REDIRECTS = 10
_REDIRECTS = {301, 302, 303, 307, 308}
_NOT_PUBLIC = "not_public"
NOT_PUBLIC_MESSAGE = (
    "This link can't be read: read_url opens public web pages, and an address on this computer or your "
    "network only once this chat allows it; never a cloud metadata service, and not a link that redirects "
    "to an address this chat hasn't allowed."
)


class _NotPublic(Exception):
    pass


def _browser_grants():
    """The browser's grants and this conversation: an address allowed in either tool opens in both."""
    from row_bot.tools import browser_tool

    conversation = browser_tool._get_thread_id()
    return browser_tool.get_session_manager().get_session(conversation), conversation


def _local_permission(url: str) -> str | None:
    """Ask before reading an address on this computer or the person's network (5.3.1 A2): the refusal, or None.

    None too for any other link, which ``_public_get`` checks. Like the browser, it asks once per address
    and chat by the approval level, and a server Row-Bot started in this chat opens without asking. Row-Bot
    itself never does.
    """
    from row_bot.browser import local_access as _local
    from row_bot.tools.approval_gate import approval_check

    origin = _local.local_development_origin(url)
    if origin is None:
        return None
    if _local.is_row_bot_origin(origin):
        return _local.row_bot_refusal(origin)
    session, conversation = _browser_grants()
    if session.local_address_grant(conversation, origin) is not None:
        return None
    if _local.row_bot_server(origin, conversation):
        session.allow_local_address(conversation, origin, source="row_bot_server")
        return None
    label = _local.address_label(origin)
    refusal, line = approval_check(
        {**_local.approval_request(origin, tool="read_url"),
         "question": f"Allow Row-Bot to read {label} in this chat?"},
        risks=("reads an address on this computer or your network",),
        blocked_message=(f"BLOCKED: Reading {label} needs approval, and this chat is on Block. "
                         "Ask the person to allow it or change the approval level."),
        cancelled_message=f"The person didn't allow {label}; it wasn't read.",
    )
    if refusal:
        return refusal
    session.allow_local_address(conversation, origin, source=_local.source_of(line))
    return None


def _granted_addresses(url: str) -> list[str] | None:
    """The addresses of a local link this chat allowed, looked up once; None when it isn't allowed."""
    from row_bot.browser import local_access as _local
    from row_bot.browser.addresses import canonical_address, is_metadata_address

    origin = _local.local_development_origin(url)
    if origin is None or _local.is_row_bot_origin(origin):
        return None
    session, conversation = _browser_grants()
    if session.local_address_grant(conversation, origin) is None:
        return None
    parts = urlsplit(url)
    try:
        found = socket.getaddrinfo(parts.hostname, _local.origin_port(origin), type=socket.SOCK_STREAM)
    except OSError:
        raise ConnectionError(f"could not resolve {parts.hostname}") from None
    addresses = list(dict.fromkeys(item[4][0].split("%", 1)[0] for item in found))
    if not addresses or any(is_metadata_address(canonical_address(address)) for address in addresses):
        return None
    return addresses


@contextmanager
def _public_get(url: str) -> Iterator[tuple[str, httpx.Response]]:
    """GET a public web page, yielding the final URL and its response.

    A page the agent reads can tell it what to read next, so each hop (the link and every redirect) is
    checked on its own: http(s) without a user name or password, a name that resolves only to public
    addresses (or an address on this computer or the network this chat allowed), and a connection to one of
    those checked addresses with no second lookup, so a rebinding name can't swap in a private one.
    """
    deadline = time.monotonic() + _TIMEOUT * 4
    for _hop in range(_MAX_REDIRECTS + 1):
        try:
            target = httpx.URL(url)
        except (httpx.InvalidURL, TypeError) as exc:
            raise ValueError(f"not a valid link: {exc}") from None
        if target.scheme not in {"http", "https"} or not target.host or target.userinfo:
            raise ValueError("only http:// and https:// links without a user name or password can be read")
        try:
            addresses = _public_addresses(target.raw_host.decode("ascii").rstrip("."), _NOT_PUBLIC)
        except ValueError as exc:
            addresses = _granted_addresses(str(target))
            if addresses is None:
                raise _NotPublic() from exc
        except ConnectionError:
            raise ConnectionError(f"could not resolve {target.host}") from None
        transport = _Pinned(target.host, addresses, deadline)
        with httpx.Client(transport=transport, timeout=_TIMEOUT, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", target, headers={"User-Agent": _USER_AGENT}) as response:
                location = response.headers.get("Location", "")
                if response.status_code in _REDIRECTS and location:
                    url = urljoin(str(target), location)
                    continue
                yield str(target), response
                return
    raise ValueError(f"the link redirected more than {_MAX_REDIRECTS} times")


def _is_pdf(response, url: str, head: bytes) -> bool:
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    return (content_type == "application/pdf" or head.startswith(b"%PDF-")
            or (content_type in {"", "application/octet-stream"} and urlsplit(url).path.lower().endswith(".pdf")))


def _pdf_text(data: bytes) -> str:
    """Text of a PDF's first pages; a PDF link used to come back as binary noise (B297)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for number, page in enumerate(reader.pages[:_PDF_MAX_PAGES], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Page {number}]\n{text}")
    if len(reader.pages) > _PDF_MAX_PAGES:
        pages.append(f"[Only the first {_PDF_MAX_PAGES} of {len(reader.pages)} pages were read.]")
    return "\n\n".join(pages)


def _collapsed(text: str) -> str:
    """Plain text with runs of spaces and blank lines collapsed."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _retry_after(value: str | None) -> float:
    """How long to wait before reading a rate-limited page again: what the site asks (in seconds), at most 10."""
    try:
        return min(10.0, max(0.0, float(value or 3)))
    except ValueError:
        return 3.0


def _read_url(url: str, offset: int = 0, limit: int = PAGE_CHARS, *, readable: bool = True) -> str:
    """Fetch a webpage or PDF and return a page of its text content.

    A web page reads as its main content in Markdown; ``readable=False`` keeps all of its text, menus,
    sidebars and footers included, for a page wait looking for text anywhere on it.
    """
    from bs4 import BeautifulSoup

    from row_bot.tools.readable_page import readable_markdown

    url = str(url or "").strip()
    # Asked before fetching: the approval pauses the turn, which a fetch error must not swallow.
    refusal = _local_permission(url)
    if refusal:
        return refusal
    try:
        for attempt in range(2):
            with _public_get(url) as (source, response):
                status = response.status_code
                # A site's block or rate limit: its error page is not the article (U10). A rate limit is read
                # again once, after the wait the site asks for.
                if status == 429 and attempt == 0:
                    wait = _retry_after(response.headers.get("Retry-After"))
                elif status in (403, 429):
                    return (f"The site refused to be read just now ({status}"
                            f"{': too many requests' if status == 429 else ''}). Wait a minute and try again, "
                            "or open the page in the browser.")
                else:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes(64 * 1024):
                        size += len(chunk)
                        if size > _PDF_MAX_BYTES:
                            break
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if _is_pdf(response, source, data[:5]):
                        if size > _PDF_MAX_BYTES:
                            return f"The PDF is larger than {_PDF_MAX_BYTES // (1024 * 1024)} MB, too large to read."
                        text = _collapsed(_pdf_text(data))
                    else:
                        # BeautifulSoup reads the page's own charset when the server names none.
                        content_type = response.headers.get("Content-Type", "")
                        charset = content_type.partition("charset=")[2].strip(" \"';") or None
                        soup = BeautifulSoup(data, "html.parser", from_encoding=charset)
                        # A web page reads as its main content in Markdown (#139); other text as it is.
                        text = (readable_markdown(soup, source) if readable and "html" in content_type.lower()
                                else _collapsed(soup.get_text()))
                    break
            time.sleep(wait)
    except _NotPublic:
        return NOT_PUBLIC_MESSAGE
    except Exception as exc:
        return f"Failed to fetch URL: {exc}"

    if not text:
        return "The page was fetched but no readable text content was found."

    # A long page is read a window at a time; the footer says where to continue.
    from row_bot.tool_outputs import page_chars

    limit = min(max(1_000, int(limit or PAGE_CHARS)), PAGE_CHARS_MAX)
    window, start, end = page_chars(text, offset=offset, limit=limit)
    total = len(text)
    if start == 0 and end >= total:
        return f"SOURCE_URL: {source}\n\n{text}"
    if start >= total:
        return f"SOURCE_URL: {source}\n\n[Offset {start:,} is past the end: the page has {total:,} characters.]"
    more = f"Continue with offset={end}." if end < total else "End of page."
    return f"SOURCE_URL: {source}\n\n{window}\n\n[Characters {start:,}–{end:,} of {total:,}. {more}]"


class URLReaderTool(BaseTool):

    @property
    def name(self) -> str:
        return "url_reader"

    @property
    def display_name(self) -> str:
        return "🌐 URL Reader"

    @property
    def description(self) -> str:
        return (
            "Fetch and read the text content of any webpage or URL. "
            "Use this when the user provides a link or asks about the "
            "content of a specific webpage."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {"read_url"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_read_url,
                name="read_url",
                description=(
                    "Fetch a webpage or PDF and return its readable text. Use this whenever the person "
                    "shares a link or asks about a specific page. A long page comes in parts: the result "
                    "ends with the offset to continue from."
                ),
                args_schema=_ReadURLInput,
            )
        ]

    def execute(self, query: str) -> str:
        return _read_url(query)


registry.register(URLReaderTool())
