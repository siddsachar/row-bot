"""What the company's policy changes in this app (Row-Bot for Teams, C3a).

A joined Row-Bot knows its person's effective company policy (the server's ``GET /api/policy/effective``) and
keeps it current:

- **Fetched** with the device's bearer and ``If-None-Match``; a 304 confirms the cached policy. The body is read
  with the shared model's strict parser (``row_bot.policy``), except that settings this app doesn't know are left
  out and their names logged once (a newer server may add some, F-19).
- **Cached** in the data folder (``company_policy.json``): the policy, its ETag, when it was last confirmed and
  when a refresh last failed. Nothing secret, but checked on load; a file that doesn't check out counts as none.
- **Refreshed** when a join or Sign in again finishes, when Settings › Company opens (and its Retry), and, while
  the app is in use (a model list is read or a chat model made), at most every ``REFRESH_SECONDS`` in the
  background. The first such use after the app starts is its start-up refresh, off the start-up path. Nothing
  polls.
- **The lease.** A policy last confirmed more than ``offline.max_days`` days ago gives way to
  ``row_bot.policy.lockdown_policy()``: models on this computer only. With ``max_days`` 0, a failed refresh is
  enough. A disconnected Row-Bot (revoked, suspended, a month since its sign-in) is locked down at once, and so
  is a joined one whose policy hasn't been fetched yet. A lockdown ends at the next successful fetch, never
  before; its start and end are kept in the cache (``lockdowns()``) for C4 to send as ``device.locked_down``.

The rest of the app asks ``current()`` (the policy in force, or None when not joined) and ``state()``; the model
seams ask ``allows_provider``, ``turned_off`` and ``refuse``. Not joined, every answer is "no company": one
file's absence is read, and nothing else happens.

What counts as what, by provider (``provider_kind``):

- **company**: the company's own models (``company``), allowed by ``models.allowed``;
- **local**: Ollama, Row-Bot's own servers (Run on this computer, Learning's) and any custom OpenAI-compatible
  endpoint whose address is this computer's loopback (LM Studio, llama.cpp, vLLM on this computer);
- **consumer**: the subscription sign-ins (ChatGPT / Codex, the Claude plan, xAI Grok, OpenCode Go: the
  providers billed as a subscription);
- **personal**: everything else, the person's own API keys and custom endpoints elsewhere.

The person's keys are never touched: a provider the policy turns off is hidden and not used, its key stays in
the keychain.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from row_bot.company import store
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.policy import model as shared
from row_bot.policy.model import ALL_MODELS, Policy, PolicyError, lockdown_policy

logger = logging.getLogger(__name__)

CACHE_FILE = "company_policy.json"
EFFECTIVE_PATH = "/api/policy/effective"
# At most this often while the app is in use.
REFRESH_SECONDS = 15 * 60
DAY = 24 * 60 * 60
MAX_CACHE_BYTES = 512 * 1024
MAX_ETAG = 256
MAX_LOCKDOWNS = 50
# A confirmation time this far ahead of the clock means a file that doesn't check out.
CLOCK_SLACK = 300

State = Literal["not_joined", "current", "cached", "locked_down"]
# locked_down: "lease_expired", "disconnected" or "not_confirmed" (joined, but no policy fetched yet);
# cached: "unreachable" (the last refresh couldn't reach the server).
Reason = Literal["lease_expired", "disconnected", "not_confirmed", "unreachable"]
Kind = Literal["company", "local", "consumer", "personal"]
LOCKDOWN_REASONS = frozenset({"lease_expired", "disconnected"})

# Why a model can't be used, in a few words: the composer's model pill, Settings › Models.
TURNED_OFF = {
    "personal": "Your company turned off personal API keys",
    "consumer": "Your company turned off personal subscriptions",
    "local": "Your company turned off models on this computer",
    "not_allowed": "Your company doesn't let you use this model",
    "locked_down": "Row-Bot can't reach your company right now",
}
# The same as a chat's failure. These words reach the person through the provider-error path, whose kinds are
# matched by words, so none says "API key" (read as a sign-in to fix, which a backup model would answer for).
REFUSED = {
    "personal": "Your company turned off models that use your own keys, so this one can't be used. "
                "Choose another model.",
    "consumer": "Your company turned off personal subscriptions, so this model can't be used. "
                "Choose another model.",
    "local": "Your company turned off models on this computer, so this one can't be used. Choose another model.",
    "not_allowed": "Your company doesn't let you use this model. Choose another model.",
    "locked_down": "Row-Bot can't reach your company, so it's using only what's safe on this computer. "
                   "Choose a model on this computer, or try again later.",
}
# The settings a company can set that Settings shows as "Set by your company", with the line shown while the
# company's value restricts the person: a switch off, a list that switches something off, a choice below the
# most permissive one (C3b).
_LOCK_WORDS = {
    "models.personal_api_keys": TURNED_OFF["personal"],
    "models.consumer_sign_ins": TURNED_OFF["consumer"],
    "models.local_models": TURNED_OFF["local"],
    "tools.disabled_groups": "Your company switched off some of Row-Bot's tools",
    "tools.disabled_tools": "Your company switched off some single tools",
    "approvals.max_level": "Your company limits how much Row-Bot may do without asking",
    "apps.plugins": "Your company limits which plugins you can add",
    "apps.mcp_servers": "Your company limits which MCP servers you can add",
    "apps.skills_hub": "Your company limits which Skills Hub skills you can add",
    "apps.channels": "Your company limits which chat channels you can use",
    "data.chat_export": "Your company turned off chat export",
    "data.share_to_company": "Your company turned off Share to company",
}
_SWITCH = {"personal": "personal_api_keys", "consumer": "consumer_sign_ins", "local": "local_models"}
_CHOICE_WORDS = {"block": "Block", "approve": "Ask", "auto": "Auto", "full": "Hands-off", "none": "None",
                 "allow-list": "Only the allowed ones", "any": "Any"}

# Seconds since the epoch, and seconds on a clock that only moves forward; tests move them.
now = time.time
monotonic = time.monotonic


class ModelTurnedOff(Exception):
    """The company's policy turns this model off; the message says so and what to do."""


