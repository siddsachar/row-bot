"""The two sources a connector reads: Microsoft Graph (a SharePoint or OneDrive drive) and the Google Drive API.

Each is called only at its documented hosts (``MICROSOFT_HOSTS``, ``GOOGLE_HOSTS``), over HTTPS, with a timeout on
every call, and no redirect is followed, except Graph's download redirect, which is fetched once, without the access
token, only when it points at SharePoint (``*.sharepoint.com``, the pre-authenticated download Graph documents).
Answers are read up to ``MAX_JSON_BYTES`` and files up to the brain's upload limit.

Signing in, as the application itself (no person's account):
- Microsoft: the client-credentials grant at ``login.microsoftonline.com/<tenant>`` for
  ``https://graph.microsoft.com/.default`` (an app registration with ``Sites.Selected`` granted on the site, or
  ``Files.Read.All``, with admin consent).
- Google: a service account's signed assertion (RS256) at ``oauth2.googleapis.com/token`` for
  ``drive.readonly``, acting as ``subject`` when domain-wide delegation is used.

Failures are one of: ``Refused`` (the source said no: the sign-in, or access to the drive or folder; repeated, the
connector stops), ``Throttled`` (429: wait as told), ``Unavailable`` (5xx, a timeout, an unreachable host, an answer
that can't be read: wait longer each time), ``FeedExpired`` (Graph's 410: list everything again) and ``ItemGone``
(one file or folder can't be read: it is skipped). Their messages never hold a secret, a token or a URL.
"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from row_bot_org.connectors.mirror import GROUP, OTHER, PERSON, Grant

MICROSOFT_HOSTS = frozenset({"login.microsoftonline.com", "graph.microsoft.com"})
GOOGLE_HOSTS = frozenset({"oauth2.googleapis.com", "www.googleapis.com"})
SHAREPOINT_SUFFIX = ".sharepoint.com"
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE = "https://www.googleapis.com/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_JSON_BYTES = 8 * 1024 * 1024
# A Retry-After is waited at most this long; with none, the caller backs off on its own.
MAX_RETRY_AFTER = 3600.0
PAGE_SIZE = 200
# The longest id, and link or page token, the connector keeps (as its columns do); a longer one is the source's error.
MAX_ID = 512
MAX_LINK = 8192
# Graph's sharing changes come only with this header, on the feed and on the cursor it starts from.
GRAPH_PREFER = "deltashowsharingchanges, hierarchicalsharing"
_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_GRAPH_ITEM = "id,name,size,file,folder,root,package,parentReference,cTag,eTag,deleted"
_DRIVE_FILE = "id,name,mimeType,parents,size,md5Checksum,modifiedTime,trashed"
_DRIVE_ID_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class SourceError(Exception):
    """A call to the source failed; ``reason`` says how, for an admin to read (no secret, token or URL)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Refused(SourceError):
    """The source refused the connector: its sign-in, or access to what it reads."""


class Unavailable(SourceError):
    """The source can't answer now (5xx, a timeout, an unreachable host, an answer that can't be read)."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


class Throttled(Unavailable):
    """429: the source asked the connector to wait."""


class FeedExpired(SourceError):
    """The change feed's cursor is too old: everything is listed again."""


class ItemGone(SourceError):
    """One file or folder can't be read (gone, or refused): it is skipped."""


class TooLarge(SourceError):
    """A file over the brain's upload limit."""


@dataclass(frozen=True)
class Item:
    """A file or folder upstream, as both sources describe it."""

    id: str
    parent_id: str | None
    name: str
    is_folder: bool
    media_type: str | None = None
    size: int | None = None
    # The content's version: Graph's cTag (eTag without one), Drive's md5Checksum (modifiedTime for a Google Doc).
    version: str | None = None
    removed: bool = False
    # Whether its sharing changed (Graph's @microsoft.graph.sharedChanged); None when the source doesn't say.
    shared_changed: bool | None = None


@dataclass(frozen=True)
class Page:
    items: list[Item]
    # Where to carry on (a link or page token), or None when this was the last page.
    next: str | None = None
    # A feed's new cursor, on its last page.
    cursor: str | None = None


