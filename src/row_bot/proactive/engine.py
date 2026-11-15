"""The proactive tick: the scheduler runs it every 30 seconds while Row-Bot runs.

Each tick:

1. settles moments that ran out (``feedback.sweep``);
2. reads the desktop's state (when its switch is on) and notes the breaks since the last tick;
3. runs the watchers that are due; what they found passes the person's rules, then one triage pass on the
   main model keeps what is worth a moment (``triage``), and each pass is logged even when it found
   nothing (silence is the usual result). A moment that wants preparing (a meeting brief, a drafted
   reply) gets it in the background, read-only (``prepare``);
4. on the first break of the day, runs the morning brief's workflow on what waits (its reply becomes the
   brief, which carries them), and at the day's end the end-of-day wrap's when it is on (``day``);
5. sends the pending moments the budget allows (``timing.decide``), one at a time, each re-checked live
   just before (stale ones are dropped) and put on exactly one surface (``deliver``).

Nothing runs while the level is Off, beyond settling what ran out.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from row_bot.proactive import feedback, store, watchers
from row_bot.proactive import settings as proactive_settings
from row_bot.proactive import timing
from row_bot.proactive.model import KIND_WORDS, UNKNOWN, Candidate, Desktop

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
SILENCED_KEPT = 2000  # Changes triage kept silent: never asked about again.
_TICK_LOCK = threading.Lock()


def read_desktop(settings: dict[str, Any]) -> Desktop:
    """The desktop's state when its switch is on; unknown otherwise."""
    if not settings["desktop_context"]:
        return UNKNOWN
    from row_bot.proactive import desktop_context

    return desktop_context.read()


def note_boundary(kind: str, *, now: float | None = None) -> None:
    """A break the server can't see by itself (the app opened): moments batched for a break may go out."""
    if kind not in timing.BOUNDARY_WORDS:
        raise ValueError(kind)
    store.set_state("boundary", {"kind": kind, "at": time.time() if now is None else now})


def _open_boundary(found: list[str], now: float) -> str | None:
    if found:
        store.set_state("boundary", {"kind": found[0], "at": now})
    saved = store.get_state("boundary") or {}
    if saved and now - float(saved.get("at", 0)) <= timing.BOUNDARY_WINDOW_SECONDS:
        return str(saved.get("kind") or "")
    return None


def _triage(candidates: list[Candidate], settings: dict[str, Any],
            now: float) -> list[tuple[Candidate, str, str]] | None:
    """(candidate, title, body) for each candidate worth a moment: one pass on the main model, silent by
    default."""
    from row_bot.proactive import triage

    recent = [moment["title"] for moment in store.list_moments(since=now - 3 * 86400, limit=10)]
    rules = [feedback.rule_words(rule) for rule in store.rules()]
    kept, words = triage.decide(candidates, level=settings["level"], rules=rules, recent=recent, now=now)
    store.log("pass", words, now=now, detail={"changes": len(candidates), "kept": -1 if kept is None else len(kept)})
    return kept


def _prepare(moment: dict[str, Any], untrusted: str, now: float) -> None:
    """The read-only preparation a moment wants (a meeting brief, a drafted reply): what Dream Cycle prepared
    for it overnight, or a fresh one in the background."""
    from row_bot.proactive import prepare

    if prepare.wants(moment) and not prepare.attach_ahead(moment, now=now):
        prepare.queue(moment["id"], untrusted)


