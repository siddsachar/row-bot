"""Posting to X as a named account, approved by the person, and sent once (N7).

A post, reply, quote or delete goes out only as the account the call names (``x_accounts``). Its
approval card says exactly what will happen: "Post to X as @rowbot_ai?", the full text, the post it
replies to or quotes as a link, and each photo or video by name, size and a short SHA-256. The
approval is bound to the account, the action, the text, the target and the media's SHA-256s: any
change is another question, asked again. Under Auto only the person approves these (a person-only
risk, like Git identity); Hands-off is the person's own choice.

Before sending, an operation is recorded (its ID, account, payload hash and time). When X doesn't
confirm (a timeout or a dropped connection after sending), the outcome is not known: Row-Bot reads
the account's latest posts and reports the post it finds, or that it didn't post. It never sends
again by itself; an operation still unsettled is checked the same way before the same payload goes
out again.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import logging
import mimetypes
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from row_bot import x_accounts
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.x_accounts import XAccount

logger = logging.getLogger(__name__)

ACTIONS = ("post", "reply", "quote", "delete")
# X API v2's chunked upload (initialize, append, finalize): it takes the OAuth 2.0 sign-in with media.write. The
# v1.1 upload.twitter.com endpoint refuses OAuth 2.0 sign-ins, so every picture post failed there (29 Oct 2026).
MEDIA_UPLOAD_URL = f"{x_accounts.API_BASE}/media/upload"
_PROCESSING_WAIT = 600.0  # How long a video may take X to process (a long one takes minutes) before the post gives up.
_CHUNK = 5 * 1024 * 1024
_MAX_IMAGES = 4
_MAX_TEXT = 25_000  # X's longest post (with a subscription).
_THUMBNAIL_PIXELS = 160
_THUMBNAIL_BYTES = 24 * 1024
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
_SINGLE_TYPES = {"image/gif", "video/mp4", "video/quicktime"}
_RECENT = 20  # How many of the account's latest posts a check reads.
_MAX_OPERATIONS = 200
_OPS_LOCK = threading.Lock()
_SENDING = threading.Lock()  # One send at a time, so an operation still "sending" was cut off, never in flight.


class XPostError(ValueError):
    """Something stops the post before anything is sent; the message says what, in plain words."""


@dataclass(frozen=True)
class Media:
    path: Path
    name: str
    size: int
    sha256: str
    mime: str
    kind: str  # image, gif or video
    thumbnail: str = ""  # A small JPEG as a data URL, for the card; "" for a video.
    source: str = ""  # What finds it again at its time slot: its path, or its Library › Media reference.
    silent: bool = False  # A video with nothing to hear: the card says so before the person approves it.

    @property
    def short(self) -> str:
        return self.sha256[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _size(value: int) -> str:
    for unit, step in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if value >= step:
            return f"{value / step:.1f} {unit}".replace(".0 ", " ")
    return f"{value} bytes"


# ── What is sent ─────────────────────────────────────────────────────────────


def _thumbnail(path: Path) -> str:
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            if source.width * source.height > 64_000_000:
                return ""
            source.draft(None, (_THUMBNAIL_PIXELS * 2, _THUMBNAIL_PIXELS * 2))
            image = ImageOps.exif_transpose(source)
            image.thumbnail((_THUMBNAIL_PIXELS, _THUMBNAIL_PIXELS))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=70)
    except Exception:
        return ""
    data = output.getvalue()
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}" if len(data) <= _THUMBNAIL_BYTES else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _find(value: str, conversation_id: str) -> tuple[Path, str, str, str]:
    """A file to attach: its path, name, MIME type and what finds it again. It is a file on this computer, a
    Library › Media item by its reference (media_find), or a file in this chat by its name (the newest)."""
    from row_bot import media_library, media_sources

    path = Path(value).expanduser()
    if path.is_file():
        return path, path.name, mimetypes.guess_type(path.name)[0] or "", str(path)
    ref = value if media_library.REF.fullmatch(value) else ""
    if not ref and conversation_id and not ("/" in value or "\\" in value):
        found = media_sources.chat_file(conversation_id, value)
        ref = found.ref if found is not None else ""
    if ref:
        try:
            facts, stored = media_library.locate(ref)
        except media_library.MediaError:
            raise XPostError(f"{value} isn't in Library › Media any more, so nothing was posted.") from None
        return stored, str(facts["name"]), str(facts["mime_type"]), ref
    raise XPostError(f"{path.name or value} isn't a file Row-Bot can read, a file in this chat or a Library › Media "
                     "item (media_find gives its reference), so nothing was posted.")


def _library_ref(source: str) -> str:
    """A file's Library › Media reference, which the card opens to check it before it goes; "" for a file on this
    computer (its path never reaches the card)."""
    from row_bot import media_library

    return source if media_library.REF.fullmatch(source) else ""


def _silent(path: Path) -> bool:
    """A video with nothing to hear (no sound track, or only silence): X plays it silent too."""
    from row_bot.sound import has_sound

    return has_sound(path) is False


def media_facts(paths: list[str] | None, conversation_id: str = "") -> list[Media]:
    """Each file to attach: its name, size, SHA-256 and kind (with a thumbnail for a picture)."""
    found: list[Media] = []
    for raw in paths or []:
        path, name, mime, source = _find(str(raw).strip(), conversation_id)
        if mime not in _IMAGE_TYPES | _SINGLE_TYPES:
            raise XPostError(f"X takes JPEG, PNG, WebP or GIF pictures and MP4 or MOV videos; {name} isn't "
                             "one, so nothing was posted.")
        kind = "video" if mime.startswith("video/") else "gif" if mime == "image/gif" else "image"
        found.append(Media(path, name[:256], path.stat().st_size, _sha256(path), mime, kind,
                           _thumbnail(path) if kind != "video" else "", source,
                           silent=kind == "video" and _silent(path)))
    singles = [item for item in found if item.kind != "image"]
    if len(found) > _MAX_IMAGES or (singles and len(found) > 1):
        raise XPostError("A post takes up to four pictures, or one video or GIF, so nothing was posted.")
    return found


def payload_hash(account: XAccount, action: str, text: str, target: str, media: list[Media]) -> str:
    """What the approval is bound to: the account, action, text, target and each file's SHA-256."""
    from row_bot.tools.approval_gate import call_binding

    return call_binding("x_post", {"account": account.id, "action": action, "text": text, "target": target},
                        [item.sha256 for item in media])