@dataclass(frozen=True)
class Credentials:
    kind: str
    tenant_id: str | None
    client_id: str
    # The client secret, or the service account's private key (PEM). Never in the repr.
    secret: str = field(default="", repr=False)
    subject: str | None = None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    if value.isdigit():
        return min(float(value), MAX_RETRY_AFTER)
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return max(0.0, min(when - time.time(), MAX_RETRY_AFTER))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def google_assertion(client_email: str, private_key: str, subject: str | None, *, now: float | None = None) -> str:
    """The service account's signed JWT (RS256) for Drive's read-only scope."""
    key = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise Refused("The service account's key isn't an RSA key.")
    issued = int(time.time() if now is None else now)
    claims: dict[str, Any] = {"iss": client_email, "scope": DRIVE_SCOPE, "aud": GOOGLE_TOKEN_URL, "iat": issued,
                              "exp": issued + 3600}
    if subject:
        claims["sub"] = subject
    signing = (_b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode()) + "."
               + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    return signing + "." + _b64(key.sign(signing.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()))


class _Http:
    """Calls to one source's hosts, with its access token once signed in. Every answer is read as a stream, up to
    its limit: nothing larger is held in memory."""

    def __init__(self, client: httpx.Client, hosts: frozenset[str], what: str) -> None:
        self.client = client
        self.hosts = hosts
        self.what = what
        self.token: str | None = None

    def check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self.hosts or parts.username or parts.password:
            raise Unavailable(f"{self.what} pointed the connector at another address.")

    def _call(self, method: str, url: str, limit: int, *, too_large: SourceError,
              **options: Any) -> tuple[httpx.Response, bytes]:
        """The answer to one call and its body, read up to ``limit`` bytes (past it, ``too_large``); a redirect's
        body isn't read."""
        self.check_url(url)
        try:
            with self.client.stream(method, url, **options) as response:
                body = bytearray()
                if not response.is_redirect:
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > limit:
                            raise too_large
                return response, bytes(body)
        except httpx.TimeoutException:
            raise Unavailable(f"{self.what} didn't answer in time.") from None
        except httpx.HTTPError:
            raise Unavailable(f"{self.what} can't be reached.") from None

    def failed(self, response: httpx.Response, body: bytes, *, item: bool = False) -> SourceError:
        status = response.status_code
        if status == 429:
            return Throttled(f"{self.what} asked the connector to slow down.", _retry_after(response))
        if status >= 500:
            return Unavailable(f"{self.what} had a problem (HTTP {status}).", _retry_after(response))
        if status == 410:
            return FeedExpired(f"{self.what} asked for everything to be listed again.")
        if status == 403 and self._rate_limited(body):
            return Throttled(f"{self.what} asked the connector to slow down.", _retry_after(response))
        if item and status in (403, 404):
            return ItemGone(f"{self.what} wouldn't give this file (HTTP {status}).")
        if status == 401:
            return Refused(f"{self.what} refused the connector's sign-in.")
        if status == 403:
            return Refused(f"{self.what} refused the connector access. Check the permissions it was granted.")
        if status == 404:
            return Refused(f"{self.what} can't find the drive or folder this connector reads.")
        return Unavailable(f"{self.what} refused a request (HTTP {status}).")

    @staticmethod
    def _rate_limited(body: bytes) -> bool:
        """Google answers a rate limit as 403 with the reason in its body."""
        try:
            errors = json.loads(body).get("error", {}).get("errors", [])
        except (ValueError, AttributeError):
            return False
        return isinstance(errors, list) and any(
            isinstance(e, dict) and e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded") for e in errors)

    def _object(self, body: bytes) -> dict[str, Any]:
        try:
            found = json.loads(body)
        except ValueError:
            raise Unavailable(f"{self.what} answered with something that isn't JSON.") from None
        if not isinstance(found, dict):
            raise Unavailable(f"{self.what} answered with something unexpected.")
        return found

    def json(self, url: str, *, item: bool = False, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None) -> dict[str, Any]:
        """A JSON object from ``url``; with ``item``, a 403 or 404 is that one item's (``ItemGone``)."""
        sent = dict(headers or {})
        if self.token is not None:
            sent["Authorization"] = f"Bearer {self.token}"
        response, body = self._call("GET", url, MAX_JSON_BYTES, params=params, headers=sent,
                                    too_large=Unavailable(f"{self.what} answered with too much."))
        if response.status_code != 200:
            raise self.failed(response, body, item=item)
        return self._object(body)

    def sign_in(self, url: str, form: dict[str, str]) -> None:
        """Trade ``form`` at the token endpoint for an access token; a 400 or 401 there is a refused sign-in."""
        response, body = self._call("POST", url, MAX_JSON_BYTES, data=form,
                                    too_large=Unavailable(f"{self.what} answered with too much."))
        if response.status_code in (400, 401):
            raise Refused(f"{self.what} refused the connector's sign-in. Check its credentials and their consent.")
        if response.status_code != 200:
            raise self.failed(response, body)
        token = self._object(body).get("access_token")
        if not isinstance(token, str) or not token:
            raise Unavailable(f"{self.what} gave no access token.")
        self.token = token

    def download(self, url: str, limit: int, *, auth: bool = True) -> tuple[httpx.Response | None, bytes]:
        """The body at ``url`` up to ``limit`` bytes (past it, ``TooLarge``); a redirect comes back as the
        response, unread."""
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        response, body = self._call("GET", url, limit, headers=headers,
                                    too_large=TooLarge("This file is larger than the company brain takes."))
        if response.is_redirect:
            return response, b""
        if response.status_code != 200:
            raise self.failed(response, body, item=True)
        return None, body


@contextmanager
def http_client(transport: httpx.BaseTransport | None) -> Iterator[httpx.Client]:
    """One client for a connector's turn: no redirects followed, a timeout on every call, nothing from the
    environment (no proxy or certificate settings it didn't ask for)."""
    with httpx.Client(transport=transport, timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
        yield client


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 1024) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _id(value: Any) -> str | None:
    """An upstream id the connector can keep: a non-empty string of at most ``MAX_ID`` characters."""
    return value if isinstance(value, str) and 0 < len(value) <= MAX_ID else None


def _token(value: Any, what: str) -> str:
    """A page token or link to carry on from, as long as the connector can keep."""
    if not isinstance(value, str) or not value or len(value) > MAX_LINK:
        raise Unavailable(f"{what} gave a page token the connector can't use.")
    return value


# -- Microsoft Graph --------------------------------------------------------------------------------

def graph_item(entry: dict[str, Any]) -> Item | None:
    """An item of a Graph listing or delta page; None for one without an id."""
    item_id = _id(entry.get("id"))
    if item_id is None:
        return None
    parent = entry.get("parentReference")
    # A parent too long to keep is no folder in scope: the item is out of scope.
    parent_id = _id(parent.get("id")) if isinstance(parent, dict) else None
    if "deleted" in entry:
        return Item(item_id, parent_id, _text(entry.get("name")), False, removed=True)
    is_folder = "folder" in entry or "root" in entry
    file = entry.get("file") if isinstance(entry.get("file"), dict) else {}
    version = entry.get("cTag") if isinstance(entry.get("cTag"), str) else entry.get("eTag")
    shared = entry.get("@microsoft.graph.sharedChanged")
    return Item(item_id, parent_id, _text(entry.get("name")), is_folder,
                media_type=_text(file.get("mimeType"), 200) or None, size=_int(entry.get("size")),
                version=version[:512] if isinstance(version, str) else None,
                shared_changed=shared is True or (isinstance(shared, str) and shared.lower() == "true"))


def _graph_identity(identity: Any, fallback: str) -> Grant | None:
    """The grant an identity set (``grantedToV2``, or one of a link's ``grantedToIdentitiesV2``) gives; None for an
    application's (it reads nothing through Row-Bot)."""
    if not isinstance(identity, dict):
        return Grant(OTHER, fallback, "Someone the source doesn't name")
    group = identity.get("group")
    if isinstance(group, dict) and isinstance(group.get("id"), str):
        return Grant(GROUP, f"microsoft-group:{group['id']}", _text(group.get("displayName"), 200) or group["id"],
                     group=group["id"])
    site_group = identity.get("siteGroup")
    if isinstance(site_group, dict):
        key = str(site_group.get("id") or site_group.get("loginName") or "")
        return Grant(OTHER, f"sharepoint-group:{key}", _text(site_group.get("displayName"), 200) or key)
    people = [part for part in (identity.get("user"), identity.get("siteUser")) if isinstance(part, dict)]
    if people:
        email = next((p["email"] for p in people if isinstance(p.get("email"), str) and "@" in p["email"]), None)
        person_id = next((str(p["id"]) for p in people if p.get("id")), fallback)
        name = next((p["displayName"] for p in people if isinstance(p.get("displayName"), str)), email or person_id)
        # A user's id is their Entra object id (a SharePoint site user's is a number).
        user = identity.get("user")
        oid = user.get("id") if isinstance(user, dict) else None
        return Grant(PERSON, f"user:{person_id}"[:300], name[:200], email=email,
                     oid=oid.lower() if isinstance(oid, str) and _OBJECT_ID.fullmatch(oid) else None)
    if isinstance(identity.get("application"), dict):
        return None
    return Grant(OTHER, fallback, "Someone the source doesn't name")


def graph_grants(permission: dict[str, Any]) -> list[Grant]:
    """The grants one Graph permission gives (every permission lets its holders read)."""
    key = f"permission:{_text(permission.get('id'), 200)}"
    link = permission.get("link")
    if isinstance(link, dict):
        scope = _text(link.get("scope"), 40) or "unknown"
        identities = permission.get("grantedToIdentitiesV2") or permission.get("grantedToIdentities") or []
        if scope == "users" and isinstance(identities, list) and identities:
            return [grant for grant in (_graph_identity(i, key) for i in identities) if grant is not None]
        names = {"anonymous": "Anyone with the link", "organization": "Anyone in the organisation with the link"}
        return [Grant(OTHER, f"link:{scope}", names.get(scope, "People with a sharing link"))]
    identity = permission.get("grantedToV2") or permission.get("grantedTo")
    grant = _graph_identity(identity, key)
    return [] if grant is None else [grant]


class MicrosoftSource:
    """A drive (a SharePoint site's document library, or a OneDrive), or a folder in it, through Graph."""

    what = "Microsoft"

    def __init__(self, client: httpx.Client, credentials: Credentials, drive_id: str, folder_id: str | None) -> None:
        self.http = _Http(client, MICROSOFT_HOSTS, self.what)
        self.credentials = credentials
        self.drive = quote(drive_id, safe="!")
        self.folder_id = folder_id

    def sign_in(self) -> None:
        tenant = quote(self.credentials.tenant_id or "", safe="")
        self.http.sign_in(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", {
            "client_id": self.credentials.client_id, "client_secret": self.credentials.secret,
            "scope": GRAPH_SCOPE, "grant_type": "client_credentials"})

    @staticmethod
    def _link(link: Any) -> str:
        """A next or delta link Graph gave, which must stay on Graph (the access token goes with it)."""
        if not isinstance(link, str) or not link.startswith(f"{GRAPH}/"):
            raise Unavailable("Microsoft pointed the connector at another address.")
        return _token(link, "Microsoft")

    def root(self) -> str:
        """The id of the folder everything in scope is under: the folder, or the drive's root."""
        path = f"items/{quote(self.folder_id, safe='')}" if self.folder_id else "root"
        found = self.http.json(f"{GRAPH}/drives/{self.drive}/{path}", params={"$select": "id,folder,root"})
        if not isinstance(found.get("id"), str) or not ("folder" in found or "root" in found):
            raise Refused("Microsoft says what this connector reads isn't a folder.")
        return found["id"]

    def latest_cursor(self) -> str:
        found = self.http.json(f"{GRAPH}/drives/{self.drive}/root/delta", params={"token": "latest"},
                               headers={"Prefer": GRAPH_PREFER})
        return self._link(found.get("@odata.deltaLink"))

    def children(self, folder_id: str, page: str | None) -> Page:
        if page is not None:
            found = self.http.json(self._link(page), item=True)
        else:
            found = self.http.json(f"{GRAPH}/drives/{self.drive}/items/{quote(folder_id, safe='')}/children",
                                   item=True, params={"$top": str(PAGE_SIZE), "$select": _GRAPH_ITEM})
        items = [item for item in map(graph_item, _list(found.get("value"))) if item is not None]
        link = found.get("@odata.nextLink")
        return Page(items, next=None if link is None else self._link(link))

    def changes(self, cursor: str) -> Page:
        found = self.http.json(self._link(cursor), headers={"Prefer": GRAPH_PREFER})
        items = [item for item in map(graph_item, _list(found.get("value"))) if item is not None]
        if (link := found.get("@odata.nextLink")) is not None:
            return Page(items, next=self._link(link))
        return Page(items, cursor=self._link(found.get("@odata.deltaLink")))

    def grants(self, item: Item) -> list[Grant]:
        grants: list[Grant] = []
        url: str | None = f"{GRAPH}/drives/{self.drive}/items/{quote(item.id, safe='')}/permissions"
        while url is not None:
            found = self.http.json(url, item=True)
            for permission in _list(found.get("value")):
                if isinstance(permission, dict):
                    grants.extend(graph_grants(permission))
            link = found.get("@odata.nextLink")
            url = None if link is None else self._link(link)
        return grants

    def download(self, item: Item, limit: int) -> bytes:
        redirect, body = self.http.download(
            f"{GRAPH}/drives/{self.drive}/items/{quote(item.id, safe='')}/content", limit)
        if redirect is None:
            return body
        location = redirect.headers.get("location", "")
        parts = urlsplit(location)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host.endswith(SHAREPOINT_SUFFIX) or parts.username or parts.password:
            raise ItemGone("Microsoft sent this file's download somewhere the connector doesn't go.")
        # The download URL is pre-authenticated: the access token never goes to it.
        sharepoint = _Http(self.http.client, frozenset({host}), self.what)
        redirect_again, body = sharepoint.download(location, limit, auth=False)
        if redirect_again is not None:
            raise ItemGone("Microsoft sent this file's download on again.")
        return body


