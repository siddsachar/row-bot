"""What a joined Row-Bot tells its company (Row-Bot for Teams, C4): audit events, metadata only.

Three actions, and nothing else:

- ``tool.called``: a tool action that went through the approval gate, whatever the gate decided (approved by the
  person, by the level, by one of their rules or chat grants, refused, blocked by the company's ceiling, or failed
  after it was let through). Read-only and ungated tool use is not sent.
- ``data.exported``: a chat export, or a data backup (which holds every chat), at the moment it succeeds.
- ``device.locked_down``: each offline lockdown C3a recorded (``policy.lockdowns()``), once it has ended.

**Never content.** Events are built only here, by ``tool_called`` and ``data_exported``, whose keyword-only
parameters are the contract's metadata fields: a tool's name, its app, the approval level, risk, surface, who
decided, a rule's id, the outcome, an export's count. There is no parameter a summary, a prompt, an argument or a
result could be passed through, every value is checked against the shared contract's closed forms
(``row_bot.audit``: identifiers and enums, no free text), and every event is built at the ``metadata`` level,
which refuses a summary on its own.

**The outbox.** Each event is built with its own id when it happens and appended, as the contract's JSON, to
``company_audit.jsonl`` in the data folder. The file holds at most ``MAX_EVENTS`` events or ``MAX_BYTES``; when
full the oldest are dropped, a warning says so, and the next send's log line counts them. Events belong to the
device that recorded them: a fresh join (another device id) starts empty, a re-join of the same device keeps them.

**Sending.** Batches of at most ``BATCH_EVENTS`` events and ``BATCH_BYTES`` go to the Company Server's
``POST /api/audit/events`` with the device's bearer; an event keeps its id across retries (the server skips one it
already has). A send runs in the background ``SOON_SECONDS`` after an event, at most once every ``MIN_INTERVAL``
seconds, and after the company's policy was fetched (the first use after the app starts, a join, Settings); never
on the start-up path. On an error the events stay and the next send backs off; a 422 naming one event drops that
event, logs its index and reason, and sends the rest. Leave makes one last send, then deletes the outbox; while
disconnected, the events stay for a re-join.

Not joined: nothing is recorded, no file is made and nothing is sent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot.audit import (
    Action, Actor, ActorKind, AuditEvent, AuditLayer, InvalidAuditEvent, RedactionLevel, Target,
    TargetKind, from_dict, looks_like_secret, new_event_id, to_dict,
)
from row_bot.audit.vocabulary import ID_PATTERN
from row_bot.company import store
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

OUTBOX_FILE = "company_audit.jsonl"
INGEST_PATH = "/api/audit/events"
MAX_EVENTS = 10_000
MAX_BYTES = 5 * 1024 * 1024
# The server's limits for one batch (S5's ingest).
BATCH_EVENTS = 100
BATCH_BYTES = 256 * 1024
SOON_SECONDS = 5.0
MIN_INTERVAL = 60.0
BACKOFF_MAX = 60 * 60.0
# The Company Server sets the actor (the signed-in person), the organisation and the time recorded.
ORG_ID = "company"
ACTOR = Actor(ActorKind.PERSON, "self")

# Who let a gated action run, or stopped it (the activity log's deciders): an event is sent only for these. "none"
# (nothing needed approval) and "policy" (a tool's own refusal) never went through the gate.
GATED = frozenset({"person", "reviewer", "hands_off", "rule", "chat_grant", "block"})
# The activity log's outcomes of an action that ran.
_RAN = frozenset({"ran", "approved_by_you", "approved_by_reviewer", "hands_off", "allowed_by_rule",
                  "allowed_in_chat"})
_ID = re.compile(ID_PATTERN)
_ENVELOPE = len(b'{"events":[]}')

# Seconds on a clock that only moves forward, and how a send is started later; tests replace them.
monotonic = time.monotonic
Timer = threading.Timer

_LOCK = threading.RLock()
_SEND = threading.Lock()
# (path, mtime, size) of the outbox last read, and what it held: the device its events belong to, how many, and
# whether it ends with a whole line.
_memo: tuple[tuple[str, int, int], tuple[str | None, int, bool]] | None = None
_timer: Any = None
_last_attempt: float | None = None  # When this process last sent (monotonic).
_retry_at = 0.0  # Not before this after an error (monotonic).
_failures = 0
_dropped = 0  # Dropped from a full outbox since the last send.


def outbox_path() -> Path:
    return get_row_bot_data_dir(create=False) / OUTBOX_FILE


# -- building events ----------------------------------------------------------------------------------

def _id_or_none(value: str | None) -> str | None:
    """An optional identifier the contract can carry, or None (a form it can't is left out, never guessed at)."""
    if not value or not _ID.fullmatch(value) or looks_like_secret(value):
        return None
    return value


def _event(record: store.Record, action: Action, outcome: str, *, target: Target, details: dict[str, Any],
           decided_by: str | None = None, occurred_at: datetime | None = None) -> AuditEvent:
    at = datetime.now(timezone.utc)
    return AuditEvent(id=new_event_id(), occurred_at=occurred_at or at, recorded_at=at, org_id=ORG_ID, actor=ACTOR,
                      action=action, outcome=outcome, layer=AuditLayer.ORG, target=target,
                      device_id=record.device_id, decided_by=decided_by, redaction=RedactionLevel.METADATA,
                      details=details)


def tool_called(*, tool: str, outcome: str, decided_by: str, approval_level: str, surface: str,
                risk: str | None = None, app: str | None = None, rule_id: str | None = None) -> bool:
    """Note one tool action for the company, in the activity log's words (its outcome and decider), if it went
    through the approval gate and a company is joined. Whether it was noted.

    Only these fields exist; a value outside the contract's forms raises ``InvalidAuditEvent``."""
    if decided_by not in GATED:
        return False
    record = store.read_record()
    if record is None:
        return False
    # The company's ceiling of Block, or the person's own level, refused it: blocked, whichever way it was asked.
    result = "blocked" if decided_by == "block" else "succeeded" if outcome in _RAN else outcome
    details: dict[str, Any] = {"approval_level": approval_level, "surface": surface}
    if risk:
        details["risk"] = risk
    if found := _id_or_none(app):
        details["app"] = found
    if found := _id_or_none(rule_id):
        details["rule_id"] = found
    event = _event(record, Action.TOOL_CALLED, result, target=Target(TargetKind.TOOL, tool), details=details,
                   decided_by=decided_by)
    _append(record, event)
    send_soon()
    return True


def data_exported(*, item_count: int | None = None) -> bool:
    """Note that the person's own data left Row-Bot (a chat export, ``item_count`` chats; or a data backup), if a
    company is joined. Whether it was noted."""
    record = store.read_record()
    if record is None:
        return False
    details: dict[str, Any] = {"scope": "person"}
    if item_count is not None:
        details["item_count"] = item_count
    try:
        event = _event(record, Action.DATA_EXPORTED, "succeeded",
                       target=Target(TargetKind.EXPORT, new_event_id()), details=details)
        _append(record, event)
        send_soon()
    except Exception as error:  # noqa: BLE001 - the export itself succeeded and stays so
        logger.warning("An export couldn't be recorded for the company (%s)", type(error).__name__)
        return False
    return True


def _queue_lockdowns(record: store.Record) -> None:
    """C3a's lockdowns that ended, as ``device.locked_down`` events (when each began), then marked sent."""
    from row_bot.company import policy

    ended = [item for item in policy.lockdowns() if item["ended_at"] is not None]
    if not ended:
        return
    for item in ended:
        _append(record, _event(
            record, Action.DEVICE_LOCKED_DOWN, "succeeded", target=Target(TargetKind.DEVICE, record.device_id),
            details={"offline_days": int(item["offline_days"])},
            occurred_at=datetime.fromtimestamp(float(item["started_at"]), timezone.utc)))
    policy.lockdowns_sent(item["started_at"] for item in ended)


# -- the outbox ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Entry:
    id: str
    line: str


def _line(event: AuditEvent) -> str:
    return json.dumps(to_dict(event), ensure_ascii=False, separators=(",", ":"))


def _size(line: str) -> int:
    return len(line.encode("utf-8")) + 1


def _key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return str(path), stat.st_mtime_ns, stat.st_size


def _remember(path: Path, device: str | None, count: int) -> None:
    global _memo
    key = _key(path)
    _memo = None if key is None else (key, (device, count, True))


def _state(path: Path) -> tuple[str | None, int, bool]:
    """(the device the outbox's events belong to, how many lines it holds, whether it ends with a whole line)."""
    global _memo
    key = _key(path)
    if key is None:
        return None, 0, True
    if _memo is not None and _memo[0] == key:
        return _memo[1]
    data = path.read_bytes()
    lines = [line for line in data.split(b"\n") if line.strip()]
    device = None
    if lines:
        try:
            device = json.loads(lines[0]).get("device_id")
        except (ValueError, AttributeError):
            device = None
    found = (device if isinstance(device, str) else None, len(lines), not data or data.endswith(b"\n"))
    _memo = (key, found)
    return found


def _raw(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")  # A damaged line then fails its check.
    except FileNotFoundError:
        return []
    return [line for line in text.split("\n") if line.strip()]


def _rewrite(path: Path, lines: list[str], device: str | None) -> None:
    if not lines:
        path.unlink(missing_ok=True)
        _remember(path, None, 0)
        return
    partial = path.with_name(path.name + ".partial")
    partial.write_text("".join(line + "\n" for line in lines), encoding="utf-8", newline="\n")
    os.replace(partial, path)
    _remember(path, device, len(lines))


def _append(record: store.Record, event: AuditEvent) -> None:
    global _dropped
    line = _line(event)
    with _LOCK:
        path = outbox_path()
        device, count, whole = _state(path)
        if count and device != record.device_id:
            # Another device's events (an earlier join that never came back): a fresh join starts empty.
            logger.info("%d audit events from an earlier join of this Row-Bot were dropped", count)
            _rewrite(path, [], None)
            count, whole = 0, True
        size = path.stat().st_size if count else 0
        if count + 1 > MAX_EVENTS or size + _size(line) > MAX_BYTES:
            lines = _raw(path)
            kept_bytes = sum(_size(item) for item in lines)
            cut = 0
            while cut < len(lines) and (len(lines) - cut + 1 > MAX_EVENTS
                                        or kept_bytes + _size(line) > MAX_BYTES):
                kept_bytes -= _size(lines[cut])
                cut += 1
            if cut and not _dropped:
                logger.warning("The company audit outbox is full; its oldest events are dropped (the next send "
                               "counts them)")
            _dropped += cut
            _rewrite(path, [*lines[cut:], line], record.device_id)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as outbox:
            outbox.write(("" if whole else "\n") + line + "\n")
        _remember(path, record.device_id, count + 1)


def _load(record: store.Record) -> list[_Entry]:
    """The events waiting to be sent, oldest first. Lines that don't check out as this device's events are
    dropped (and counted in the log)."""
    path = outbox_path()
    entries: list[_Entry] = []
    other = broken = 0
    for line in _raw(path):
        try:
            event = from_dict(json.loads(line))
        except (ValueError, InvalidAuditEvent):
            broken += 1
            continue
        if event.device_id != record.device_id:
            other += 1
            continue
        entries.append(_Entry(event.id, line))
    if other or broken:
        logger.info("Audit events left out of the company outbox: %d from an earlier join, %d unreadable",
                    other, broken)
        _rewrite(path, [entry.line for entry in entries], record.device_id)
    return entries


def _remove(ids: set[str]) -> None:
    """Take sent (or refused) events out of the outbox; events added meanwhile stay."""
    with _LOCK:
        path = outbox_path()
        device = _state(path)[0]
        kept = []
        for line in _raw(path):
            try:
                found = json.loads(line).get("id")
            except (ValueError, AttributeError):
                found = None
            if found not in ids:
                kept.append(line)
        _rewrite(path, kept, device)


def _batch(entries: list[_Entry]) -> list[_Entry]:
    batch: list[_Entry] = []
    size = _ENVELOPE
    for entry in entries[:BATCH_EVENTS]:
        grown = size + len(entry.line.encode("utf-8")) + (1 if batch else 0)
        if batch and grown > BATCH_BYTES:
            break
        batch.append(entry)
        size = grown
    return batch


def _waiting(record: store.Record) -> bool:
    from row_bot.company import policy

    with _LOCK:
        device, count, _whole = _state(outbox_path())
    if count and device == record.device_id:
        return True
    return any(item["ended_at"] is not None for item in policy.lockdowns())


# -- sending ------------------------------------------------------------------------------------------

def _back_off(wait: float | None = None) -> None:
    global _failures, _retry_at
    _failures += 1
    delay = wait if wait is not None else min(BACKOFF_MAX, MIN_INTERVAL * 2 ** (_failures - 1))
    _retry_at = monotonic() + max(delay, MIN_INTERVAL)


def _refused_index(response: Any, size: int) -> tuple[int, str] | None:
    """The one event a 422 names (its index in the batch, and why), or None."""
    if response.status_code != 422:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    index = data.get("index") if isinstance(data, dict) else None
    if type(index) is not int or not 0 <= index < size:
        return None
    reason = " ".join(f"{data.get('error') or ''} {data.get('detail') or ''}".split())[:200]
    return index, reason


def send_now() -> None:
    """One send of everything waiting, batch by batch (the background send, and Leave's last one)."""
    global _last_attempt, _failures, _dropped, _retry_at
    from row_bot.company import client

    with _SEND:
        record = store.read_record()
        if record is None or record.disconnected:
            return
        _last_attempt = monotonic()
        _queue_lockdowns(record)
        with _LOCK:
            entries = _load(record)
        while entries:
            batch = _batch(entries)
            body = ("{\"events\":[" + ",".join(entry.line for entry in batch) + "]}").encode("utf-8")
            try:
                response = client.call(record.origin, "POST", INGEST_PATH, content=body)
            except client.Disconnected:
                return  # Kept for a re-join of this device.
            except client.Busy as busy:
                logger.info("The company asked Row-Bot to wait before sending audit events; they're kept")
                _back_off(busy.retry_after)
                return
            except (client.Unreachable, store.StorageUnavailable) as error:
                logger.info("Audit events couldn't be sent to the company (%s); they're kept", type(error).__name__)
                _back_off()
                return
            if response.status_code == 200:
                _remove({entry.id for entry in batch})
                entries = entries[len(batch):]
                with _LOCK:
                    dropped, _dropped = _dropped, 0
                logger.info("Sent %d audit events to the company%s", len(batch),
                            f"; {dropped} older ones were dropped while the outbox was full" if dropped else "")
                continue
            refused = _refused_index(response, len(batch))
            if refused is not None:
                index, reason = refused
                logger.warning("The company refused audit event %d of a batch (%s); it is dropped", index, reason)
                bad = batch[index].id
                _remove({bad})
                entries = [entry for entry in entries if entry.id != bad]
                continue
            logger.info("The company didn't take Row-Bot's audit events (%d); they're kept", response.status_code)
            _back_off()
            return
        _failures, _retry_at = 0, 0.0  # All sent: the next error backs off from a minute again.


def _run() -> None:
    global _timer
    with _LOCK:
        _timer = None
    try:
        send_now()
    except Exception as error:  # noqa: BLE001 - the events stay for the next send
        logger.warning("Audit events couldn't be sent to the company (%s)", type(error).__name__)
        _back_off()
    send_soon()


def send_soon() -> None:
    """Send what's waiting in the background: soon, but at most once a minute, and not before a back-off ends.
    Joined, not disconnected and something waiting only; nothing else happens."""
    global _timer
    record = store.read_record()
    if record is None or record.disconnected or not _waiting(record):
        return
    with _LOCK:
        if _timer is not None:
            return
        at = monotonic()
        delay = max(SOON_SECONDS, _retry_at - at,
                    0.0 if _last_attempt is None else _last_attempt + MIN_INTERVAL - at)
        _timer = Timer(delay, _run)
        _timer.daemon = True
        _timer.start()


def stop(timeout: float = 5.0) -> None:
    """Cancel a send waiting to start, and wait for one running to end."""
    global _timer
    with _LOCK:
        timer, _timer = _timer, None
    if timer is not None:
        timer.cancel()
    if _SEND.acquire(timeout=timeout):
        _SEND.release()


def forget() -> None:
    """The outbox goes, and what this process remembered of it."""
    global _memo, _dropped, _failures, _retry_at
    stop()
    with _LOCK:
        path = outbox_path()
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".partial").unlink(missing_ok=True)
        _memo = None
        _dropped = _failures = 0
        _retry_at = 0.0


def leave() -> None:
    """Leave: one last send of what's waiting (while the company still accepts this Row-Bot), then the outbox
    goes. A send that fails doesn't stop Leave."""
    stop()
    record = store.read_record()
    if record is not None and not record.disconnected and _waiting(record):
        try:
            send_now()
        except Exception as error:  # noqa: BLE001 - Leave goes ahead
            logger.warning("Audit events couldn't be sent before leaving (%s)", type(error).__name__)
    forget()


def waiting_ids() -> list[str]:
    """The ids of the events waiting to be sent, oldest first (Settings and tests read what is kept)."""
    record = store.read_record()
    if record is None:
        return []
    with _LOCK:
        return [entry.id for entry in _load(record)]


__all__ = [
    "BATCH_BYTES", "BATCH_EVENTS", "GATED", "INGEST_PATH", "MAX_BYTES", "MAX_EVENTS", "MIN_INTERVAL", "OUTBOX_FILE",
    "SOON_SECONDS", "data_exported", "forget", "leave", "outbox_path", "send_now", "send_soon", "stop",
    "tool_called", "waiting_ids",
]
