"""A fake X API (v2 with its chunked media upload, and OAuth's token endpoint) for deterministic tests.

It runs in-process as an httpx transport: ``x_accounts.transport = FakeXApi().transport()`` sends
every X request here, so nothing reaches X. Users are known by their access tokens; posts, media
and every request are kept in memory for the test to read. ``after_send`` makes the next create or
delete go through on "X's side" and then lose its answer (a timeout or a dropped connection), and
``before_send`` fails it before it reaches X.

As X does: the v1.1 upload (upload.twitter.com) refuses an OAuth 2.0 sign-in, and v2's upload needs the
sign-in's ``media.write``. A video is processed after finalize; its STATUS says when it's ready. A reply or quote
is refused (403) unless the post's author summoned the account (mentioned it, quoted one of its posts, or is it),
as X's self-serve API does since 23 Feb 2026; ``enterprise`` lifts that rule.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

import httpx

API = "https://api.x.com"
LEGACY_UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"
SCOPE = "tweet.read tweet.write users.read media.write offline.access"
OLD_SCOPE = "tweet.read tweet.write users.read offline.access"  # An account connected before 5.3.1
ROWBOT = ("111", "rowbot_ai", "Row-Bot")
PERSONAL = ("222", "sidd", "Sidd")
REPLY_RULE = "You can only reply to or quote posts where you are mentioned or are the author."


def install(monkeypatch, fake: FakeXApi, *users: tuple[str, str, str]) -> list:
    """Send every X request to ``fake``, with an in-memory keychain and the person's own X app; each user
    in ``users`` (id, handle, name) is a connected account, in that order."""
    from row_bot import secret_store, x_accounts
    from row_bot.tools import x_posting
    from tests.subsystem.plugins.conftest import MemoryKeyring

    monkeypatch.setattr(secret_store, "_backend_override", MemoryKeyring())
    monkeypatch.setattr(x_posting, "_LIMITS", {})  # Each account's post length, read once per test.
    monkeypatch.setattr(x_accounts, "transport", fake.transport())
    monkeypatch.setenv("X_CLIENT_ID", "fixture-x-client")
    monkeypatch.setenv("X_CLIENT_SECRET", "fixture-x-secret")
    return [x_accounts.add(fake.sign_in(*user)) for user in users]


@dataclass
class FakeXApi:
    users: dict[str, dict[str, str]] = field(default_factory=dict)  # access token -> {id, username, name}
    refreshes: dict[str, dict[str, Any]] = field(default_factory=dict)  # refresh token -> the new token
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)  # a sign-in's code -> its token
    posts: list[dict[str, Any]] = field(default_factory=list)
    requests: list[tuple[str, str]] = field(default_factory=list)  # (method, path), in order
    uploaded: dict[str, int] = field(default_factory=dict)  # media id -> bytes appended
    categories: dict[str, str] = field(default_factory=dict)  # media id -> media_category at initialize
    scopes: dict[str, str] = field(default_factory=dict)  # access token -> the scopes it was granted
    after_send: str = ""  # "timeout" or "disconnect": the next create or delete happens, its answer is lost
    before_send: str = ""  # "connect": the next create or delete never reaches X
    fail_reads: bool = False  # Reading posts fails (X down)
    enterprise: bool = False  # X's reply rule doesn't apply (Enterprise access)
    now: str = "2026-10-22T10:00:00.000Z"  # When X says each post was made; "" is the real time (browser fixture).
    _next: int = 1_900_000_000_000_000_000

    def sign_in(self, user_id: str, username: str, name: str = "", *, access: str = "",
                expires_at: float = 4_070_908_800.0, subscription: str = "", scope: str = SCOPE) -> dict[str, Any]:
        """A sign-in (token) for this user, as X's token endpoint would give it; *subscription* is X's
        subscription_type ("Premium" lets the account post up to 25,000 characters), *scope* what it was granted
        (``OLD_SCOPE``: connected before Row-Bot asked for media.write)."""
        access = access or f"access-{username}"
        self.users[access] = {"id": user_id, "username": username, "name": name,
                              **({"subscription_type": subscription} if subscription else {})}
        self.scopes[access] = scope
        return {"access_token": access, "refresh_token": f"refresh-{username}", "expires_at": expires_at,
                "token_type": "bearer", "scope": scope}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def post(self, author: str, text: str, *, created_at: str = "", references: list | None = None) -> dict:
        """A post already on X (written by someone, or sent earlier)."""
        self._next += 1
        moment = created_at or self.now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        item = {"id": str(self._next), "author_id": author, "text": text, "created_at": moment,
                "edit_history_tweet_ids": [str(self._next)]}
        known = {user["username"].casefold(): user["id"] for user in self.users.values()}
        mentions = [{"start": found.start(), "end": found.end(), "username": found.group(1),
                     **({"id": known[found.group(1).casefold()]} if found.group(1).casefold() in known else {})}
                    for found in re.finditer(r"@(\w{1,15})", text)]
        if mentions:
            item["entities"] = {"mentions": mentions}
        if references:
            item["referenced_tweets"] = references
        self.posts.append(item)
        return item

    def posted_by(self, user_id: str) -> list[dict[str, Any]]:
        return [item for item in self.posts if item["author_id"] == user_id]

    def _summons(self, target: str, user_id: str) -> bool:
        """Whether the post ``target`` lets ``user_id`` reply to or quote it (X's reply rule); an unknown post does."""
        found = next((item for item in self.posts if item["id"] == target), None)
        if found is None or self.enterprise or found["author_id"] == user_id:
            return True
        if any(mention.get("id") == user_id for mention in (found.get("entities") or {}).get("mentions", [])):
            return True
        quoted = {ref["id"] for ref in found.get("referenced_tweets") or [] if ref["type"] == "quoted"}
        return any(item["id"] in quoted and item["author_id"] == user_id for item in self.posts)

    # ── The API ─────────────────────────────────────────────────────────────

    def _user(self, request: httpx.Request) -> dict[str, str] | None:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        return self.users.get(token)

    def _lost(self) -> None:
        kind, self.after_send = self.after_send, ""
        if kind == "timeout":
            raise httpx.ReadTimeout("timed out after sending")
        if kind == "disconnect":
            raise httpx.RemoteProtocolError("server disconnected without sending a response")

    def _unreached(self) -> None:
        kind, self.before_send = self.before_send, ""
        if kind == "connect":
            raise httpx.ConnectError("connection refused")

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?", 1)[0]
        path = url.removeprefix(API)
        self.requests.append((request.method, path if url.startswith(API) else url))
        if url == f"{API}/2/oauth2/token":
            return self._token(request)
        if url == LEGACY_UPLOAD:  # v1.1 takes OAuth 1.0a, never an OAuth 2.0 sign-in.
            return httpx.Response(403, json={"errors": [{"code": 220, "message": "Your credentials do not allow "
                                                                                 "access to this resource."}]})
        user = self._user(request)
        if user is None:
            return httpx.Response(401, json={"title": "Unauthorized", "detail": "Unauthorized"})
        parts = path.strip("/").split("/")  # ["2", ...]
        if parts[1:3] == ["media", "upload"]:
            return self._upload(request, parts[3:])
        if request.method == "GET" and parts[1:] == ["users", "me"]:
            return httpx.Response(200, json={"data": dict(user)})
        if request.method == "POST" and parts[1:] == ["tweets"]:
            self._unreached()
            body = json.loads(request.content or b"{}")
            target = (body.get("reply") or {}).get("in_reply_to_tweet_id") or body.get("quote_tweet_id")
            if target and not self._summons(target, user["id"]):
                return httpx.Response(403, json={"title": "Forbidden", "detail": REPLY_RULE,
                                                 "type": "about:blank", "status": 403})
            references = []
            if "reply" in body:
                references.append({"type": "replied_to", "id": body["reply"]["in_reply_to_tweet_id"]})
            if "quote_tweet_id" in body:
                references.append({"type": "quoted", "id": body["quote_tweet_id"]})
            text = body.get("text", "")
            for media_id in (body.get("media") or {}).get("media_ids", []):
                if media_id not in self.uploaded:
                    return httpx.Response(400, json={"title": "Invalid Request", "detail": "unknown media"})
                text += f" https://t.co/m{media_id[-4:]}"  # X adds the media's link to the text it returns.
            item = self.post(user["id"], text.replace("&", "&amp;"), references=references)
            item["media_ids"] = list((body.get("media") or {}).get("media_ids", []))
            self._lost()
            return httpx.Response(201, json={"data": {"id": item["id"], "text": item["text"],
                                                      "edit_history_tweet_ids": [item["id"]]}})
        if parts[1] == "tweets" and len(parts) == 3:
            found = next((item for item in self.posts if item["id"] == parts[2]), None)
            if request.method == "DELETE":
                self._unreached()
                if found is None or found["author_id"] != user["id"]:
                    return httpx.Response(403, json={"title": "Forbidden", "detail": "not yours"})
                self.posts.remove(found)
                self._lost()
                return httpx.Response(200, json={"data": {"deleted": True}})
            if self.fail_reads:
                return httpx.Response(503, json={"title": "Service Unavailable"})
            if found is None:
                return httpx.Response(200, json={"errors": [{"title": "Not Found Error", "value": parts[2]}]})
            shown = ("id", "text", "author_id", "created_at", "entities", "referenced_tweets")
            data: dict[str, Any] = {"data": {key: found[key] for key in shown if key in found}}
            if "referenced_tweets.id" in request.url.params.get("expansions", ""):
                ids = {ref["id"] for ref in found.get("referenced_tweets") or []}
                data["includes"] = {"tweets": [{key: item[key] for key in ("id", "text", "author_id")}
                                               for item in self.posts if item["id"] in ids]}
            return httpx.Response(200, json=data)
        if request.method == "GET" and parts[1] == "users" and len(parts) == 4 and parts[3] in {"tweets", "mentions"}:
            if self.fail_reads:
                return httpx.Response(503, json={"title": "Service Unavailable"})
            mine = [item for item in reversed(self.posts) if item["author_id"] == parts[2]]
            shown = [{key: value for key, value in item.items() if key != "media_ids"} for item in mine]
            return httpx.Response(200, json={"data": shown[:int(request.url.params.get("max_results", "10"))],
                                             "meta": {"result_count": len(shown)}} if shown else {"meta": {}})
        if parts[1] == "users" and len(parts) >= 4 and parts[3] in {"likes", "retweets", "bookmarks"}:
            if parts[2] != user["id"]:
                return httpx.Response(403, json={"title": "Forbidden", "detail": "not this user"})
            return httpx.Response(200, json={"data": {"done": True}})
        return httpx.Response(404, json={"title": "Not Found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") == "authorization_code":
            fresh = self.codes.pop(form.get("code", ""), None)
        else:
            fresh = self.refreshes.pop(form.get("refresh_token", ""), None) if form.get(
                "grant_type") == "refresh_token" else None
        if fresh is None:
            return httpx.Response(400, json={"error": "invalid_request"})
        return httpx.Response(200, json=fresh)

    def _upload(self, request: httpx.Request, rest: list[str]) -> httpx.Response:
        """v2's chunked upload: initialize (JSON), append (multipart), finalize, and STATUS for a video."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if "media.write" not in self.scopes.get(token, "").split():
            return httpx.Response(403, json={"title": "Forbidden", "detail": "Missing required scope: media.write",
                                             "type": "https://api.twitter.com/2/problems/client-forbidden"})
        if request.method == "POST" and rest == ["initialize"]:
            body = json.loads(request.content or b"{}")
            if not body.get("media_type") or not body.get("total_bytes"):
                return httpx.Response(400, json={"title": "Invalid Request", "detail": "media_type, total_bytes"})
            self._next += 1
            media_id = str(self._next)
            self.uploaded[media_id] = 0
            self.categories[media_id] = str(body.get("media_category") or "")
            return httpx.Response(200, json={"data": {"id": media_id, "media_key": f"3_{media_id}"}})
        if request.method == "POST" and len(rest) == 2 and rest[0] in self.uploaded and rest[1] == "append":
            if b'name="segment_index"' not in request.content or b'name="media"' not in request.content:
                return httpx.Response(400, json={"title": "Invalid Request", "detail": "segment_index, media"})
            self.uploaded[rest[0]] += len(request.content)
            return httpx.Response(200, json={"data": {"expires_at": 1_900_000_000}})
        if request.method == "POST" and len(rest) == 2 and rest[0] in self.uploaded and rest[1] == "finalize":
            video = self.categories.get(rest[0]) == "tweet_video"
            info = {"state": "pending", "check_after_secs": 0} if video else None
            return httpx.Response(200, json={"data": {"id": rest[0], **({"processing_info": info} if info else {})}})
        if request.method == "GET" and rest == [] and request.url.params.get("command") == "STATUS":
            return httpx.Response(200, json={"data": {"id": request.url.params.get("media_id"),
                                                      "processing_info": {"state": "succeeded"}}})
        return httpx.Response(400, json={"title": "Invalid Request", "detail": "not an upload step"})