# -- Google Drive -----------------------------------------------------------------------------------

def drive_item(entry: dict[str, Any], *, removed: bool = False) -> Item | None:
    """A file of a Drive listing or change; None for one without an id."""
    item_id = _id(entry.get("id"))
    if item_id is None:
        return None
    parents = entry.get("parents")
    parent_id = _id(parents[0]) if isinstance(parents, list) and parents else None
    mime = _text(entry.get("mimeType"), 200)
    name = _text(entry.get("name"))
    if removed or entry.get("trashed") is True:
        return Item(item_id, parent_id, name, mime == FOLDER_MIME, removed=True)
    if mime == GOOGLE_DOC_MIME:
        # Exported as text (export), so it is read as a .txt file.
        return Item(item_id, parent_id, f"{name}.txt", False, media_type=mime,
                    version=_text(entry.get("modifiedTime"), 512) or None)
    version = entry.get("md5Checksum") or entry.get("modifiedTime")
    return Item(item_id, parent_id, name, mime == FOLDER_MIME, media_type=mime or None, size=_int(entry.get("size")),
                version=version[:512] if isinstance(version, str) else None)


def drive_grants(permission: dict[str, Any]) -> list[Grant]:
    """The grant one Drive permission gives (every role reads); none for a deleted account's."""
    if permission.get("deleted") is True:
        return []
    kind = permission.get("type")
    email = _text(permission.get("emailAddress"), 320)
    if kind == "user":
        key = f"user:{_text(permission.get('id'), 200)}"
        return [Grant(PERSON, key, email or key, email=email or None)]
    if kind == "group" and email:
        return [Grant(GROUP, f"google-group:{email.lower()}", email, group=email)]
    if kind == "domain":
        domain = _text(permission.get("domain"), 253).lower()
        return [Grant(OTHER, f"google-domain:{domain}", f"Everyone at {domain}")]
    if kind == "anyone":
        return [Grant(OTHER, "google-anyone", "Anyone with the link")]
    return [Grant(OTHER, f"permission:{_text(permission.get('id'), 200)}", "Someone the source doesn't name")]