def _gather(candidates: list[Candidate], settings: dict[str, Any], at: datetime) -> list[dict[str, Any]]:
    """Candidates through the person's choices and rules, then triage; the moments kept."""
    now = at.timestamp()
    rules = store.rules()
    silenced = list(store.get_state("silenced") or [])
    fresh: list[Candidate] = []
    for candidate in candidates:
        if (not settings["kinds"].get(candidate.kind, False) or candidate.key in silenced
                or store.seen_key(candidate.key)):
            continue  # Off, already a moment, or triage already said it isn't worth one.
        reason = feedback.blocked(candidate, rules, today_start=timing.day_start(at))
        if reason:
            store.log("rule", f"Left out “{candidate.title}”: {reason}.", now=now,
                      detail={"kind": candidate.kind, "watcher": candidate.watcher})
            continue
        fresh.append(candidate)
    if not fresh:
        return []
    chosen = _triage(fresh, settings, now)
    if chosen is None:
        return []  # Nothing was decided: nothing is shown, and nothing is remembered as silenced.
    kept_keys = {candidate.key for candidate, _title, _body in chosen}
    silenced.extend(candidate.key for candidate in fresh if candidate.key not in kept_keys)
    store.set_state("silenced", silenced[-SILENCED_KEPT:])
    kept = []
    for candidate, title, body in chosen:
        moment = store.add_moment(candidate, title=title, body=body, now=now)
        if moment is not None:
            store.log("moment", f"New: “{title}” ({KIND_WORDS[candidate.kind]}).", now=now, moment_id=moment["id"],
                      detail={"watcher": candidate.watcher})
            kept.append(moment)
            _prepare(moment, candidate.untrusted, now)
    return kept


# The watchers' sources in the activity log's words.
SOURCE_WORDS = {"chats": "chats", "dates": "dates", "workflows": "workflows", "sign_ins": "sign-ins",
                "mail": "mail", "calendar": "calendar"}


def _passes(candidates: list[Candidate], ran: list[str], skipped: list[str], kept: list[dict[str, Any]],
            now: float) -> None:
    if not ran and not skipped:
        return
    if not candidates:
        words = "nothing new"
    else:
        words = f"{len(candidates)} change{'s' if len(candidates) != 1 else ''}, {len(kept)} worth a mention"
    named = [SOURCE_WORDS.get(name, name.replace("_", " ")) for name in ran]
    unconnected = [SOURCE_WORDS.get(name, name.replace("_", " ")) for name in skipped]
    summary = (f"Checked {', '.join(named)}: {words}." if named else "")
    if unconnected:
        summary += (" " if summary else "") + f"Skipped (not connected): {', '.join(unconnected)}."
    store.log("pass", summary, now=now,
              detail={"ran": ran, "skipped": skipped, "changes": len(candidates), "moments": len(kept)})


def _day(found: list[str], boundary: str | None, at: datetime, settings: dict[str, Any]) -> None:
    """The morning brief at the day's first break, the end-of-day wrap at its end (``day``)."""
    from row_bot.proactive import day

    day.morning(boundary, at, settings)
    day.evening(found, at, settings)


def _recheck(moment: dict[str, Any], now: float) -> bool | None:
    """Its watcher's live check of the evidence, just before it is shown (True when no watcher owns it)."""
    watcher = watchers.get(moment["watcher"])
    if watcher is None:
        return True
    try:
        return watcher.recheck(moment, now)
    except Exception:
        logger.info("Re-checking a %s moment failed; it waits", moment["kind"], exc_info=True)
        return None


def _pick(moment: dict[str, Any], desktop: Desktop, settings: dict[str, Any]) -> str:
    from row_bot.proactive import deliver

    return deliver.pick(moment, desktop, settings, buddy=deliver.buddy_on(), push=deliver.push_ready(),
                        channel=deliver.channel_ready(settings))


def _put(surface: str, moment: dict[str, Any], settings: dict[str, Any]) -> bool:
    from row_bot.proactive import deliver

    return deliver.send(surface, moment, settings)