@dataclass(frozen=True)
class Status:
    state: State
    reason: Reason | None = None
    # current or cached: when the policy was last confirmed; locked_down: since when (None when unknown).
    since: float | None = None
    # The policy in force: the company's, or lockdown's; None when not joined.
    policy: Policy | None = None


@dataclass(frozen=True)
class _Cache:
    origin: str
    device_id: str
    etag: str | None
    confirmed_at: float
    failed_at: float | None
    policy: Policy
    # The lockdown in force: {"since", "reason", "offline_days"}; it ends at the next successful fetch.
    lockdown: Mapping[str, Any] | None = None
    # Lockdowns that ended, oldest first, for C4: {"started_at", "ended_at", "reason", "offline_days"}.
    lockdowns: tuple[Mapping[str, Any], ...] = field(default=())


_LOCK = threading.RLock()
_FETCH = threading.Lock()
# (path, mtime, size) of the cache file last read, and what it held.
_memo: tuple[tuple[str, int, int], _Cache | None] | None = None
# When this process last asked the server (monotonic), and the background refresh running, if any.
_attempted_at: float | None = None
_reading: threading.Thread | None = None
_unknown_logged: set[str] = set()


def cache_path():
    return get_row_bot_data_dir(create=False) / CACHE_FILE


# -- the cache ----------------------------------------------------------------------------------------

def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _episode(value: Any, keys: tuple[str, ...]) -> bool:
    return (isinstance(value, dict) and set(value) == {*keys, "reason", "offline_days"}
            and all(_number(value[key]) for key in keys) and value["reason"] in LOCKDOWN_REASONS
            and type(value["offline_days"]) is int and 0 <= value["offline_days"] <= 365)


def _parse_cache(data: Any) -> _Cache:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unknown cache")
    etag, failed, lockdown, ended = data["etag"], data["failed_at"], data["lockdown"], data["lockdowns"]
    if (not isinstance(data["origin"], str) or not isinstance(data["device_id"], str)
            or not (etag is None or (isinstance(etag, str) and 0 < len(etag) <= MAX_ETAG and etag.isprintable()))
            or not _number(data["confirmed_at"]) or data["confirmed_at"] > now() + CLOCK_SLACK
            or not (failed is None or _number(failed))
            or not (lockdown is None or _episode(lockdown, ("since",)))
            or not isinstance(ended, list) or len(ended) > MAX_LOCKDOWNS
            or not all(_episode(item, ("started_at", "ended_at")) for item in ended)):
        raise ValueError("unexpected field")
    policy = shared.resolve(shared.parse_policy(data["policy"]))
    return _Cache(data["origin"], data["device_id"], etag, float(data["confirmed_at"]),
                  None if failed is None else float(failed), policy, lockdown, tuple(ended))