def target_url(target: str) -> str:
    return f"https://x.com/i/status/{target}" if target else ""


# ── X's reply rule ───────────────────────────────────────────────────────────
# Since 23 Feb 2026 X's self-serve API refuses (403) a reply or quote unless the post's author summoned the account:
# mentioned it, quoted one of its posts, or is the account. Row-Bot checks first, so such a reply never reaches X;
# the person gets the draft and the link, to post it in X's own app or website.


def summoned(account: XAccount, target: str) -> bool | None:
    """Whether X's API lets ``account`` reply to or quote the post ``target`` (by the account's own user ID); None
    when X doesn't say."""
    token = x_accounts.valid_token(account)
    if token is None:
        return None
    try:
        response = x_accounts.send("GET", f"{x_accounts.API_BASE}/tweets/{target}", headers=x_accounts.bearer(token),
                                   params={"tweet.fields": "author_id,entities,referenced_tweets",
                                           "expansions": "referenced_tweets.id"}, timeout=10)
        found = response.json() if response.status_code == 200 else {}
        post = found.get("data")
        if not isinstance(post, dict):
            return None
        mentions = (post.get("entities") or {}).get("mentions") or []
        quoted = {str(item.get("id")) for item in post.get("referenced_tweets") or [] if item.get("type") == "quoted"}
        included = (found.get("includes") or {}).get("tweets") or []
        return (str(post.get("author_id")) == account.id
                or any(str(item.get("id")) == account.id for item in mentions)
                or any(str(item.get("id")) in quoted and str(item.get("author_id")) == account.id
                       for item in included))
    except Exception as exc:
        logger.warning("Reading the post %s replies to failed (%s)", account.handle, type(exc).__name__)
        return None


def not_allowed(account: XAccount, action: str, *, refused: bool = False) -> str:
    """Why a reply or quote can't go out through X's API, in plain words."""
    lead = f"Nothing was posted as {account.handle}. X refused it" if refused else "Nothing was sent to X"
    return (f"{lead}: X allows this {'reply' if action == 'reply' else 'quote'} only from its own app or website. "
            f"Through other apps, X allows a reply or quote only when the post's author mentioned {account.handle} "
            f"or quoted one of {account.handle}'s posts.")


def by_hand(account: XAccount, action: str, text: str, target: str, *, refused: bool = False) -> str:
    """``not_allowed`` with the post's link and the draft, ready to copy."""
    return (f"{not_allowed(account, action, refused=refused)}\n{'Reply to' if action == 'reply' else 'Quote'} it "
            f"there: {target_url(target)}\nThe draft, ready to copy:\n{text}")


def _reply_rule(response: Any) -> bool:
    """Whether X refused a reply or quote by its reply rule (the target's author didn't summon the account)."""
    return response.status_code == 403 and "mentioned" in _detail(response).casefold()


def question(account: XAccount, action: str) -> str:
    return {"post": f"Post to X as {account.handle}?", "reply": f"Reply on X as {account.handle}?",
            "quote": f"Quote a post on X as {account.handle}?",
            "delete": f"Delete a post from {account.handle}?"}[action]


# A post may wait for a time slot (5.3.1 G3): approved now, sent by the scheduler at its time.
SLOT_AHEAD = timedelta(days=7)  # How far ahead a post may be scheduled.
SLOT_LATE = timedelta(hours=12)  # A post Row-Bot couldn't send by then (it was closed) is never sent late.
# A local time ("16:00") this recently passed is today's, not tomorrow's: its approval came late (R8).
SLOT_JUST_PASSED = timedelta(hours=1)


def slot(value: str | None, now: datetime | None = None, *, late_ok: bool = False) -> datetime | None:
    """When an approved post goes out: None for at once; a local time ("13:00", the next one) or date and time.

    *late_ok*: a time that passed while its approval waited (up to ``SLOT_LATE`` ago; a bare local time up to
    ``SLOT_JUST_PASSED``) is None, at once: the person approved it late, and a new card asks for "now" (R8).
    """
    text = str(value or "").strip()
    if not text:
        return None
    moment = (now or datetime.now()).replace(second=0, microsecond=0)
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", text):
            hour, minute = (int(part) for part in text.split(":"))
            when = moment.replace(hour=hour, minute=minute)
            if when <= moment and not (late_ok and moment - when <= SLOT_JUST_PASSED):
                when += timedelta(days=1)
        else:
            when = datetime.fromisoformat(text)
            if when.tzinfo is not None:
                when = when.astimezone().replace(tzinfo=None)
            when = when.replace(second=0, microsecond=0)
    except ValueError:
        raise XPostError(f"post_at is a local time like 13:00 or 2026-10-29T09:30, not {text!r}, so nothing was "
                         "posted.") from None
    if when <= moment and late_ok and moment - when <= SLOT_LATE:
        return None
    if when <= moment:
        raise XPostError(f"{when:%Y-%m-%d %H:%M} has passed, so nothing was posted. Leave post_at out to post now.")
    if when - moment > SLOT_AHEAD:
        raise XPostError("A post can be scheduled up to 7 days ahead, so nothing was posted.")
    return when