def _send(boundary: str | None, at: datetime, desktop: Desktop, settings: dict[str, Any]) -> list[dict[str, Any]]:
    now = at.timestamp()
    rules = store.rules()
    pending = store.list_moments(("pending",), limit=200)
    # Urgent first, then the day's brief or wrap (it lists what waits), then by how welcome each kind is.
    pending.sort(key=lambda moment: (moment["urgency"] != "urgent", moment["kind"] != "brief",
                                     -feedback.weight(moment["kind"]), moment["created_at"]))
    sent: list[dict[str, Any]] = []
    for moment in pending:
        today = timing.day_start(at)
        decision = timing.decide(
            moment, at=at, desktop=desktop, settings=settings, boundary=boundary,
            shown_today=store.shown_since(today), kind_shown_today=store.shown_since(today, kind=moment["kind"]),
            kind_last_shown=store.last_shown(moment["kind"]),
            quieted=bool(store.kind_stats(moment["kind"])["quieted_at"]), rules=rules)
        if decision.action == "expire":
            if store.move(moment["id"], "expired", from_states=("pending",), now=now):
                store.log("moment", f"“{moment['title']}” ran out before a good moment came.", now=now,
                          moment_id=moment["id"])
            continue
        if decision.action == "hold":
            continue
        if sent and moment["urgency"] != "urgent":
            continue  # One at a time at a break: the next one goes on the next tick.
        place = _pick(moment, desktop, settings)
        if not place:
            continue  # Away, and nothing reaches them there: it waits until they're back.
        fresh = _recheck(moment, now)
        if fresh is None:
            continue
        if not fresh:
            if store.move(moment["id"], "dropped_stale", from_states=("pending",), now=now):
                store.log("moment", f"Dropped “{moment['title']}”: it no longer holds.", now=now,
                          moment_id=moment["id"])
            continue
        # Claimed for one surface first, so no other tick or surface can show it too.
        if not store.move(moment["id"], "shown", from_states=("pending",), now=now, surface=place, shown_at=now,
                          why=list(decision.why)):
            continue
        if not _put(place, moment, settings):
            store.move(moment["id"], "pending", from_states=("shown",), now=now, surface="", shown_at=0.0, why=[])
            continue
        store.count(moment["kind"], "shown", now=now)
        store.log("moment", f"Shown: “{moment['title']}” ({place}). {decision.reason}", now=now,
                  moment_id=moment["id"], detail={"surface": place, "why": list(decision.why)})
        sent.append(moment)
    return sent


def tick(now: float | None = None, *, desktop: Desktop | None = None) -> dict[str, Any]:
    """One proactive pass; what it did, for tests and the log."""
    now = time.time() if now is None else now
    if not _TICK_LOCK.acquire(blocking=False):
        return {"skipped": True}
    try:
        at = datetime.fromtimestamp(now)
        settings = proactive_settings.load()
        feedback.sweep(now)
        if settings["level"] == "off":
            return {"off": True}
        desktop = read_desktop(settings) if desktop is None else desktop
        found, remembered = timing.detect_boundaries(store.get_state("previous") or {}, desktop, at, settings)
        store.set_state("previous", remembered)
        boundary = _open_boundary(found, now)
        candidates, ran, skipped = watchers.run_due(now, settings)
        kept = _gather(candidates, settings, at) if candidates else []
        _passes(candidates, ran, skipped, kept, now)
        _day(found, boundary, at, settings)
        sent = _send(boundary, at, desktop, settings)
        return {"boundaries": found, "boundary": boundary, "ran": ran, "candidates": len(candidates),
                "moments": [moment["id"] for moment in kept], "sent": [moment["id"] for moment in sent]}
    finally:
        _TICK_LOCK.release()


def _tick_quietly() -> None:
    try:
        tick()
    except Exception:
        logger.warning("A proactive pass failed", exc_info=True)


def schedule(scheduler: Any) -> None:
    """At start-up: the built-in watchers, opening Row-Bot is a break, and the tick runs every 30 seconds."""
    watchers.register_defaults()
    note_boundary("app_open")
    scheduler.add_job(_tick_quietly, trigger="interval", seconds=TICK_SECONDS, id="row-bot-proactive",
                      replace_existing=True, coalesce=True, max_instances=1)