def _read_cache(record: store.Record) -> _Cache | None:
    """The cached policy of ``record``'s company and device, or None (none, or a file that doesn't check out)."""
    global _memo
    path = cache_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if _memo is not None and _memo[0] == key:
            cache = _memo[1]
        else:
            try:
                if stat.st_size > MAX_CACHE_BYTES:
                    raise ValueError("too large")
                cache = _parse_cache(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, ValueError, KeyError, TypeError, PolicyError):
                logger.warning("The cached company policy could not be read; it is fetched again")
                cache = None
            _memo = (key, cache)
    if cache is None or (cache.origin, cache.device_id) != (record.origin, record.device_id):
        return None
    return cache


def _write_cache(cache: _Cache) -> bool:
    """Replace the cache in one step (under _LOCK), and remember what it now holds. A folder that can't be
    written is logged: what is in force is then worked out again from what the file still holds."""
    global _memo
    path = cache_path()
    data = {"schema_version": 1, "origin": cache.origin, "device_id": cache.device_id, "etag": cache.etag,
            "confirmed_at": cache.confirmed_at, "failed_at": cache.failed_at,
            "policy": shared.policy_to_dict(cache.policy), "lockdown": cache.lockdown,
            "lockdowns": list(cache.lockdowns)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(partial, path)
        stat = path.stat()
    except OSError as error:
        logger.warning("The company policy couldn't be saved (%s)", type(error).__name__)
        return False
    _memo = ((str(path), stat.st_mtime_ns, stat.st_size), cache)
    return True


def _changed() -> None:
    """The policy in force changed: model lists read again, and chat models already made are made afresh (so
    one the policy now turns off is refused)."""
    from row_bot.application.live_updates import publish

    # Only caches that exist: a module not imported yet holds nothing to drop.
    agent, models = sys.modules.get("row_bot.agent"), sys.modules.get("row_bot.models")
    if agent is not None:
        agent.clear_agent_cache()
    if models is not None:
        models.clear_llm_cache()
    ways = sys.modules.get("row_bot.integrations.builtin")
    if ways is not None:
        ways.changed()  # Apps shows a channel the company now holds (or lets go) as it is (C3b).
    # What the company now holds stops (C3b): its running channels and MCP servers (the servers in the
    # background: each stop waits for its program). Only where they run: a module not imported runs nothing.
    channels, mcp = sys.modules.get("row_bot.channels.registry"), sys.modules.get("row_bot.mcp_client.runtime")
    if channels is not None:
        try:
            channels.stop_held_channels()
        except Exception as error:  # noqa: BLE001 - each held channel's sends are refused all the same
            logger.warning("Channels the company holds couldn't be stopped (%s)", type(error).__name__)
    if mcp is not None and mcp.held_servers():
        threading.Thread(target=_stop_mcp, args=(mcp,), daemon=True, name="row-bot-company-mcp").start()
    publish("models")


def _stop_mcp(mcp: Any) -> None:
    try:
        mcp.stop_held_servers()
    except Exception as error:  # noqa: BLE001 - each held server's calls are refused all the same
        logger.warning("MCP servers the company holds couldn't be stopped (%s)", type(error).__name__)


# -- the state ----------------------------------------------------------------------------------------

def _evaluate(record: store.Record, cache: _Cache | None, at: float) -> Status:
    if cache is None:
        return Status("locked_down", "disconnected" if record.disconnected else "not_confirmed", None,
                      lockdown_policy())
    if cache.lockdown is not None:
        return Status("locked_down", cache.lockdown["reason"], cache.lockdown["since"], lockdown_policy())
    if record.disconnected:
        return Status("locked_down", "disconnected", at, lockdown_policy())
    days = cache.policy.offline.max_days
    if days == 0 and cache.failed_at is not None:
        return Status("locked_down", "lease_expired", cache.failed_at, lockdown_policy())
    if days and at - cache.confirmed_at > days * DAY:
        return Status("locked_down", "lease_expired", cache.confirmed_at + days * DAY, lockdown_policy())
    if cache.failed_at is not None:
        return Status("cached", "unreachable", cache.confirmed_at, cache.policy)
    return Status("current", None, cache.confirmed_at, cache.policy)


def state() -> Status:
    """Whether a company policy is in force, which, and why. Contacts nothing.

    A lockdown seen here for the first time is recorded (its start, for C4) and the model lists are told."""
    record = store.read_record()
    if record is None:
        return Status("not_joined")
    with _LOCK:
        cache = _read_cache(record)
        found = _evaluate(record, cache, now())
        if cache is None or cache.lockdown is not None or found.state != "locked_down":
            return found
        if not _write_cache(replace(cache, lockdown={"since": found.since, "reason": found.reason,
                                                     "offline_days": cache.policy.offline.max_days})):
            return found
    logger.info("Row-Bot locked down to models on this computer (%s)", found.reason)
    _changed()
    return found


def current() -> Policy | None:
    """The company policy in force (lockdown's while locked down), or None when no company is joined."""
    return state().policy


def lockdowns() -> list[dict[str, Any]]:
    """The lockdowns that ended, oldest first, and the one in force (``ended_at`` None), for C4."""
    record = store.read_record()
    cache = _read_cache(record) if record is not None else None
    if cache is None:
        return []
    ended = [dict(item) for item in cache.lockdowns]
    if cache.lockdown is not None:
        ended.append({"started_at": cache.lockdown["since"], "ended_at": None, "reason": cache.lockdown["reason"],
                      "offline_days": cache.lockdown["offline_days"]})
    return ended


def lockdowns_sent(started: Iterable[float]) -> None:
    """C4 has queued these ended lockdowns (by when each began) for the company: they leave ``lockdowns()``."""
    done = set(started)
    record = store.read_record()
    if record is None or not done:
        return
    with _LOCK:
        cache = _read_cache(record)
        if cache is None:
            return
        kept = tuple(item for item in cache.lockdowns if item["started_at"] not in done)
        if kept != cache.lockdowns:
            _write_cache(replace(cache, lockdowns=kept))


# -- fetching -----------------------------------------------------------------------------------------

def _known(data: Any) -> Any:
    """``data`` without the sections and settings this app doesn't know (each name logged once)."""
    if not isinstance(data, dict):
        return data
    kept: dict[str, Any] = {}
    unknown: list[str] = []
    for section, values in data.items():
        if section == "version":
            kept[section] = values
        elif section not in shared.SECTIONS:
            unknown.append(section)
        elif isinstance(values, dict):
            names = shared.specs(section)
            kept[section] = {name: value for name, value in values.items() if name in names}
            unknown += [f"{section}.{name}" for name in values if name not in names]
        else:
            kept[section] = values  # The parser says what is wrong with it.
    new = sorted(set(unknown) - _unknown_logged)
    if new:
        _unknown_logged.update(new)
        logger.info("The company policy has settings this Row-Bot doesn't know; they are left out: %s",
                    ", ".join(name[:80] for name in new[:20]))
    return kept


def _fetched(response: Any, cache: _Cache | None) -> tuple[Policy, str | None]:
    from row_bot.company import client

    if response.status_code == 304 and cache is not None:
        return cache.policy, cache.etag
    if response.status_code != 200:
        raise client.Unreachable(str(response.status_code))
    try:
        policy = shared.resolve(shared.parse_policy(_known(shared.loads(response.content))))
    except PolicyError as refused:
        logger.warning("The company policy couldn't be read (%s); the cached one is kept", refused.detail[:200])
        raise client.Unreachable("invalid policy") from None
    etag = response.headers.get("ETag")
    return policy, etag if etag and len(etag) <= MAX_ETAG and etag.isprintable() else None


def refresh() -> Status:
    """Ask the company for the policy now, keep it, and say what is in force.

    Not joined or disconnected: nothing is asked. A failure keeps the cached policy (and counts for the lease)."""
    global _attempted_at
    with _FETCH:
        record = store.read_record()
        if record is None or record.disconnected:
            return state()
        _attempted_at = monotonic()
        from row_bot.company import client

        with _LOCK:
            cache = _read_cache(record)
        try:
            response = client.call(record.origin, "GET", EFFECTIVE_PATH,
                                   headers={"If-None-Match": cache.etag} if cache and cache.etag else None)
            policy, etag = _fetched(response, cache)
        except client.Disconnected:
            return state()
        except (client.Unreachable, store.StorageUnavailable) as error:
            logger.info("The company policy couldn't be refreshed (%s)", type(error).__name__)
            with _LOCK:
                latest = _read_cache(record)  # A lockdown may have been recorded meanwhile.
                if latest is not None and latest.failed_at is None:
                    _write_cache(replace(latest, failed_at=now()))
            return state()
        with _LOCK:
            still = store.read_record()
            if still is None or (still.origin, still.device_id) != (record.origin, record.device_id):
                return state()  # Left (or joined elsewhere) while it was asked: nothing is kept.
            latest = _read_cache(record)
            at = now()
            ended = latest.lockdowns if latest is not None else ()
            if latest is not None and latest.lockdown is not None:
                ended = (*ended, {"started_at": latest.lockdown["since"], "ended_at": at,
                                  "reason": latest.lockdown["reason"],
                                  "offline_days": latest.lockdown["offline_days"]})[-MAX_LOCKDOWNS:]
                logger.info("Row-Bot reached its company again; the lockdown ended")
            changed = latest is None or latest.policy != policy or latest.lockdown is not None
            _write_cache(_Cache(record.origin, record.device_id, etag, at, None, policy, None, ended))
    if changed:
        _changed()
    from row_bot.company import audit

    audit.send_soon()  # The company answered: what's waiting for it (C4), lockdowns that ended included.
    return state()


def _refresh_in_background() -> None:
    global _reading
    try:
        refresh()
    except Exception as error:  # noqa: BLE001 - a later use asks again
        logger.info("The company policy couldn't be refreshed (%s)", type(error).__name__)
    finally:
        with _LOCK:
            _reading = None


def refresh_soon() -> None:
    """The app is in use: refresh in the background when this process hasn't asked for REFRESH_SECONDS."""
    global _attempted_at, _reading
    record = store.read_record()
    if record is None or record.disconnected:
        return
    with _LOCK:
        due = _attempted_at is None or monotonic() - _attempted_at >= REFRESH_SECONDS
        if not due or _reading is not None:
            return
        _attempted_at = monotonic()
        _reading = thread = threading.Thread(target=_refresh_in_background, daemon=True,
                                             name="row-bot-company-policy")
    thread.start()


def settle(timeout: float = 5.0) -> None:
    """Wait for a background refresh to end (a test's way to see its outcome)."""
    with _LOCK:
        thread = _reading
    if thread is not None:
        thread.join(timeout)


def forget() -> None:
    """Leave: the company's policy goes with it."""
    global _attempted_at
    with _LOCK:
        existed = cache_path().exists()
        cache_path().unlink(missing_ok=True)
        _attempted_at = None
    if existed:
        _changed()


# -- what the policy allows ---------------------------------------------------------------------------

def _loopback(url: Any) -> bool:
    try:
        host = (urlsplit(str(url or "")).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def provider_kind(provider_id: str) -> Kind:
    """What a provider counts as for the policy: company, local, consumer or personal (the module's notes)."""
    from row_bot.providers.catalog import provider_billing
    from row_bot.providers.custom import get_custom_endpoint, is_custom_openai_provider

    if provider_id == "company":
        return "company"
    if provider_id == "ollama":
        return "local"
    if is_custom_openai_provider(provider_id):
        endpoint = get_custom_endpoint(provider_id) or {}
        return "local" if endpoint.get("managed") or _loopback(endpoint.get("base_url")) else "personal"
    return "consumer" if provider_billing(provider_id) == "subscription" else "personal"


def _allows(policy: Policy, kind: Kind) -> bool:
    return kind == "company" or bool(getattr(policy.models, _SWITCH[kind]))


def _allows_company_model(policy: Policy, model_id: str) -> bool:
    return ALL_MODELS in policy.models.allowed or model_id in policy.models.allowed


def allows_provider(provider_id: str, status: Status | None = None) -> bool:
    """Whether this provider's models may be listed and used (always, with no company joined)."""
    status = status or state()
    return status.policy is None or _allows(status.policy, provider_kind(provider_id))


def allows_company_model(model_id: str, status: Status | None = None) -> bool:
    """Whether the company's model ``model_id`` is in ``models.allowed`` (the server filters its catalog too, but
    a catalog read before a newer policy obeys the newer one)."""
    status = status or state()
    return status.policy is None or _allows_company_model(status.policy, model_id)


def keep_allowed(rows: dict[str, Any], status: Status | None = None) -> None:
    """Take the rows of every provider the policy turns off out of the app's model rows (keyed by ref)."""
    status = status or state()
    if status.policy is None:
        return
    kinds: dict[str, Kind] = {}
    for ref in [ref for ref, row in rows.items() if row.provider_id != "company"]:
        provider_id = rows[ref].provider_id
        if provider_id not in kinds:
            kinds[provider_id] = provider_kind(provider_id)
        if not _allows(status.policy, kinds[provider_id]):
            del rows[ref]


def _why(provider_id: str, model_id: str, status: Status) -> str | None:
    if status.policy is None:
        return None
    kind = provider_kind(provider_id)
    if kind == "company":
        if status.state == "locked_down":
            # Disconnected: the company's models say how to sign in again themselves.
            return None if status.reason == "disconnected" else "locked_down"
        return None if _allows_company_model(status.policy, model_id) else "not_allowed"
    return None if _allows(status.policy, kind) else kind


def turned_off(provider_id: str, model_id: str) -> str | None:
    """Why the company's policy turns this model off, in a few words; None when it doesn't (or no company)."""
    why = _why(provider_id, model_id, state())
    return TURNED_OFF[why] if why else None


def refuse(provider_id: str, model_id: str) -> None:
    """Raise ModelTurnedOff when the company's policy turns this model off. Asked as a chat model is made, which
    is also when the app is in use (a background refresh may start)."""
    status = state()
    if status.policy is None:
        return
    refresh_soon()
    why = _why(provider_id, model_id, status)
    if why:
        logger.info("A model the company's policy turns off was refused (%s)", why)
        raise ModelTurnedOff(REFUSED[why])


# -- in plain words, for Settings ---------------------------------------------------------------------

def _value_words(spec: shared.Spec, value: Any) -> str:
    if spec.kind == "bool":
        return "On" if value else "Off"
    if spec.kind == "int":
        return f"{value} day{'' if value == 1 else 's'}"
    if spec.kind == "enum":
        return _CHOICE_WORDS.get(value, str(value))
    if ALL_MODELS in value:
        return "All"
    entries = sorted(value)
    if not entries:
        return "None"
    shown = ", ".join(entries[:8])
    return shown if len(entries) <= 8 else f"{shown} and {len(entries) - 8} more"


def rules(policy: Policy | None) -> list[dict[str, str]]:
    """Every setting of ``policy`` in plain words, from the shared model's titles and help."""
    if policy is None:
        return []
    found = []
    for section in shared.SECTIONS:
        values = getattr(policy, section)
        for name, spec in shared.specs(section).items():
            found.append({"setting": f"{section}.{name}", "title": spec.title, "help": spec.help,
                          "value": _value_words(spec, getattr(values, name))})
    return found


def locked(policy: Policy | None) -> list[dict[str, str]]:
    """The settings ``policy`` restricts that Settings shows as "Set by your company", with their line."""
    if policy is None:
        return []
    found = []
    for setting, words in _LOCK_WORDS.items():
        section, name = setting.split(".", 1)
        value = getattr(getattr(policy, section), name)
        spec = shared.specs(section)[name]
        restricts = (value is False if spec.kind == "bool" else bool(value) if spec.kind == "list"
                     else spec.kind == "enum" and value != spec.choices[-1])
        if restricts:
            found.append({"setting": setting, "title": spec.title, "value": _value_words(spec, value)[:256],
                          "words": words})
    return found


def iso(seconds: float | None) -> str | None:
    return None if seconds is None else datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def default_model_words(provider_id: str, model_id: str, model_name: str) -> str | None:
    """Settings › Models, when the policy takes the person's chosen model away: why, and what to do. Nothing is
    chosen for them; the company's suggestion is named when it has one."""
    reason = turned_off(provider_id, model_id)
    if reason is None:
        return None
    from row_bot.company import provider

    suggested = provider.suggestion(store.read_record())
    if suggested is not None and not allows_company_model(suggested.model_id):
        suggested = None
    other = f"another model, such as {suggested.name}, which your company suggests" if suggested else "another model"
    return f"{reason}, so {model_name} can't be used. Choose {other}."


__all__ = [
    "LOCKDOWN_REASONS", "REFRESH_SECONDS", "REFUSED", "TURNED_OFF", "ModelTurnedOff", "Status",
    "allows_company_model", "allows_provider", "current", "default_model_words", "forget", "iso", "keep_allowed",
    "locked", "lockdowns", "provider_kind", "refresh", "refresh_soon", "refuse", "rules", "settle", "state",
    "turned_off",
]