def card(account: XAccount, action: str, text: str, target: str, media: list[Media], *,
         target_text: str = "", post_at: datetime | None = None) -> dict[str, Any]:
    """What the person's card shows; never a path or a token."""
    return {
        "account_id": account.id, "username": account.username, "name": account.name, "action": action,
        "text": text, "target_id": target,
        "target_url": account.post_url(target) if action == "delete" and target else target_url(target),
        "target_text": target_text[:_MAX_TEXT],
        "media": [{"name": item.name, "size": item.size, "sha256": item.short, "kind": item.kind,
                   "thumbnail": item.thumbnail, "silent": item.silent, "ref": _library_ref(item.source),
                   "mime": item.mime}
                  for item in media],
        "limit": _LIMITS.get(account.id, 0),  # How long a post the account may send, when X said (0: unknown).
        "post_at": post_at.isoformat(timespec="minutes") if post_at else "",
    }


def _words(account: XAccount, action: str, text: str, target: str, media: list[Media], target_text: str) -> str:
    """The action in one paragraph, for the conversation's activity and a workflow's approval."""
    who = f"{account.handle} ({account.name})" if account.name else account.handle
    lead = {"post": f"Post to X as {who}", "reply": f"Reply as {who} to {target_url(target)}",
            "quote": f"Quote {target_url(target)} as {who}",
            "delete": f"Delete post {target} from {who}"}[action]
    quoted = text if action != "delete" else target_text
    sentence = f"{lead}: “{quoted[:600]}{'…' if len(quoted) > 600 else ''}”" if quoted else lead
    if media:
        sentence += " with " + ", ".join(f"{item.name} ({_size(item.size)}, SHA-256 {item.short}"
                                         f"{', no sound' if item.silent else ''})" for item in media)
    return sentence + "."


def approval_payload(account: XAccount, action: str, text: str, target: str, media: list[Media], *,
                     target_text: str = "", post_at: datetime | None = None) -> dict[str, Any]:
    """The approval this post asks for: its card, its words, and the binding (a new question when any part changes,
    its time slot included)."""
    binding = payload_hash(account, action, text, target, media)
    words = _words(account, action, text, target, media, target_text)
    if post_at is not None:
        binding += "@" + post_at.isoformat(timespec="minutes")
        words += f" It goes out at {post_at:%H:%M} on {post_at:%a %d %b}."
    return {"tool": "x_post", "label": question(account, action).rstrip("?"), "description": words,
            "question": question(account, action), "binding": binding,
            "args": {"account": account.handle, "action": action, "text": text, "tweet_id": target,
                     "media": [item.name for item in media],
                     **({"post_at": post_at.isoformat(timespec="minutes")} if post_at else {})},
            "x_post": card(account, action, text, target, media, target_text=target_text, post_at=post_at)}


class PostEdited(Exception):
    """The person edited the post on its card (G2): ask again with this text and the files they kept."""

    def __init__(self, text: str, kept: list[str]) -> None:
        super().__init__("The person edited the post.")
        self.text, self.kept = text, kept


def _edited(answer: Any) -> PostEdited | None:
    edit = answer.get("x_post_edit") if isinstance(answer, dict) else None
    if not isinstance(edit, dict) or not isinstance(edit.get("text"), str):
        return None
    return PostEdited(edit["text"], [str(item) for item in edit.get("media") or []])


def approve(account: XAccount, action: str, text: str, target: str, media: list[Media], *,
            target_text: str = "", post_at: datetime | None = None) -> tuple[str | None, str]:
    """(None, its approval line) when this exact post may go out; else (the refusal, its line).

    Raises PostEdited when the person edited it on the card instead: never an approval of anything.
    """
    from row_bot.tools.approval_gate import X_POST_RISK, current_approval_mode, resolve_approval_line, with_approval

    payload = approval_payload(account, action, text, target, media, target_text=target_text, post_at=post_at)
    answers: list[Any] = []

    def ask(asked: dict[str, Any]) -> Any:
        answers.append(_ask(asked))
        return answers[-1]
    outcome, line = resolve_approval_line(payload, approval_mode=current_approval_mode(), risks=(X_POST_RISK,),
                                          approval_callback=ask)
    edited = _edited(answers[-1]) if answers and outcome == "deny" else None
    if edited is not None:
        raise edited
    if outcome == "allow":
        return None, line
    if outcome == "block":
        return "BLOCKED: posting on X is unavailable while this chat's approval level is Block.", ""
    return with_approval(line, "Nothing was sent to X: the person didn't approve it. Don't post it another way."), line


def _ask(payload: dict[str, Any]) -> Any:
    """The person's answer (the conversation pauses on the card); a test stands in here."""
    from langgraph.types import interrupt

    return interrupt(payload)


# ── Scheduled posts (G3) ─────────────────────────────────────────────────────
# An approved post with a time slot waits here, with the files it was approved with (by path and SHA-256),
# until the scheduler's minute loop sends it at its time. A file changed since the approval, a sign-in
# removed or a time missed by more than SLOT_LATE sends nothing and says so.

_SCHEDULE_LOCK = threading.Lock()
_IN_FLIGHT: set[str] = set()  # Scheduled posts being sent now.


def _schedule_path() -> Path:
    return get_row_bot_data_dir() / "x" / "scheduled.json"


