"""Fake Microsoft Graph and Google Drive sources for the Org server's connectors, served through
``httpx.MockTransport`` (nothing leaves the process: no real Microsoft or Google host is ever called).

Each fake holds a drive of folders and files, a change log its feed reads (``change``/``move``/``remove``/
``share``), the permissions of each file, and every request it was sent (``requests``). Pages are small
(``page_size``) so listings and feeds come in several pages. ``refuse_sign_in`` makes the token endpoint refuse
(with a body that quotes the secret, as a careless server might), ``throttle`` answers every data call 429 with that
Retry-After, and the access token is checked on every data call.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

GRAPH = "https://graph.microsoft.com/v1.0"
SHAREPOINT = "https://contoso.sharepoint.com"
TENANT = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
CLIENT_ID = "0d5f3a1e-1111-4222-8333-444455556666"
CLIENT_SECRET = "fake-graph-client-secret-Q9x~not-real"
ACCESS_TOKEN = "fake-access-token-7b1d0c-not-real"
GOOGLE_EMAIL = "row-bot-sync@acme-project.iam.gserviceaccount.com"
DRIVE_API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
GOOGLE_DOC = "application/vnd.google-apps.document"


def _json(status: int, body: Any, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


@dataclass
class Node:
    id: str
    name: str
    parent: str | None
    folder: bool
    content: bytes = b""
    permissions: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1
    removed: bool = False
    mime: str = "text/markdown"
    # Larger than its content (a file the fake says is big, without holding it).
    size: int | None = None


class _Fake:
    """What both fakes share: the tree, the change log, the requests."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.nodes: dict[str, Node] = {root: Node(root, "root", None, True)}
        self.log: list[str] = []
        self.requests: list[httpx.Request] = []
        self.page_size = 2
        self.refuse_sign_in = False
        self.throttle: int | None = None

    # -- the drive -----------------------------------------------------------------------------------

    def folder(self, node_id: str, name: str, parent: str) -> None:
        self.nodes[node_id] = Node(node_id, name, parent, True)
        self.log.append(node_id)

    def file(self, node_id: str, name: str, parent: str, content: bytes, permissions: list[dict[str, Any]],
             **extra: Any) -> None:
        self.nodes[node_id] = Node(node_id, name, parent, False, content, list(permissions), **extra)
        self.log.append(node_id)

    def change(self, node_id: str, content: bytes) -> None:
        node = self.nodes[node_id]
        node.content, node.version = content, node.version + 1
        self.log.append(node_id)

    def move(self, node_id: str, parent: str) -> None:
        """Only the moved item shows up in the feed (as Graph's and Drive's do), not what it holds."""
        self.nodes[node_id].parent = parent
        self.log.append(node_id)

    def remove(self, node_id: str) -> None:
        self.nodes[node_id].removed = True
        self.log.append(node_id)

    def share(self, node_id: str, permissions: list[dict[str, Any]]) -> None:
        self.nodes[node_id].permissions = list(permissions)
        self.log.append(node_id)

    def children(self, parent: str) -> list[Node]:
        return [node for node in self.nodes.values() if node.parent == parent and not node.removed]

    def changed_since(self, position: int) -> list[Node]:
        """Each item changed since ``position`` of the log, once, in the order of its last change."""
        seen: dict[str, None] = {}
        for node_id in self.log[position:]:
            seen.pop(node_id, None)
            seen[node_id] = None
        return [self.nodes[node_id] for node_id in seen]

    def paged(self, entries: list[Any], start: int) -> tuple[list[Any], int | None]:
        end = start + self.page_size
        return entries[start:end], (end if end < len(entries) else None)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)

    def answer(self, request: httpx.Request) -> httpx.Response:
        raise NotImplementedError

    def to(self, host: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == host]

    def authorised(self, request: httpx.Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {ACCESS_TOKEN}"


# -- Microsoft Graph ------------------------------------------------------------------------------------

def group_permission(group_id: str, name: str) -> dict[str, Any]:
    return {"id": f"perm-{group_id[:8]}", "roles": ["read"],
            "grantedToV2": {"group": {"id": group_id, "displayName": name}}}


def user_permission(user_id: str, name: str, email: str | None) -> dict[str, Any]:
    user: dict[str, Any] = {"id": user_id, "displayName": name}
    if email is not None:
        user["email"] = email
    return {"id": f"perm-{user_id}", "roles": ["write"], "grantedToV2": {"user": user}}


def site_group_permission(group_id: str, name: str) -> dict[str, Any]:
    return {"id": f"perm-site-{group_id}", "roles": ["read"],
            "grantedToV2": {"siteGroup": {"id": group_id, "displayName": name, "loginName": name}}}


def link_permission(scope: str) -> dict[str, Any]:
    return {"id": f"perm-link-{scope}", "roles": ["read"], "link": {"scope": scope, "type": "view"}}


class FakeGraph(_Fake):
    """One drive (``drive``) whose root is ``root-1``."""

    def __init__(self, drive: str = "b!drive-hr-1") -> None:
        super().__init__("root-1")
        self.drive = drive

    def _item(self, node: Node) -> dict[str, Any]:
        if node.removed:
            return {"id": node.id, "deleted": {"state": "deleted"}, "parentReference": {"driveId": self.drive}}
        if node.id == self.root:
            return {"id": node.id, "name": "root", "root": {}, "folder": {"childCount": len(self.children(node.id))}}
        entry: dict[str, Any] = {"id": node.id, "name": node.name, "eTag": f"e{node.id}.{node.version}",
                                 "parentReference": {"driveId": self.drive, "id": node.parent}}
        if node.folder:
            entry["folder"] = {"childCount": len(self.children(node.id))}
        else:
            entry.update({"file": {"mimeType": node.mime}, "cTag": f"c{node.id}.{node.version}",
                          "size": node.size if node.size is not None else len(node.content)})
        return entry

    def answer(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "login.microsoftonline.com":
            return self._token(request)
        if url.host == "contoso.sharepoint.com":
            node = self.nodes[url.path.rsplit("/", 1)[-1]]
            return httpx.Response(200, content=node.content)
        assert url.host == "graph.microsoft.com", url
        if not self.authorised(request):
            return _json(401, {"error": {"code": "InvalidAuthenticationToken"}})
        if self.throttle is not None:
            return _json(429, {"error": {"code": "activityLimitReached"}}, **{"Retry-After": str(self.throttle)})
        prefix = f"/v1.0/drives/{self.drive}/"
        assert url.path.startswith(prefix), url.path
        path = url.path[len(prefix):]
        params = url.params
        if path == "root":
            return _json(200, self._item(self.nodes[self.root]))
        if path == "root/delta":
            return self._delta(params)
        match = re.fullmatch(r"items/([^/]+)(/children|/permissions|/content)?", path)
        assert match is not None, path
        node = self.nodes.get(match.group(1))
        if node is None or node.removed:
            return _json(404, {"error": {"code": "itemNotFound"}})
        if match.group(2) is None:
            return _json(200, self._item(node))
        if match.group(2) == "/permissions":
            return _json(200, {"value": node.permissions})
        if match.group(2) == "/content":
            return httpx.Response(302, headers={"Location": f"{SHAREPOINT}/_layouts/download/{node.id}"})
        start = int(params.get("$skiptoken", "0"))
        page, following = self.paged(self.children(node.id), start)
        body: dict[str, Any] = {"value": [self._item(child) for child in page]}
        if following is not None:
            body["@odata.nextLink"] = f"{GRAPH}/drives/{self.drive}/items/{node.id}/children?$skiptoken={following}"
        return _json(200, body)

    def _token(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == f"/{TENANT}/oauth2/v2.0/token", request.url
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        if (self.refuse_sign_in or form.get("client_secret") != CLIENT_SECRET or form.get("client_id") != CLIENT_ID
                or form.get("grant_type") != "client_credentials"
                or form.get("scope") != "https://graph.microsoft.com/.default"):
            return _json(401, {"error": "invalid_client",
                               "error_description": f"AADSTS7000215: Invalid client secret {CLIENT_SECRET}"})
        return _json(200, {"token_type": "Bearer", "expires_in": 3599, "access_token": ACCESS_TOKEN})

    def _delta(self, params: httpx.QueryParams) -> httpx.Response:
        token = params.get("token")
        if token == "latest":
            return _json(200, {"value": [], "@odata.deltaLink": self._delta_link(len(self.log))})
        position = int(str(token).removeprefix("t"))
        start = int(params.get("skip", "0"))
        page, following = self.paged(self.changed_since(position), start)
        body: dict[str, Any] = {"value": [self._item(node) for node in page]}
        if following is None:
            body["@odata.deltaLink"] = self._delta_link(len(self.log))
        else:
            body["@odata.nextLink"] = f"{self._delta_link(position)}&skip={following}"
        return _json(200, body)

    def _delta_link(self, position: int) -> str:
        return f"{GRAPH}/drives/{self.drive}/root/delta?token=t{position}"


# -- Google Drive -----------------------------------------------------------------------------------------

def google_user(email: str) -> dict[str, Any]:
    return {"id": f"perm-{email}", "type": "user", "role": "reader", "emailAddress": email}


def google_group(email: str) -> dict[str, Any]:
    return {"id": f"perm-{email}", "type": "group", "role": "writer", "emailAddress": email}


def google_domain(domain: str) -> dict[str, Any]:
    return {"id": f"perm-{domain}", "type": "domain", "role": "reader", "domain": domain}


def _b64url(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


class FakeDrive(_Fake):
    """One shared drive (its root folder's id is the drive's id), and a service account whose assertions it checks
    against its public key."""

    def __init__(self, drive: str = "0AsharedDriveHR") -> None:
        super().__init__(drive)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.subjects: list[str | None] = []

    def key_file(self) -> str:
        pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode("ascii")
        return json.dumps({"type": "service_account", "project_id": "acme-project", "private_key_id": "k1",
                           "private_key": pem, "client_email": GOOGLE_EMAIL, "client_id": "1234567890",
                           "token_uri": "https://oauth2.googleapis.com/token"})

    def private_key_pem(self) -> str:
        return json.loads(self.key_file())["private_key"]

    def _file(self, node: Node) -> dict[str, Any]:
        entry: dict[str, Any] = {"id": node.id, "name": node.name, "parents": [node.parent],
                                 "mimeType": FOLDER if node.folder else node.mime, "trashed": node.removed,
                                 "modifiedTime": f"2026-11-0{min(node.version, 9)}T10:00:00.000Z"}
        if not node.folder and node.mime != GOOGLE_DOC:
            entry.update({"size": str(len(node.content)),
                          "md5Checksum": hashlib.md5(node.content, usedforsecurity=False).hexdigest()})
        return entry

    def answer(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "oauth2.googleapis.com":
            return self._token(request)
        assert url.host == "www.googleapis.com" and url.path.startswith("/drive/v3/"), url
        if not self.authorised(request):
            return _json(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
        if self.throttle is not None:
            return _json(429, {"error": {"code": 429}}, **{"Retry-After": str(self.throttle)})
        path, params = url.path.removeprefix("/drive/v3/"), url.params
        if path == "changes/startPageToken":
            return _json(200, {"startPageToken": str(len(self.log))})
        if path == "changes":
            return self._changes(params)
        if path == "files":
            folder = re.fullmatch(r"'([A-Za-z0-9_-]+)' in parents and trashed = false", params["q"]).group(1)
            page, following = self.paged(self.children(folder), int(params.get("pageToken", "0")))
            body: dict[str, Any] = {"files": [self._file(node) for node in page]}
            if following is not None:
                body["nextPageToken"] = str(following)
            return _json(200, body)
        match = re.fullmatch(r"files/([A-Za-z0-9_-]+)(/permissions|/export)?", path)
        assert match is not None, path
        node = self.nodes.get(match.group(1))
        if node is None or node.removed:
            return _json(404, {"error": {"code": 404, "message": "File not found"}})
        if match.group(2) == "/permissions":
            return _json(200, {"permissions": node.permissions})
        if match.group(2) == "/export":
            assert node.mime == GOOGLE_DOC and params["mimeType"] == "text/plain"
            return httpx.Response(200, content=node.content)
        if params.get("alt") == "media":
            return httpx.Response(200, content=node.content)
        return _json(200, self._file(node))

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        header, claims, signature = form["assertion"].split(".")
        self.key.public_key().verify(_b64url(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(),
                                     hashes.SHA256())
        said = json.loads(_b64url(claims))
        assert json.loads(_b64url(header)) == {"alg": "RS256", "typ": "JWT"}
        assert (said["iss"], said["aud"], said["scope"]) == (
            GOOGLE_EMAIL, "https://oauth2.googleapis.com/token", "https://www.googleapis.com/auth/drive.readonly")
        assert said["exp"] - said["iat"] == 3600
        self.subjects.append(said.get("sub"))
        if self.refuse_sign_in:
            return _json(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."})
        return _json(200, {"access_token": ACCESS_TOKEN, "expires_in": 3599, "token_type": "Bearer"})

    def _changes(self, params: httpx.QueryParams) -> httpx.Response:
        position, _, start = params["pageToken"].partition("x")
        page, following = self.paged(self.changed_since(int(position)), int(start or "0"))
        changes = [{"fileId": node.id, "removed": False, "file": self._file(node)} for node in page]
        body: dict[str, Any] = {"changes": changes}
        if following is None:
            body["newStartPageToken"] = str(len(self.log))
        else:
            body["nextPageToken"] = f"{position}x{following}"
        return _json(200, body)


__all__ = ["ACCESS_TOKEN", "CLIENT_ID", "CLIENT_SECRET", "GOOGLE_DOC", "GOOGLE_EMAIL", "TENANT", "FakeDrive",
           "FakeGraph", "google_domain", "google_group", "google_user", "group_permission", "link_permission",
           "site_group_permission", "user_permission"]
