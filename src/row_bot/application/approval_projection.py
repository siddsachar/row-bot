"""Bounded public context for conversation approval prompts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from row_bot.application.conversation_traces import (
    canonical_tool_name,
    safe_tool_input,
)


_RISK_CLASSES = {"low", "medium", "high", "critical"}


def _text(value: Any, maximum: int) -> str:
    text = str(value or "").strip()[:maximum]
    return "".join(
        character
        for character in text
        if character in "\n\t" or ord(character) >= 32
    )


def project_approval_context(value: Any, *, fallback_reason: str = "") -> dict[str, Any]:
    """Expose only reviewed, size-bounded interrupt metadata."""

    from row_bot.questions import question_item
    from row_bot.tools.approval_gate import asked_alone

    items = value if isinstance(value, list) else [value]
    alone = asked_alone(items) if question_item(value) is None else None
    if alone is not None:
        # Only the person answers it, so its card is its own: an incognito chat's memory write (N11)
        # is asked as one, whatever else the step asks (B412). The rest ask again once it is answered.
        items = [alone]
    item = next((entry for entry in items if isinstance(entry, Mapping)), {})
    tool_name = _text(item.get("tool") or item.get("name"), 180)
    action_label = canonical_tool_name(tool_name) if tool_name else "Requested action"
    reason = _text(
        item.get("description")
        or item.get("reason")
        or fallback_reason
        or "This action requires approval under the current policy.",
        1024,
    )
    risk = _text(item.get("risk_class") or item.get("risk"), 32).casefold()
    if risk not in _RISK_CLASSES:
        risk = "unknown"
    scope = _text(item.get("scope") or item.get("consequence"), 1024)
    if not scope:
        scope = (
            f"{len(items)} requested actions will be resolved together."
            if len(items) > 1
            else "Only this requested action will be resolved."
        )
    requesting_trace_id = _text(
        item.get("tool_call_id")
        or item.get("call_id")
        or item.get("id")
        or item.get("__interrupt_id"),
        256,
    )
    from row_bot.approval_messages import reviewer_reason

    projected: dict[str, Any] = {
        "action_label": action_label,
        "reason": reason,
        "risk_class": risk,
        "scope": scope,
        "safe_argument_summary": safe_tool_input(item.get("args")),
        "requesting_trace_id": requesting_trace_id,
    }
    asked = reviewer_reason(item)
    if asked:  # Under Auto: why the reviewer asked the person rather than approving.
        projected["reviewer_reason"] = _text(asked, 240)
    question = _text(item.get("question"), 240) if len(items) == 1 else ""
    if question:  # Written by the tool (never the model), e.g. naming the local address it opens.
        projected["question"] = question
    # The folder a command runs in, by its name only, never its path (U7).
    folder = re.split(r"[\\/]", _text(item.get("folder"), 1024).rstrip("\\/"))[-1][:120] if len(items) == 1 else ""
    if folder:
        projected["folder"] = folder
    memory = item.get("memory_save") if len(items) == 1 else None
    memory_text = _text(memory.get("text"), 4096) if isinstance(memory, Mapping) else ""
    if memory_text:
        # A memory write in an incognito chat (N11): the exact text, Save or Don't save, the person's alone.
        # No reviewer was asked: the card says why in its own question.
        projected["memory_text"] = memory_text
        projected.pop("reviewer_reason", None)
    # Turning on a tool the work needs reads as a setup card ("Turn on …"),
    # and a code folder to use or a repository to clone as a folder card
    # where the person picks the folder (B277).
    from row_bot.application.conversation_traces import app_of_tool
    # A tool this runtime didn't issue still names its app in words, never as a raw name (B388).
    app = app_of_tool(tool_name, words=True) if len(items) == 1 else None
    if app:  # Shown with its logo and name, so the person knows which app is asking.
        projected["app"] = app
    change = item.get("change")
    if isinstance(change, Mapping) and len(items) == 1 and tool_name == "row_bot_update_setting":
        card = _setting_change_card(change)
        if card:  # The change in words, with its row (self system).
            projected["setting_change"] = card
    setup = item.get("setup")
    if isinstance(setup, Mapping) and len(items) == 1:
        projected_setup = _setup_card(setup)
        if projected_setup:
            projected["setup"] = projected_setup
    post = item.get("x_post")
    if isinstance(post, Mapping) and len(items) == 1 and tool_name == "x_post":
        card = _x_post_card(post)
        if card:  # Exactly what goes out on X, and as whom (N7): the text is never cut.
            projected["x_post"] = card
    # Row-Bot's question with options (N4): its card asks it, answered by the person alone. An action asked
    # beside it in the same step is never answered with it; it asks again for itself once this is answered.
    asked = question_item(value)
    if asked is not None:
        projected.update({
            "action_label": "Row-Bot has a question",
            "reason": _text(asked["questions"][0]["question"], 1024),
            "scope": "Only this question.",
            "safe_argument_summary": "",
            "requesting_trace_id": _text(asked.get("__interrupt_id"), 256),
            "questions": asked["questions"],
        })
        projected.pop("app", None)
        projected.pop("reviewer_reason", None)
    return projected


_DIGITS = re.compile(r"^[0-9]{1,20}$")
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,50}$")
_TARGET_URL = re.compile(r"^https://x\.com/[A-Za-z0-9_]+/status/[0-9]+$")
_THUMBNAIL = re.compile(r"^data:image/jpeg;base64,[A-Za-z0-9+/=]+$")
_SLOT = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d)?$")


def _x_post_card(post: Mapping[str, Any]) -> dict[str, Any] | None:
    """An X post's card as the tool wrote it, checked field by field; None if any key fact is off."""
    account_id, username = str(post.get("account_id") or ""), str(post.get("username") or "")
    action = post.get("action")
    target_id = str(post.get("target_id") or "")
    target_url = str(post.get("target_url") or "")
    if (not _DIGITS.fullmatch(account_id) or not _HANDLE.fullmatch(username)
            or action not in {"post", "reply", "quote", "delete"}
            or (target_id and not _DIGITS.fullmatch(target_id)) or (target_url and not _TARGET_URL.fullmatch(target_url))
            or not isinstance(post.get("text"), str) or len(post["text"]) > 25000):
        return None
    media = []
    for entry in post.get("media") if isinstance(post.get("media"), list) else []:
        if not isinstance(entry, Mapping) or len(media) == 4:
            return None  # A card that can't show every file it sends isn't shown at all.
        thumbnail = str(entry.get("thumbnail") or "")
        size = entry.get("size")
        if (not _text(entry.get("name"), 256) or not isinstance(size, int) or isinstance(size, bool) or size < 0
                or not re.fullmatch(r"[0-9a-f]{12}", str(entry.get("sha256") or ""))
                or entry.get("kind") not in {"image", "gif", "video"}):
            return None
        media.append({"name": _text(entry.get("name"), 256), "size": size, "sha256": entry["sha256"],
                      "kind": entry["kind"],
                      "thumbnail": thumbnail if len(thumbnail) <= 32768 and _THUMBNAIL.fullmatch(thumbnail) else ""})
    limit = post.get("limit")
    return {"account_id": account_id, "username": username, "name": _text(post.get("name"), 80), "action": action,
            "text": post["text"], "target_id": target_id, "target_url": target_url,
            "target_text": str(post.get("target_text") or "")[:25000], "media": media,
            "limit": limit if isinstance(limit, int) and not isinstance(limit, bool) and 0 <= limit <= 25000 else 0,
            "post_at": str(post.get("post_at") or "") if _SLOT.fullmatch(str(post.get("post_at") or "")) else ""}