def _scheduled() -> list[dict[str, Any]]:
    try:
        value = json.loads(_schedule_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _save_scheduled(items: list[dict[str, Any]]) -> None:
    path = _schedule_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    # Settled posts are kept a week, for the record.
    keep = [item for item in items if item.get("status") == "scheduled"
            or str(item.get("settled_at") or "") >= _stamp(_now() - timedelta(days=7))]
    temporary.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def schedule(account: XAccount, action: str, text: str, target: str, media: list[Media],
             post_at: datetime) -> dict[str, Any]:
    """Keep an approved post for its time slot; the scheduler sends it then."""
    item = {"id": f"xs-{uuid.uuid4().hex[:12]}", "account_id": account.id, "action": action, "text": text,
            "target": target, "post_at": post_at.isoformat(timespec="minutes"),
            "media": [{"path": entry.source or str(entry.path), "sha256": entry.sha256, "name": entry.name}
                      for entry in media],
            "created_at": _stamp(_now()), "status": "scheduled"}
    with _SCHEDULE_LOCK:
        _save_scheduled([*_scheduled(), item])
    return item


# A scheduled post that didn't go out stays in Home's Review with why, until the person tries it again or dismisses it.
# A reply or quote X's reply rule stops (by_hand) is never tried again: it stays with its draft and link to post by hand.
_RETRIABLE = ("failed", "missed", "unknown")
_NOT_SENT = (*_RETRIABLE, "by_hand")


def scheduled_posts() -> list[dict[str, Any]]:
    """Home's Review: the posts that didn't go out (with why), then those waiting for their time, soonest first
    (never a path or a token)."""
    accounts = {account.id: account for account in x_accounts.listed()}
    shown = []
    with _SCHEDULE_LOCK:
        items = _scheduled()
    waiting = [*sorted((item for item in items if item.get("status") in _NOT_SENT),
                       key=lambda item: str(item.get("settled_at")), reverse=True),
               *sorted((item for item in items if item.get("status") == "scheduled"),
                       key=lambda item: str(item.get("post_at")))]
    for item in waiting:
        account = accounts.get(str(item.get("account_id")))
        files = []
        for entry in item.get("media") or []:
            path = None
            try:
                path, name, mime, _ = _find(str(entry.get("path") or ""), "")
                size = path.stat().st_size
            except (XPostError, OSError):
                name = str(entry.get("name") or Path(str(entry.get("path") or "")).name)
                size, mime = 0, mimetypes.guess_type(name)[0] or ""
            kind = "video" if mime.startswith("video/") else "gif" if mime == "image/gif" else "image"
            files.append({"name": name[:256], "size": size, "sha256": str(entry.get("sha256") or "")[:12],
                          "kind": kind, "silent": kind == "video" and path is not None and _silent(path),
                          "ref": _library_ref(str(entry.get("path") or "")) if path is not None else "",
                          "mime": mime})
        shown.append({"id": item["id"], "account_id": str(item.get("account_id") or ""),
                      "username": account.username if account else "", "name": account.name[:80] if account else "",
                      # The editor's count: the limit X already said for the account, never a call to X here.
                      "limit": (_LIMITS.get(account.id) or 0) if account else 0,
                      "action": str(item.get("action") or "post"),
                      "text": str(item.get("text") or ""), "target_url": target_url(str(item.get("target") or "")),
                      "post_at": str(item.get("post_at") or ""), "media": files,
                      "status": str(item.get("status")), "reason": str(item.get("result") or "")[:2000]
                      if item.get("status") in _NOT_SENT else ""})
    return shown


def cancel_scheduled(item_id: str) -> bool:
    """Cancel a post still waiting for its time, or dismiss one that didn't go out; False when there is none."""
    with _SCHEDULE_LOCK:
        items = _scheduled()
        for item in items:
            if item.get("id") == item_id and item.get("status") in ("scheduled", *_NOT_SENT):
                item.update(status="cancelled" if item.get("status") == "scheduled" else "dismissed",
                            settled_at=_stamp(_now()))
                _save_scheduled(items)
                return True
    return False


def edit_scheduled(item_id: str, text: str, kept: list[str]) -> bool:
    """The person's edit of a post waiting for its time (Home › Review): its text, and which of its files still go
    (by their SHA-256 start). False when no post waits under that id.

    The person approved this post; the edit is their own, so it keeps its time and goes out as edited. Raises
    XPostError when the edit can't go out: no text, or longer than the account may post.
    """
    with _SCHEDULE_LOCK:
        item = next((entry for entry in _scheduled() if entry.get("id") == item_id
                     and entry.get("status") == "scheduled"), None)
    if item is None:
        return False
    account = next((entry for entry in x_accounts.listed() if entry.id == str(item.get("account_id"))), None)
    if account is None:
        return False
    action = str(item.get("action") or "post")
    words, _ = check_text(action, text, str(item.get("target") or "") or None)
    check_length(account, action, words)  # May ask X the account's limit: outside the lock.
    with _SCHEDULE_LOCK:
        items = _scheduled()
        for entry in items:
            if entry.get("id") == item_id and entry.get("status") == "scheduled":
                entry.update(text=words, media=[file for file in entry.get("media") or []
                                                if str(file.get("sha256") or "")[:12] in kept])
                _save_scheduled(items)
                return True
    return False


def retry_scheduled(item_id: str, *, without_media: bool = False) -> bool:
    """The person's Try again (or Post without the picture) on a post that didn't go out: due now (the next
    ``send_due`` sends it).

    The person approved this post; pressing it is their go-ahead to send it now, as it was or without its media.
    Sending checks X first for an earlier attempt that may have gone out, so nothing is posted twice.
    """
    with _SCHEDULE_LOCK:
        items = _scheduled()
        item = next((entry for entry in items if entry.get("id") == item_id and entry.get("status") in _RETRIABLE),
                    None)
        if item is None:
            return False
        item.update(status="scheduled", post_at=datetime.now().isoformat(timespec="minutes"),  # Local, as slots are.
                    result="", url="", settled_at="",
                    media=[] if without_media else item.get("media") or [])
        _save_scheduled(items)
    return True


def _settle_scheduled(item: dict[str, Any], status: str, result: str, url: str = "") -> None:
    with _SCHEDULE_LOCK:
        items = _scheduled()
        for index, entry in enumerate(items):
            if entry.get("id") == item["id"]:
                items[index] = {**entry, "status": status, "result": result, "url": url,
                                "settled_at": _stamp(_now())}
        _save_scheduled(items)


def send_due(now: datetime | None = None) -> list[dict[str, Any]]:
    """Send each scheduled post whose time has come, once; what each one did."""
    from row_bot.notifications import notify

    if not _schedule_path().exists():
        return []
    moment = now or datetime.now()
    with _SCHEDULE_LOCK:
        # Taken before sending, so a slow send is never picked up twice. One cut off by Row-Bot closing is
        # picked up again, and post_as checks X before sending it a second time.
        due = [item for item in _scheduled() if item.get("status") == "scheduled"
               and item["id"] not in _IN_FLIGHT and datetime.fromisoformat(str(item["post_at"])) <= moment]
        _IN_FLIGHT.update(item["id"] for item in due)
    done = []
    for item in due:
        account = next((entry for entry in x_accounts.listed() if entry.id == item.get("account_id")), None)
        when = datetime.fromisoformat(str(item["post_at"]))
        if account is None:
            status, result, url = "failed", "Its X account was removed, so it wasn't posted.", ""
        elif moment - when > SLOT_LATE:
            status, result, url = "missed", (f"Row-Bot wasn't running at {when:%H:%M} on {when:%a %d %b}, so it "
                                             "wasn't posted. Approve it again to post it."), ""
        elif item["action"] in ("reply", "quote") and summoned(account, str(item.get("target") or "")) is False:
            # Approved before Row-Bot checked X's reply rule: it never goes to X.
            status, result, url = "by_hand", not_allowed(account, str(item["action"])), ""
        else:
            try:
                media = media_facts([entry["path"] for entry in item.get("media") or []])
                if [entry.sha256 for entry in media] != [entry["sha256"] for entry in item.get("media") or []]:
                    raise XPostError("A file changed after you approved the post, so it wasn't posted.")
                sent = post_as(account, str(item["action"]), str(item["text"]), str(item.get("target") or ""),
                               media)
                status = {"posted": "posted", "unknown": "unknown", "by_hand": "by_hand"}.get(sent.status, "failed")
                # Review shows the draft and the link beside the reason.
                result = not_allowed(account, str(item["action"]), refused=True) if status == "by_hand" else sent.text
                url = sent.url
            except XPostError as exc:
                status, result, url = "failed", str(exc), ""
        _settle_scheduled(item, status, result, url)
        _IN_FLIGHT.discard(item["id"])
        done.append({"id": item["id"], "status": status, "result": result, "url": url})
        handle = account.handle if account else "X"
        if status == "posted":
            notify(title=f"Posted on X as {handle}", message=url or result, source="x", requested=True)
        else:
            after = ("It's in Home › Review, with the draft to copy." if status == "by_hand"
                     else "It's in Home › Review, where you can try it again.")
            notify(title=f"A scheduled post as {handle} didn't go out", message=f"{result} {after}",
                   toast_type="warning", source="x")
    return done


# ── The operation record ─────────────────────────────────────────────────────


def _operations_path() -> Path:
    return get_row_bot_data_dir() / "x" / "operations.json"


def _operations() -> list[dict[str, Any]]:
    try:
        value = json.loads(_operations_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _save_operations(items: list[dict[str, Any]]) -> None:
    path = _operations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(items[-_MAX_OPERATIONS:], indent=1), encoding="utf-8")
    os.replace(temporary, path)


def operations() -> list[dict[str, Any]]:
    """The recorded operations, oldest first (what was sent from which account, and how it ended)."""
    with _OPS_LOCK:
        return _operations()


def _begin(account: XAccount, action: str, digest: str, text: str, target: str) -> dict[str, Any]:
    operation = {"id": str(uuid.uuid4()), "account_id": account.id, "action": action, "payload_hash": digest,
                 "text": text, "target": target, "started_at": _stamp(_now()), "status": "sending"}
    with _OPS_LOCK:
        _save_operations([*_operations(), operation])
    return operation


def _settle(operation: dict[str, Any], status: str, **facts: str) -> None:
    operation.update(status=status, settled_at=_stamp(_now()), **facts)
    with _OPS_LOCK:
        items = _operations()
        for index, item in enumerate(items):
            if item.get("id") == operation["id"]:
                items[index] = operation
        _save_operations(items)


def _unsettled(account: XAccount, digest: str) -> dict[str, Any] | None:
    with _OPS_LOCK:
        return next((item for item in reversed(_operations()) if item.get("account_id") == account.id
                     and item.get("payload_hash") == digest and item.get("status") in {"sending", "unknown"}), None)


# ── Sending ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    status: str  # posted, failed, unknown or by_hand (X's reply rule refused it)
    post_id: str = ""
    at: str = ""
    detail: str = ""


def _definite(exc: Exception) -> bool:
    """Whether a failed request certainly never reached X (so nothing can have been posted)."""
    import httpx

    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol))


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return str(body.get("detail") or body.get("title") or "").strip()[:300] if isinstance(body, dict) else ""