def _drive_id(value: str) -> str:
    """An id that goes into a Drive query: only the characters Drive's ids have."""
    if not value or not set(value) <= _DRIVE_ID_CHARACTERS:
        raise ItemGone("Google gave an id the connector doesn't use.")
    return value


class GoogleSource:
    """A shared drive, a folder, or a folder in a shared drive, through the Drive API (v3)."""

    what = "Google"

    def __init__(self, client: httpx.Client, credentials: Credentials, drive_id: str | None,
                 folder_id: str | None) -> None:
        self.http = _Http(client, GOOGLE_HOSTS, self.what)
        self.credentials = credentials
        self.drive_id = drive_id
        self.folder_id = folder_id

    def sign_in(self) -> None:
        try:
            assertion = google_assertion(self.credentials.client_id, self.credentials.secret,
                                         self.credentials.subject)
        except (TypeError, ValueError):
            raise Refused("The service account's private key can't be read.") from None
        self.http.sign_in(GOOGLE_TOKEN_URL, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                              "assertion": assertion})

    def _drives(self) -> dict[str, str]:
        common = {"supportsAllDrives": "true"}
        return {**common, "driveId": self.drive_id} if self.drive_id else common

    def root(self) -> str:
        root = _drive_id(self.folder_id or self.drive_id or "")
        found = self.http.json(f"{DRIVE}/files/{root}", params={"fields": "id,mimeType",
                                                                "supportsAllDrives": "true"})
        if found.get("mimeType") != FOLDER_MIME or not isinstance(found.get("id"), str):
            raise Refused("Google says what this connector reads isn't a folder or shared drive.")
        return found["id"]

    def latest_cursor(self) -> str:
        found = self.http.json(f"{DRIVE}/changes/startPageToken", params=self._drives())
        return _token(found.get("startPageToken"), "Google")

    def children(self, folder_id: str, page: str | None) -> Page:
        params = {"q": f"'{_drive_id(folder_id)}' in parents and trashed = false", "pageSize": str(PAGE_SIZE),
                  "fields": f"nextPageToken,files({_DRIVE_FILE})", "includeItemsFromAllDrives": "true",
                  **self._drives()}
        if self.drive_id:
            params["corpora"] = "drive"
        if page is not None:
            params["pageToken"] = page
        found = self.http.json(f"{DRIVE}/files", item=True, params=params)
        items = [item for item in (drive_item(f) for f in _list(found.get("files")) if isinstance(f, dict))
                 if item is not None]
        token = found.get("nextPageToken")
        return Page(items, next=None if token in (None, "") else _token(token, "Google"))

    def changes(self, cursor: str) -> Page:
        found = self.http.json(f"{DRIVE}/changes", params={
            "pageToken": cursor, "pageSize": str(PAGE_SIZE), "includeItemsFromAllDrives": "true",
            "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({_DRIVE_FILE}))",
            **self._drives()})
        items: list[Item] = []
        for change in _list(found.get("changes")):
            if not isinstance(change, dict):
                continue
            file = change.get("file") if isinstance(change.get("file"), dict) else {"id": change.get("fileId")}
            item = drive_item(file, removed=change.get("removed") is True)
            if item is not None:
                items.append(item)
        if found.get("nextPageToken") not in (None, ""):
            return Page(items, next=_token(found["nextPageToken"], "Google"))
        return Page(items, cursor=_token(found.get("newStartPageToken"), "Google"))

    def grants(self, item: Item) -> list[Grant]:
        grants: list[Grant] = []
        page: str | None = None
        while True:
            params = {"supportsAllDrives": "true", "pageSize": "100",
                      "fields": "nextPageToken,permissions(id,type,role,emailAddress,domain,deleted)"}
            if page is not None:
                params["pageToken"] = page
            found = self.http.json(f"{DRIVE}/files/{_drive_id(item.id)}/permissions", item=True, params=params)
            for permission in _list(found.get("permissions")):
                if isinstance(permission, dict):
                    grants.extend(drive_grants(permission))
            if found.get("nextPageToken") in (None, ""):
                return grants
            page = _token(found["nextPageToken"], "Google")

    def download(self, item: Item, limit: int) -> bytes:
        file_id = _drive_id(item.id)
        if item.media_type == GOOGLE_DOC_MIME:
            url = f"{DRIVE}/files/{file_id}/export?mimeType=text%2Fplain"
        else:
            url = f"{DRIVE}/files/{file_id}?alt=media&supportsAllDrives=true"
        redirect, body = self.http.download(url, limit)
        if redirect is not None:
            raise ItemGone("Google sent this file's download somewhere else.")
        return body


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


Source = MicrosoftSource | GoogleSource
SourceFactory = Callable[[httpx.Client], Source]


__all__ = ["DRIVE", "FOLDER_MIME", "GOOGLE_DOC_MIME", "GOOGLE_HOSTS", "GOOGLE_TOKEN_URL", "GRAPH", "MICROSOFT_HOSTS",
           "Credentials", "FeedExpired", "GoogleSource", "Item", "ItemGone", "MicrosoftSource", "Page", "Refused",
           "Source", "SourceError", "Throttled", "TooLarge", "Unavailable", "drive_grants", "drive_item",
           "google_assertion", "graph_grants", "graph_item", "http_client"]