_SETTINGS_HREF = re.compile(r"^/settings/[A-Za-z0-9/?=&#._:-]{0,189}$")


def _setting_change_card(change: Mapping[str, Any]) -> dict[str, Any] | None:
    """A setting change as its card shows it, checked field by field; None without a name or new value."""
    label, after = _text(change.get("label"), 120), _text(change.get("after"), 240)
    if not label or not after:
        return None
    href = _text(change.get("href"), 200)
    return {"label": label, "where": _text(change.get("where"), 200),
            "href": href if _SETTINGS_HREF.fullmatch(href) else "",
            "before": _text(change.get("before"), 240), "after": after}


def _setup_card(setup: Mapping[str, Any]) -> dict[str, Any] | None:
    kind = setup.get("kind")
    label = _text(setup.get("label"), 120)
    if not label or kind not in {"tool", "folder", "clone"}:
        return None
    if kind == "tool":
        return {"kind": "tool", "label": label}
    if kind == "clone":
        repo_url = _text(setup.get("repo_url"), 2048)
        return {"kind": "clone", "label": label, "repo_url": repo_url} if repo_url else None
    raw = setup.get("folders")
    folders = []
    for entry in raw if isinstance(raw, list) else []:
        if len(folders) == 8:
            break
        if not isinstance(entry, Mapping):
            continue
        folder = {"resource_id": _text(entry.get("resource_id"), 256), "name": _text(entry.get("name"), 120),
                  "revision": _text(entry.get("revision"), 128)}
        if folder["resource_id"] and folder["name"]:
            folders.append(folder)
    return {"kind": "folder", "label": label, "folders": folders}