def _refusal(response: Any, account: XAccount) -> str:
    if response.status_code == 401:
        return f"X turned down the sign-in of {account.handle}. The person can reconnect it in Settings › Apps › X."
    if response.status_code == 429:
        return "X is limiting requests right now. Try again later."
    detail = _detail(response)
    return f"X refused it ({response.status_code}){': ' + detail if detail else ''}."


def _category(mime: str) -> str:
    return "tweet_video" if mime.startswith("video/") else "tweet_gif" if mime == "image/gif" else "tweet_image"


def _upload(account: XAccount, media: list[Media], token: dict) -> list[str]:
    """Upload each file in chunks, checking its bytes are the ones approved; raises XPostError (nothing posted)."""
    ids: list[str] = []
    headers = x_accounts.bearer(token)
    for item in media:
        def fail(step: str, response: Any = None) -> XPostError:
            reason = f" {_refusal(response, account)}" if response is not None else ""
            return XPostError(f"Uploading {item.name} to X failed ({step}), so nothing was posted.{reason} "
                              "Uploaded media may stay on X unused.")
        try:
            started = x_accounts.send("POST", f"{MEDIA_UPLOAD_URL}/initialize", headers=headers, timeout=30,
                                      json={"media_type": item.mime, "total_bytes": item.size,
                                            "media_category": _category(item.mime)})
            if started.status_code not in (200, 201, 202):
                raise fail("start", started)
            media_id = str((started.json().get("data") or {}).get("id") or "")
            if not media_id:
                raise fail("start")
            digest, sent = hashlib.sha256(), 0
            with item.path.open("rb") as handle:
                segment = 0
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
                    sent += len(chunk)
                    appended = x_accounts.send("POST", f"{MEDIA_UPLOAD_URL}/{media_id}/append", headers=headers,
                                               timeout=60, data={"segment_index": str(segment)},
                                               files={"media": chunk})
                    if appended.status_code not in (200, 202, 204):
                        raise fail("sending", appended)
                    segment += 1
            if digest.hexdigest() != item.sha256 or sent != item.size:
                raise XPostError(f"{item.name} changed after it was approved, so nothing was posted.")
            finished = x_accounts.send("POST", f"{MEDIA_UPLOAD_URL}/{media_id}/finalize", headers=headers,
                                       timeout=30)
            if finished.status_code not in (200, 201):
                raise fail("finishing", finished)
            _processed(media_id, (finished.json().get("data") or {}).get("processing_info"), headers, fail)
        except XPostError:
            raise
        except (OSError, ValueError) as exc:
            logger.warning("Uploading media to X failed (%s)", type(exc).__name__)
            raise fail("reading the file") from None
        except Exception as exc:
            logger.warning("Uploading media to X failed (%s)", type(exc).__name__)
            raise fail("connection") from None
        ids.append(media_id)
    return ids


def _processed(media_id: str, info: Any, headers: dict, fail: Any) -> None:
    """Wait while X processes a video or GIF (a picture is ready at once); raises when X can't use it."""
    deadline = time.monotonic() + _PROCESSING_WAIT
    while isinstance(info, dict) and info.get("state") in {"pending", "in_progress"}:
        if time.monotonic() >= deadline:
            raise fail("processing")
        time.sleep(min(max(float(info.get("check_after_secs") or 1), 0.0), 10.0))
        checked = x_accounts.send("GET", MEDIA_UPLOAD_URL, headers=headers, timeout=30,
                                  params={"command": "STATUS", "media_id": media_id})
        if checked.status_code != 200:
            raise fail("processing", checked)
        info = (checked.json().get("data") or {}).get("processing_info")
    if isinstance(info, dict) and info.get("state") == "failed":
        raise fail("processing")


def _create(account: XAccount, token: dict, body: dict[str, Any]) -> Outcome:
    try:
        response = x_accounts.send("POST", f"{x_accounts.API_BASE}/tweets", headers=x_accounts.bearer(token),
                                   json=body)
    except Exception as exc:
        logger.warning("Posting to X as %s got no answer (%s)", account.handle, type(exc).__name__)
        if _definite(exc):
            return Outcome("failed", detail="Row-Bot couldn't reach X.")
        return Outcome("unknown")
    if response.status_code >= 500:
        return Outcome("unknown")
    if _reply_rule(response):
        return Outcome("by_hand")
    if response.status_code not in (200, 201):
        return Outcome("failed", detail=_refusal(response, account))
    try:
        post_id = str((response.json().get("data") or {}).get("id") or "")
    except ValueError:
        post_id = ""
    return Outcome("posted", post_id, _stamp(_now())) if post_id.isdigit() else Outcome("unknown")


def _comparable(text: str) -> str:
    """A post's words as X may give them back: links shortened or added (media), entities escaped."""
    return " ".join(re.sub(r"https?://\S+", " ", html.unescape(text or "")).split()).casefold()


def _reconcile(account: XAccount, token: dict, operation: dict[str, Any]) -> Outcome:
    """Look among the account's latest posts for the one this operation sent (no resend)."""
    action, target = operation.get("action"), str(operation.get("target") or "")
    try:
        if action == "delete":
            response = x_accounts.send("GET", f"{x_accounts.API_BASE}/tweets/{target}",
                                       headers=x_accounts.bearer(token))
            if response.status_code == 404:
                return Outcome("posted", target, _stamp(_now()))
            data = response.json() if response.status_code == 200 else None
            if data is None:
                return Outcome("unknown")
            return Outcome("posted", target, _stamp(_now())) if not data.get("data") else Outcome("failed")
        response = x_accounts.send("GET", f"{x_accounts.API_BASE}/users/{account.id}/tweets",
                                   headers=x_accounts.bearer(token),
                                   params={"max_results": _RECENT, "tweet.fields": "created_at,referenced_tweets"})
        if response.status_code != 200:
            return Outcome("unknown")
        posts = response.json().get("data") or []
    except Exception as exc:
        logger.warning("Reading the latest posts of %s failed (%s)", account.handle, type(exc).__name__)
        return Outcome("unknown")
    try:
        since = datetime.fromisoformat(str(operation.get("started_at")).replace("Z", "+00:00")) - timedelta(minutes=5)
    except ValueError:
        since = _now() - timedelta(days=1)
    wanted = _comparable(str(operation.get("text") or ""))
    kind = {"reply": "replied_to", "quote": "quoted"}.get(str(action))
    for post in posts if isinstance(posts, list) else []:
        if not isinstance(post, dict) or _comparable(str(post.get("text") or "")) != wanted:
            continue
        try:
            created = datetime.fromisoformat(str(post.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        references = {(str(item.get("type")), str(item.get("id"))) for item in post.get("referenced_tweets") or []
                      if isinstance(item, dict)}
        if created >= since and (kind is None or (kind, target) in references):
            return Outcome("posted", str(post.get("id") or ""), _stamp(created))
    return Outcome("failed")


def _posted(account: XAccount, action: str, target: str, outcome: Outcome) -> str:
    url = account.post_url(outcome.post_id)
    lead = {"post": f"Posted as {account.handle}: {url}",
            "reply": f"Replied as {account.handle} to {target_url(target)}: {url}",
            "quote": f"Quoted {target_url(target)} as {account.handle}: {url}",
            "delete": f"Deleted as {account.handle}: post {outcome.post_id} ({url})"}[action]
    return f"{lead}\nPost ID: {outcome.post_id} · Account: {account.handle} (ID {account.id}) · Time: {outcome.at}"


_NOT_SURE = "Not sure it went through: X didn't confirm it."


def _settled_text(account: XAccount, action: str, target: str, outcome: Outcome, *, checked: bool) -> str:
    deleting = action == "delete"
    if outcome.status == "posted":
        found = ("" if not checked else f"{_NOT_SURE} Row-Bot looked on X and the post is gone.\n" if deleting
                 else f"{_NOT_SURE} Row-Bot read {account.handle}'s latest posts and found it.\n")
        return found + _posted(account, action, target, outcome)
    if outcome.status == "failed" and checked:
        return ((f"{_NOT_SURE} Row-Bot looked on X and the post is still there: it wasn't deleted." if deleting
                 else f"{_NOT_SURE} Row-Bot read {account.handle}'s latest posts and it isn't there: it didn't post.")
                + " Nothing was sent again; trying again needs a new approval.")
    if outcome.status == "failed":
        return f"Nothing was {'deleted' if action == 'delete' else 'posted'} as {account.handle}. {outcome.detail}".strip()
    return (f"{_NOT_SURE} Row-Bot couldn't read {account.handle}'s latest posts to check, so it may or may not "
            f"have gone out. Look at {account.handle} on X before trying again; Row-Bot won't send it again by "
            "itself.")


@dataclass(frozen=True)
class Sent:
    """How one send ended: ``posted`` (deleted, for a delete), ``failed``, ``unknown`` or ``by_hand`` (X's reply rule
    refused it: trying again fails again), and in words."""
    status: str
    text: str
    url: str = ""


def post_as(account: XAccount, action: str, text: str, target: str, media: list[Media]) -> Sent:
    """Send one approved post, reply, quote or delete as ``account``, at most once."""
    if action not in ACTIONS:
        raise XPostError(f"Unknown action {action}. Use post, reply, quote or delete.")
    with _SENDING:
        return _send_once(account, action, text, target, media)


def _sent(account: XAccount, action: str, target: str, outcome: Outcome, *, checked: bool, lead: str = "") -> Sent:
    url = account.post_url(outcome.post_id) if outcome.status == "posted" else ""
    return Sent(outcome.status, lead + _settled_text(account, action, target, outcome, checked=checked), url)


def _send_once(account: XAccount, action: str, text: str, target: str, media: list[Media]) -> Sent:
    token = x_accounts.valid_token(account)
    if token is None:
        return Sent("failed", f"Nothing was sent: the sign-in of {account.handle} has run out or was removed. The "
                              "person can reconnect it in Settings › Apps › X.")
    if media and not x_accounts.can_post_media(account):
        return Sent("failed", f"Nothing was sent: {x_accounts.media_reconnect(account)}")
    digest = payload_hash(account, action, text, target, media)
    earlier = _unsettled(account, digest)
    if earlier is not None:
        # The same payload went out before and X never confirmed it: check before sending it again.
        outcome = _reconcile(account, token, earlier)
        if outcome.status == "posted":
            _settle(earlier, "posted", post_id=outcome.post_id, url=account.post_url(outcome.post_id))
            return _sent(account, action, target, outcome, checked=False,
                         lead="An earlier attempt that X didn't confirm went through, so it wasn't sent again.\n")
        if outcome.status == "unknown":
            return Sent("unknown", f"An earlier attempt to send this as {account.handle} may have gone out, and "
                                   f"Row-Bot couldn't check. Look at {account.handle} on X first; nothing was sent now.")
        _settle(earlier, "not_posted")
    operation = _begin(account, action, digest, text, target)
    try:
        if action == "delete":
            outcome = _delete(account, token, target)
        else:
            body: dict[str, Any] = {"text": text}
            if action == "reply":
                body["reply"] = {"in_reply_to_tweet_id": target}
            elif action == "quote":
                body["quote_tweet_id"] = target
            if media:
                body["media"] = {"media_ids": _upload(account, media, token)}
            outcome = _create(account, token, body)
    except XPostError as exc:
        _settle(operation, "failed")
        return Sent("failed", str(exc))
    if outcome.status == "by_hand":
        _settle(operation, "failed")
        return Sent("by_hand", by_hand(account, action, text, target, refused=True))
    checked = outcome.status == "unknown"
    if checked:
        _settle(operation, "unknown")
        outcome = _reconcile(account, token, operation)
    facts = {"post_id": outcome.post_id, "url": account.post_url(outcome.post_id)} if outcome.post_id else {}
    _settle(operation, {"posted": "posted", "failed": "not_posted" if checked else "failed"}.get(outcome.status,
                                                                                                "unknown"), **facts)
    return _sent(account, action, target, outcome, checked=checked)


def _delete(account: XAccount, token: dict, target: str) -> Outcome:
    try:
        response = x_accounts.send("DELETE", f"{x_accounts.API_BASE}/tweets/{target}",
                                   headers=x_accounts.bearer(token))
    except Exception as exc:
        logger.warning("Deleting a post of %s got no answer (%s)", account.handle, type(exc).__name__)
        return Outcome("failed", detail="Row-Bot couldn't reach X.") if _definite(exc) else Outcome("unknown")
    if response.status_code >= 500:
        return Outcome("unknown")
    if response.status_code != 200:
        return Outcome("failed", detail=_refusal(response, account))
    try:
        deleted = bool((response.json().get("data") or {}).get("deleted"))
    except ValueError:
        deleted = False
    return Outcome("posted", target, _stamp(_now())) if deleted else Outcome("failed", detail="X didn't delete it.")


def read_text(account: XAccount, target: str) -> str:
    """A post's text, for the card that deletes it ("" when X doesn't say)."""
    token = x_accounts.valid_token(account)
    if token is None or not target:
        return ""
    try:
        response = x_accounts.send("GET", f"{x_accounts.API_BASE}/tweets/{target}", headers=x_accounts.bearer(token),
                                   timeout=10)
        return str((response.json().get("data") or {}).get("text") or "")[:_MAX_TEXT] if response.status_code == 200 else ""
    except Exception:
        return ""


# X counts a post's length by weight (5.3.1 G1): each link is 23, and characters outside the Latin and common
# punctuation ranges count 2. Without a subscription a post is 280; with one, 25,000.
_LINK = re.compile(r"https?://\S+", re.IGNORECASE)
LINK_LENGTH = 23
SHORT_POST, LONG_POST = 280, 25_000
_LIMITS: dict[str, int] = {}
MEDIA_LIMITS = "up to 4 pictures (5 MB each) or one GIF (15 MB) or one video (512 MB)"


def _weigh(text: str) -> int:
    import unicodedata

    return sum(1 if (code <= 4351 or 8192 <= code <= 8205 or 8208 <= code <= 8223 or 8242 <= code <= 8247) else 2
               for code in map(ord, unicodedata.normalize("NFC", text)))


def weighted_length(text: str) -> int:
    """The post's length as X counts it."""
    total, position = 0, 0
    for match in _LINK.finditer(text):
        total += _weigh(text[position:match.start()]) + LINK_LENGTH
        position = match.end()
    return total + _weigh(text[position:])


def text_limit(account: XAccount) -> int | None:
    """How long a post the account may send: 280, or 25,000 with an X subscription; None when X doesn't say."""
    if account.id in _LIMITS:
        return _LIMITS[account.id]
    token = x_accounts.valid_token(account)
    if token is None:
        return None
    try:
        response = x_accounts.send("GET", f"{x_accounts.API_BASE}/users/me", headers=x_accounts.bearer(token),
                                   params={"user.fields": "subscription_type"}, timeout=10)
        if response.status_code != 200:
            return None
        kind = str((response.json().get("data") or {}).get("subscription_type") or "None")
    except Exception:
        return None
    _LIMITS[account.id] = LONG_POST if kind in {"Basic", "Premium", "PremiumPlus"} else SHORT_POST
    return _LIMITS[account.id]


def check_length(account: XAccount, action: str, text: str) -> None:
    """Refuse, before the card, a post longer than the account may send (X would refuse it after approval)."""
    if action == "delete" or not text:
        return
    limit = text_limit(account)
    length = weighted_length(text)
    if limit is not None and length > limit:
        what = "with its X subscription" if limit == LONG_POST else "without an X subscription"
        raise XPostError(f"That post is {length:,} characters as X counts them (each link counts {LINK_LENGTH}); "
                         f"@{account.username} can post up to {limit:,} {what}. Shorten it by {length - limit:,}, "
                         "or post the rest as a reply to it.")


def check_text(action: str, text: str | None, target: str | None) -> tuple[str, str]:
    """The text and target an action needs, cleaned; raises XPostError when one is missing."""
    words = str(text or "")
    post = str(target or "").strip()
    if action != "delete" and not words.strip():
        raise XPostError("Give the text to post.")
    if action != "post" and not post.isdigit():
        raise XPostError(f"Give the numeric ID of the post to {action} (tweet_id).")
    if len(words) > _MAX_TEXT:
        raise XPostError("That text is longer than X allows.")
    return (words if action != "delete" else ""), (post if action != "post" else "")
