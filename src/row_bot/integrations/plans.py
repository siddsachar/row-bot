"""Install plans: what it takes to make one entry usable, computed and run by the server.

A plan is a list of typed steps. Every step type is part of the contract now; a
step the owners cannot perform yet is ``unsupported`` with its reason, and such
a plan cannot start. Starting needs the consent token issued with the plan,
bound to its digest, so nothing runs before consent and a changed plan needs a
new one.

The runner drives the existing owner commands, whose admissions, idempotency
keys and revision checks are unchanged. Each owner command is recorded before it
is sent, so running a step again re-sends that same command and the owner can
only replay or reconcile it. A new command is sent only with a review computed
in the same run from the consented plan.

A plan pauses only for a browser sign-in, a missing input, access to newly
discovered tools, a changed plan, or ``resume`` (a background owner step has
finished, or a step stopped before sending anything). Reading a plan observes
owner receipts; it never sends a command. Over HTTP a plan runs in the
background and reports each step as it goes; a pause left for ``EXPIRES``
seconds expires, keeping what was done.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import copy
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any
from uuid import uuid4

from row_bot.integrations import apps, facts, presets, safe, sources
from row_bot.integrations.safe import public_url as public_link

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()  # Guards the sets below; held only briefly.
_PLAN_LOCKS: dict[str, threading.RLock] = {}
_RUNNING: set[str] = set()
_CANCELLED: set[str] = set()
EXPIRES = 30 * 60
INTENTS = ("connect", "add", "turn_on", "fix", "access", "turn_off", "remove", "update")
_DONE = {"turn_off": "Turned off.", "remove": "Removed.", "update": "Updated."}
_MESSAGES = {
    "plan_changed": "Something changed since you agreed. Review it again.",
    "plan_cancelled": "Stopped. Anything already done is kept.",
    "plan_expired": "This setup waited too long, so Row-Bot stopped it. Anything already done is kept.",
    "revision_conflict": "The settings changed while setting up. Start again.",
    "mcp_connection_failed": "The connection test failed. Check the details and try again.",
    "change_unconfirmed": "Your last change didn't finish. Retry to check it again.",
    "skill_preview_expired": "The skill check expired. Start again.",
    "package_preview_expired": "The package check expired. Start again.",
    "owner_local_only": "Adding packages works only in Row-Bot on this computer.",
}


class PlanError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code, self.message = code, message


@dataclass
class Context:
    """Who runs a plan, the request's inputs, and the owner services a route supplies."""
    owner_id: str
    mcp_owner_id: str
    validate: Callable[[], None]
    local_owner: bool = False
    redirect_uri: str = ""
    runtimes: Any = None
    read_policy: Callable[[str], dict] | None = None
    inputs: dict = field(default_factory=dict)
    tools_digest: str = ""
    review_digest: str = ""  # What the person reviewed in place (a package lock): a step continues only on it.


def _now() -> float:
    return time.time()


def _step(kind: str, state: str = "pending", title: str = "", message: str = "", **detail) -> dict:
    return {"id": kind, "type": kind, "state": state, "title": title, "message": message[:512], **detail}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


# --- Plan computation --------------------------------------------------------

def _mcp_steps(row: dict, cfg: dict, intent: str) -> tuple[list[dict], dict, dict]:
    name = (row["app"] or {}).get("name") or row["name"]
    requirements = facts._requirements(cfg)
    setup = facts.mcp_setup({"requirements": requirements}, cfg)
    codes = {b["code"] for b in row.get("blockers", [])}
    signed_in = setup["credential_configured"] and "expired" not in codes
    hosted = cfg.get("transport", "stdio") != "stdio"
    app = apps.match(facts._mcp_refs(cfg))
    steps = [_step("consent", title="Before you connect")]
    from row_bot.mcp_client import packages
    package = packages.kind(cfg) if not hosted else None
    for requirement in [] if hosted else requirements:
        if requirement["available"]:
            continue
        supported = requirement["id"] in {"node", "uv"} and requirement["installable"]
        steps.append(_step("runtime", "pending" if supported else "unsupported", "Set up " + requirement["label"],
                           "" if supported else f"Install {requirement['label']}, then try again.",
                           runtime={"id": requirement["id"], "label": requirement["label"]}))
    if setup["package_required"]:
        found = {"npm": ("npm_package", "npm package"), "pypi": ("pypi_package", "Python package"), "oci": ("oci_image", "container image"),
                 "mcpb": ("mcpb", "bundle")}
        steps.append(_step("runtime", title="Prepare the " + found[package][1],
                           runtime={"id": found[package][0], "label": found[package][1]}))
    for index, step in enumerate(s for s in steps if s["type"] == "runtime"):
        step["id"] = f"runtime{index or ''}"
    fields = _fields(cfg, setup, app)  # After anything to install, so its review comes before any key is typed.
    if fields:
        done = not (setup["auth_mode"] == "api_key" and not signed_in) and not setup["inputs_missing"]
        steps.append(_step("inputs", "done" if done else "pending",
                           "Add your key" if all(f["secret"] for f in fields) else "Add your settings", inputs=fields))
    steps += _local_app_step(app or apps.catalog()[0].get((row["app"] or {}).get("id", "")))
    method = "oauth_client" if (cfg.get("source") or {}).get("oauth_client") == "required" else "oauth_dcr"
    if setup["auth_mode"] == "oauth":
        steps.append(_step("sign_in", "done" if signed_in else "pending", "Sign in to " + name,
                           sign_in={"method": method, "authorization_url": None}))
    elif setup["auth_mode"] == "unsupported":
        steps.append(_step("sign_in", "unsupported", "Sign in to " + name, "Row-Bot can't sign in to this app yet.",
                           sign_in={"method": method, "authorization_url": None}))
    elif hosted and setup["auth_mode"] == "unknown" and not signed_in:
        # Whether it asks for a sign-in is found out when its connection is first checked.
        asks = bool((cfg.get("source") or {}).get("requires_auth"))
        steps.append(_step("sign_in", "pending" if asks else "skipped", "Sign in to " + name,
                           "" if asks else f"Only if {name} asks you to sign in.",
                           sign_in={"method": method, "authorization_url": None}))
    installed = row["lifecycle"] != "available"
    accepted = setup["catalog_accepted"] and not codes & {"tools_changed", "tools_not_accepted"}
    retest = bool(codes & {"connection_failed", "expired", "tools_changed", "tools_not_accepted", "sign_in_required", "key_required"})
    checked = installed and accepted and not (intent == "fix" and retest)
    steps.append(_step("test", "done" if checked or intent == "access" else "pending", "Check the connection"))
    steps.append(_step("access", "done" if checked and intent != "access" else "pending", f"Choose what {name} can do",
                       access={"preset": presets.current(cfg.get("tools") or {}) if accepted else presets.DEFAULT,
                               "tools": [], "tools_digest": "", "note": ""}))
    steps.append(_step("enable", "pending" if intent == "access" else "done" if row["lifecycle"] == "installed"
                       and row["readiness"] == "ready" else "pending", "Save access" if intent == "access" else "Turn on " + name))
    if intent == "access":  # Saving access changes access only; setup still to do needs its own consent.
        unfinished = any(s["state"] == "pending" for s in steps if s["type"] not in {"consent", "test", "access", "enable"})
        steps = [s for s in steps if s["type"] in {"consent", "test", "access", "enable"}]
        if unfinished:
            steps[2].update(state="unsupported", message=f"Finish setting up {name} first.")
    # Turning a connection on also turns on MCP when it is off, which can wake other connections.
    standalone = row.get("target") in (None, {"kind": "standalone"})
    consent = {"destinations": [setup["destination"]] if hosted else [], "runs_locally": not hosted,
               "downloads": [s["runtime"]["label"] for s in steps if s["type"] == "runtime" and s["state"] == "pending"],
               "access_preset": presets.DEFAULT, "turns_on_mcp": intent != "access" and standalone and not _mcp_on(),
               "cleanup": False}
    declaration = {"transport": cfg.get("transport"), "url": cfg.get("url", ""), "command": cfg.get("command", ""),
                   "args": cfg.get("args", []), "headers": sorted(cfg.get("headers") or {}), "env": sorted(cfg.get("env") or {}),
                   "auth": setup["auth_mode"], "bindings": setup["bindings"], "source": cfg.get("source") or {}}
    return steps, consent, declaration


def _local_app_step(app: apps.App | None) -> list[dict]:
    """For an app that works with a desktop app on this computer: check that it is open (a loopback port
    or a process name, never anything off this computer), with "Open it, then check again" until it is."""
    if app is None or not app.local_check:
        return []
    return [_step("local_app_check", title="Open " + app.name,
                  local_app={"label": app.local_app or app.name, "help_url": app.docs_url})]


def local_app_open(check: dict) -> bool:
    """Whether the app is open: something listens on its loopback port, or a process has its exact name."""
    import socket
    if "port" in check:
        try:
            with socket.create_connection(("127.0.0.1", int(check["port"])), timeout=1):
                return True
        except OSError:
            return False
    import psutil
    wanted = str(check.get("process", "")).lower()
    for process in psutil.process_iter(["name"]):
        if str(process.info.get("name") or "").lower().removesuffix(".exe") == wanted:
            return True
    return False


def _local_app(ctx: Context, record: dict, step: dict) -> str:
    app = apps.catalog()[0].get(record.get("app_id", ""))
    if app is None or not app.local_check or local_app_open(dict(app.local_check)):
        step["message"] = ""
        return "done"
    step["message"] = f"Open {step['local_app']['label']}, then check again."[:512]
    return "resume"


def _fields(cfg: dict, setup: dict, app: apps.App | None) -> list[dict]:
    """What the inputs step asks for: a recipe's keys (header or variable bindings) and its declared
    inputs, with "Where do I get this?" for keys when the app's catalog entry knows where."""
    saved = cfg.get("input_values") or {}
    help_url = app.key_url if app else ""
    found = [{"key": b["key"], "label": "API key" if b["key"] in {"api_key", "token"} else b["name"], "secret": True,
              "required": True, "target": b["kind"], "name": b["name"], "template": b.get("prefix", "") + "{value}",
              "default": "", "choices": [], "help_url": help_url, "description": "", "format": "string"}
             for b in setup["bindings"] if b["kind"] != "input"]
    for item in setup["inputs"]:
        found.append({"key": item["key"], "label": item["label"], "secret": item["secret"], "required": item["required"],
                      "target": item["target"], "name": item["name"], "template": "", "choices": item["choices"],
                      "default": "" if item["secret"] else str(saved.get(item["key"]) or item["default"]),
                      "help_url": item.get("help_url") or (help_url if item["secret"] else ""),
                      "description": sources.plain_text(item.get("description", ""), 512), "format": item.get("format", "string")})
    return found[:32]


def _hub_record(row: dict):
    from row_bot.skills_hub.provenance import get_record
    return get_record(row["owner_ref"]) if row["kind"] == "skill" and row["parent_id"] is None else None


def changeable(row: dict, intent: str) -> bool:
    """Whether an installed item supports Turn off, Remove or Check for updates."""
    if row["lifecycle"] == "available":
        return False
    if intent == "turn_off":
        return row["lifecycle"] == "installed" and (row["parent_id"] is None or row["kind"] == "mcp")
    if intent == "remove":
        return row["parent_id"] is None and (row["kind"] != "skill" or row["source"] == "user" or _hub_record(row) is not None)
    record = _hub_record(row)
    return row["lifecycle"] != "data_retained" and (record is not None and record.source != "upload" if row["kind"] == "skill"
                                                     else row["kind"] == "plugin" and row["source_url"].startswith("https://github.com/"))


def _checkable(row: dict) -> bool:
    """An added package whose settings are complete: its local check and turning it on can run as one plan."""
    from row_bot.application.plugin_commands import read_plugin_detail
    try:
        capabilities = read_plugin_detail(row["owner_ref"], validate=lambda: None)["capabilities"]
    except Exception:
        return False
    return row["parent_id"] is None and bool((capabilities.get("test") or {}).get("available"))


def compute(row: dict, reference: dict, *, intent: str = "", cleanup: bool = False) -> dict | None:
    """The plan for one entry and intent, or None when nothing needs doing.

    ``reference`` carries what the owners need: ``cfg`` (an installed MCP
    configuration), ``entry`` (a catalog MCP record), or a skill or package
    catalog reference. ``cleanup`` (Remove only) also deletes saved keys and data.
    """
    kind, available, action = row["kind"], row["lifecycle"] == "available", row["next_action"]["kind"]
    intent = intent or ("connect" if available and kind == "mcp" else "add" if available else
                        "turn_on" if action == "turn_on" else "fix" if action not in {"try", "none", "delete_data"} else "")
    if (not intent or intent not in INTENTS or (intent == "access" and (kind != "mcp" or available))
            or (intent in _DONE and not changeable(row, intent))):
        return None
    name = row["name"] if kind == "skill" else (row["app"] or {}).get("name") or row["name"]
    consent = {"destinations": [], "runs_locally": True, "downloads": [], "access_preset": presets.DEFAULT,
               "turns_on_mcp": False, "cleanup": False}
    declaration: dict = {}
    if intent in _DONE:
        title = {"turn_off": "Turn off ", "remove": "Remove ", "update": "Update "}[intent] + name
        steps = [_step("consent", title=title), _step("enable", title=title)]
        if intent == "update":
            steps.insert(1, _step("test", title="Check for a newer version"))
            consent["downloads"] = [row["source_url"] or name]
        # Removing what a package left behind is deleting its data, so that is what is agreed.
        consent["cleanup"] = intent == "remove" and (bool(cleanup) or row["lifecycle"] == "data_retained")
        declaration = {"revision": row["revision"], "lifecycle": row["lifecycle"]}
    elif reference.get("kind") == "account":  # Connected from Accounts or Channels until they join Apps.
        steps = [_step("consent", title="Before you connect"), _step("enable", "unsupported", "Connect " + name,
                 next((b["message"] for b in row["blockers"] if b["code"] == "unsupported"), ""))]
    elif kind == "mcp" and reference.get("kind") == "hermes_mcp":
        # The recipe is read at its pin once agreed, shown in place, and only then saved; its own steps follow.
        steps = [_step("consent", title="Before you connect"), _step("test", title="Check the connection"),
                 _step("access", title=f"Choose what {name} can do", access={"preset": presets.DEFAULT, "tools": [],
                                                                            "tools_digest": "", "note": ""}),
                 _step("enable", title="Turn on " + name)]
        consent["downloads"] = [f"the {name} recipe from Hermes"]
        declaration = {"recipe": reference.get("name"), "pin": reference.get("pin")}
    elif kind == "mcp":
        from row_bot.mcp_client.marketplace import MarketplaceEntry, entry_to_server_config
        entry = reference.get("entry")
        try:
            cfg = reference.get("cfg") or entry_to_server_config(entry if not isinstance(entry, dict) else MarketplaceEntry(**entry))
            steps, consent, declaration = _mcp_steps(row, cfg, intent)
        except (ValueError, TypeError):  # No launch recipe Row-Bot can express.
            steps = [_step("consent", title="Before you connect"), _step("enable", "unsupported", "Turn on " + name,
                     "Row-Bot can't connect to this one yet.")]
    elif available:
        what = "skill" if kind == "skill" else "package"
        steps = [_step("consent", title="Before you add " + name), _step("test", title=f"Check the {what}"),
                 _step("enable", title="Add and turn on" if kind == "skill" else "Add " + name)]
        if kind == "plugin":  # A package for a desktop app (Blender): its program is open before it is turned on.
            steps[-1:-1] = _local_app_step(apps.catalog()[0].get((row["app"] or {}).get("id", "")))
        consent["downloads"] = [row["source_url"] or name]
        if reference.get("kind") not in {"skill", "plugin"}:
            steps[1].update(state="unsupported", message="Add this one from its marketplace page for now.")
        declaration = {key: reference.get(key) for key in ("reference", "pin", "identity", "revision", "entry_id", "install_ref",
                                                            "link", "upload")}
    elif intent == "fix" and kind == "plugin" and _checkable(row):
        steps = [_step("consent", title="Finish setting up " + name), _step("test", title="Check it on this computer"),
                 _step("enable", title="Turn on " + name)]
    else:
        steps = [_step("consent", title="Turn on " + name), _step("enable", title="Turn on " + name)]
        if intent == "fix":
            steps[1].update(state="unsupported", title="Fix " + name, message="Finish it in its advanced settings for now.")
    listed = next((b["message"] for b in row["blockers"] if b["code"] == "unsupported"), "")
    if available and listed and all(s["state"] != "unsupported" for s in steps):
        steps[-1].update(state="unsupported", message=listed[:512] or "Row-Bot can't add this one yet.")
    unsupported = next((s for s in steps if s["state"] == "unsupported"), None)
    plan = {"schema_version": 1, "plan_id": None, "item_id": row["id"], "app_id": (row["app"] or {}).get("id", ""),
            "installed_id": "", "kind": kind, "name": name, "intent": intent,
            "state": "ready", "pause": None, "message": "", "steps": steps, "consent": consent,
            "supported": unsupported is None, "unsupported_reason": unsupported["message"] if unsupported else ""}
    plan["digest"] = _digest({"item_id": row["id"], "intent": intent, "declaration": declaration, "consent": consent,
                              "steps": [(s["id"], s["state"]) for s in steps]})
    plan["current_step"] = next((s["id"] for s in steps if s["state"] not in {"done", "skipped"}), None)
    return plan


def next_action(plan: dict) -> dict:
    state, pause = plan["state"], plan.get("pause")
    if not plan.get("supported", True):
        kind = "none"
    elif state == "ready":
        kind = plan["intent"] if plan["intent"] in {"connect", "add", "turn_on", *_DONE} else "continue_setup"
    elif state == "paused":
        kind = {"inputs": "add_key", "access": "continue_setup", "digest_changed": "continue_setup", "resume": "continue_setup"}.get(
            pause, "none")
    else:
        kind = "retry" if state in {"failed", "uncertain"} else "try" if state == "completed" and plan["intent"] not in _DONE else "none"
    return {"kind": kind, "label": "Allow" if pause == "access" else facts.LABELS[kind]}


def view(plan: dict) -> dict:
    """The public plan; owner command records and references stay private."""
    value = {key: copy.deepcopy(item) for key, item in plan.items()
             if not key.startswith("_") and key not in {"reference", "owner", "target", "server_id", "preset", "overrides", "app_id"}}
    value["next_action"] = next_action(plan)
    value["consent_token"] = ""
    return value


# --- Persistence -------------------------------------------------------------

def _save(record: dict, *, terminal: bool = False) -> None:
    from row_bot.runtime import admissions
    record["_saved_at"], record["_save_id"] = _now(), uuid4().hex  # The id tells saves apart within one clock tick.
    value = {"command_id": record["plan_id"], "status": "completed" if terminal else "admitting", "plan": record}
    if terminal:
        admissions.complete_command(record["owner"], record["plan_id"], value)
    else:
        admissions.command_progress(record["owner"], record["plan_id"], value)


def _plan_lock(plan_id: str) -> threading.RLock:
    """One plan's lock: a slow read of one plan never holds up another plan."""
    with _LOCK:
        return _PLAN_LOCKS.setdefault(plan_id, threading.RLock())


def _target(ctx: Context, item_id: str) -> str:
    owner = hashlib.sha256(ctx.owner_id.encode()).hexdigest()[:32]
    return f"integrations:plan:{owner}:{item_id}"


def _load(owner_id: str, plan_id: str) -> tuple[dict, bool]:
    from row_bot.runtime import admissions
    metadata = admissions.read_command_metadata(owner_id, plan_id)
    saved = admissions.receipt(owner_id, plan_id) if metadata and metadata["type"] == "integrations.plan" else None
    if not saved or not isinstance(saved.get("plan"), dict):
        raise PlanError("not_found")
    return saved["plan"], metadata["status"] != "completed"


def _overrides(value: dict | None) -> dict:
    value = value or {}
    if len(value) > 256 or any(not isinstance(k, str) or v not in presets.STATES for k, v in value.items()):
        raise PlanError("invalid_access_preset")
    return dict(value)


# --- Runner ------------------------------------------------------------------

def start(ctx: Context, row: dict, reference: dict, *, digest: str, intent: str = "", preset: str = "",
          plan_id: str = "", overrides: dict | None = None, cleanup: bool = False, background: bool = False) -> dict:
    """Admit one consented plan and run it (in the background when asked) to its first pause."""
    from row_bot.runtime import admissions
    open_plan(ctx, row["id"])  # An abandoned pause on this item expires here, freeing it.
    plan = compute(row, reference, intent=intent, cleanup=cleanup)
    if plan is None or plan["digest"] != digest:
        raise PlanError("plan_changed")
    if not plan["supported"]:
        raise PlanError("plan_unsupported")
    if preset and preset not in presets.PRESETS:
        raise PlanError("invalid_access_preset")
    if not ctx.local_owner and ((row["kind"] == "plugin" and plan["intent"] in {"add", "remove", "update"}) or any(
            s["type"] == "runtime" and s["runtime"]["id"] in PACKAGES for s in plan["steps"])):
        raise PlanError("owner_local_only")
    installed = row["lifecycle"] != "available"
    target = row.get("target") if installed else None
    reference = dict(reference)
    if "entry" in reference and not isinstance(reference["entry"], dict):
        reference["entry"] = asdict(reference["entry"])
    if plan["intent"] in _DONE:
        reference.update(source_url=row["source_url"], pin=row["pin"], lifecycle=row["lifecycle"],
                         identity=row["canonical_identity"])
    plan_id = plan_id or str(uuid4())
    record = {**plan, "plan_id": plan_id, "owner": ctx.owner_id, "state": "running", "preset": preset or presets.DEFAULT,
              "overrides": _overrides(overrides), "reference": {k: v for k, v in reference.items() if k != "cfg"},
              "target": None if target in (None, {"kind": "standalone"}) else target,
              "server_id": row["owner_ref"] if row["kind"] == "mcp" and installed else None, "_commands": {}}
    if record["server_id"] and plan["intent"] not in _DONE:
        record["_recipe"] = _recipe(record)
    command = {"command_id": plan_id, "type": "integrations.plan", "item_id": row["id"], "intent": plan["intent"], "digest": digest}
    try:
        prior = admissions.claim_command(ctx.owner_id, plan_id, command, _target(ctx, row["id"]), exclusive_target=True,
                                         initial_result={"command_id": plan_id, "status": "admitting", "plan": record})
    except admissions.AdmissionError as error:
        raise PlanError(str(error)) from None
    if prior is not None:  # This exact plan already finished: report it, never run it again.
        return view(prior["plan"])
    return _launch(ctx, record, background)


def resume(ctx: Context, plan_id: str, *, preset: str = "", overrides: dict | None = None, background: bool = False) -> dict:
    """Continue from the current step: after a sign-in, an input, access, or a finished background step."""
    record, open_ = _load(ctx.owner_id, plan_id)
    if open_ and _stale(record) and plan_id not in _RUNNING:
        _stop(ctx, record, "expired", _MESSAGES["plan_expired"])
        open_ = False
    if not open_:
        raise PlanError("plan_not_resumable")
    if preset:
        if preset not in presets.PRESETS:
            raise PlanError("invalid_access_preset")
        record["preset"] = preset
    if overrides is not None:
        record["overrides"] = _overrides(overrides)
    return _launch(ctx, record, background)


def _stale(record: dict) -> bool:
    """A pause nobody continued within ``EXPIRES``; running and uncertain plans never expire."""
    return record["state"] == "paused" and _now() - record.get("_saved_at", _now()) > EXPIRES


def _stop(ctx: Context, record: dict, state: str, message: str) -> None:
    if record.get("_auth"):
        from row_bot.application.client_mcp_auth import cancel_auth
        try:
            cancel_auth(owner_id=ctx.owner_id, command_id=record["_auth"], validate=ctx.validate)
        except Exception:
            pass  # A finished or expired sign-in has nothing left to cancel.
    record.update(state=state, pause=None, message=message)
    _save(record, terminal=True)


def cancel(ctx: Context, plan_id: str) -> dict:
    """Stop a plan. A pending sign-in is cancelled; finished steps are kept, never undone."""
    record, open_ = _load(ctx.owner_id, plan_id)
    if not open_:
        return view(record)
    with _plan_lock(plan_id), _LOCK:
        if plan_id in _RUNNING:
            _CANCELLED.add(plan_id)
            raise PlanError("operation_pending")
        _RUNNING.add(plan_id)  # No run starts while the plan is being cancelled.
    try:
        record, open_ = _load(ctx.owner_id, plan_id)
        if open_:
            _stop(ctx, record, "cancelled", _MESSAGES["plan_cancelled"])
    finally:
        with _LOCK:
            _RUNNING.discard(plan_id)
    return view(record)


def read_plan(ctx: Context, plan_id: str) -> dict:
    """The plan's state, reconciled from owner receipts. Reading never sends a command."""
    with _plan_lock(plan_id):  # Observing never interleaves with this plan's runner, a cancel or another reader.
        found = _read(ctx, plan_id)
    if found.get("state") in {"completed", "failed", "cancelled", "expired"}:
        with _LOCK:  # Nothing runs a finished plan again: its lock need not outlive it.
            if plan_id not in _RUNNING:
                _PLAN_LOCKS.pop(plan_id, None)
    return found


def _read(ctx: Context, plan_id: str) -> dict:
    record, open_ = _load(ctx.owner_id, plan_id)
    if open_ and plan_id not in _RUNNING and _stale(record):
        _stop(ctx, record, "expired", _MESSAGES["plan_expired"])
    elif open_ and plan_id not in _RUNNING and record["state"] in {"running", "paused", "uncertain"}:
        before, save_id = json.dumps(record, sort_keys=True), record.get("_save_id")
        step = next((s for s in record["steps"] if s["id"] == record.get("current_step")), None)
        seen = ""
        if step is not None:
            try:
                seen = _OBSERVERS.get(step["type"], _observe_commands)(ctx, record, step)
            except Exception:
                seen = "uncertain"
        if seen == "resume":
            record.update(state="paused", pause="resume", message="")
        elif seen == "failed":
            step["state"] = "failed"
            record.update(state="failed", pause=None, message=step["message"] or "This step could not finish.")
        elif seen == "uncertain" and record["state"] != "uncertain":
            record.update(state="uncertain", pause=None, message=_MESSAGES["change_unconfirmed"])
        if seen == "failed" or json.dumps(record, sort_keys=True) != before:
            latest = _load(ctx.owner_id, plan_id)[0]
            if latest.get("_save_id") != save_id:  # The plan moved on meanwhile: report that, never overwrite it.
                return view(latest)
            _save(record, terminal=seen == "failed")
    return view(record)


def open_plan(ctx: Context, item_id: str) -> dict | None:
    """This owner's unfinished plan for an item, so a client can always find, continue or cancel it."""
    from row_bot.runtime import admissions
    pending = admissions.read_unfinished_target_commands(_target(ctx, item_id))
    mine = next((command for command in pending["items"] if command["owner_id"] == ctx.owner_id), None)
    found = read_plan(ctx, mine["command_id"]) if mine else None
    return None if found is None or found["state"] == "expired" else found


def _spawn(work: Callable[[], None]) -> None:
    """The background seam; tests replace it to run a plan at a chosen moment."""
    threading.Thread(target=work, daemon=True, name="integration-plan").start()


def _launch(ctx: Context, record: dict, background: bool) -> dict:
    """Reserve the plan, then run it here or on a background thread; the client polls ``read_plan``."""
    plan_id = record["plan_id"]
    with _plan_lock(plan_id), _LOCK:
        if plan_id in _RUNNING:
            raise PlanError("operation_pending")
        _RUNNING.add(plan_id)
    if not background:
        return _run(ctx, record)
    record.update(state="running", pause=None, message="")
    try:
        _save(record)
    except Exception:
        with _LOCK:
            _RUNNING.discard(plan_id)
        raise

    def work() -> None:
        try:
            _run(ctx, record)
        except PlanError as error:  # Cancelled or finished meanwhile; its record says so.
            logger.debug("Integration plan %s stopped: %s", plan_id, error.code)
    _spawn(work)
    return read_plan(ctx, plan_id)


def _run(ctx: Context, record: dict) -> dict:
    """Run a reserved plan from its current step until it pauses, finishes or fails."""
    plan_id = record["plan_id"]
    try:
        still_open = _load(record["owner"], plan_id)[1]  # A cancel may have finished since this record was read.
    except PlanError:
        still_open = False
    if not still_open:
        with _LOCK:
            _RUNNING.discard(plan_id)
        raise PlanError("plan_not_resumable")
    base = ctx.validate

    def validate() -> None:
        base()
        if plan_id in _CANCELLED:
            raise PlanError("plan_cancelled")
    ctx = Context(**{**ctx.__dict__, "validate": validate})
    try:
        record.update(state="running", pause=None, message="")
        if record.get("_recipe") and _recipe(record) != record["_recipe"]:
            raise PlanError("plan_changed")
        # The first step still to do, each time: a step may switch an earlier one on (a sign-in found at test).
        while (step := next((s for s in record["steps"] if s["state"] not in {"done", "skipped"}), None)) is not None:
            record["current_step"] = step["id"]
            step["state"] = "running"
            _save(record)
            with safe.checked(ctx.validate):  # Stopping or signing out ends a download the step started.
                outcome = _HANDLERS[(record["kind"], step["type"])](ctx, record, step)
            if outcome in {"done", "skipped"}:
                step["state"] = outcome
                continue
            if outcome == "again":
                continue
            if outcome != "running":
                step["state"] = "waiting"
                record.update(state="paused", pause=outcome)
            _save(record)
            return view(record)
        record.update(state="completed", current_step=None,
                      message=record.get("_done_message") or _DONE.get(record["intent"], "Ready to use."))
        _save(record, terminal=True)
    except Exception as error:
        code = getattr(error, "code", str(error))
        step = next((s for s in record["steps"] if s["id"] == record.get("current_step")), None)
        if step is not None and code != "plan_cancelled" and _unsettled(ctx, record, step):
            # An owner command may have taken effect: reads reconcile it; it is never re-sent blindly.
            record.update(state="uncertain", pause=None, message=_MESSAGES["change_unconfirmed"])
            _save(record)
        else:
            message = getattr(error, "message", "") or _MESSAGES.get(code, "This step could not finish. Retry, or open its settings.")
            if step is not None:
                step.update(state="failed", message=message[:512])
            record.update(state="cancelled" if code == "plan_cancelled" else "failed", pause=None, message=message[:512])
            _save(record, terminal=True)
    finally:
        with _LOCK:
            _RUNNING.discard(plan_id)
            _CANCELLED.discard(plan_id)
        facts.invalidate()
    return view(record)


# --- Observation (reads) -----------------------------------------------------

def _unsettled(ctx: Context, record: dict, step: dict) -> bool:
    """Whether this step sent an owner command that is admitted but not finished."""
    from row_bot.runtime import admissions
    for name, command in record["_commands"].items():
        if name.split(":", 1)[0] != step["id"] or not command.get("command_id"):
            continue
        for owner in {ctx.mcp_owner_id, ctx.owner_id}:
            metadata = admissions.read_command_metadata(owner, command["command_id"])
            if metadata is not None and metadata["status"] not in {"completed", "rejected"}:
                return True
    metadata = admissions.read_command_metadata(ctx.owner_id, record["_auth"]) if step["id"] == "sign_in" and record.get("_auth") else None
    return metadata is not None and metadata["status"] not in {"completed", "rejected"}


def _observe_commands(ctx: Context, record: dict, step: dict) -> str:
    """A step stopped mid-run: resume once its owner command is settled, never repeat it."""
    from row_bot.runtime import admissions
    if record["state"] != "running" and record["state"] != "uncertain":
        return ""
    sent = [c for name, c in record["_commands"].items() if name.split(":", 1)[0] == step["id"] and c.get("command_id")]
    if not sent:
        return "resume"  # Nothing was sent; running the step again sends its first command.
    command_id = sent[-1]["command_id"]
    for owner in (ctx.mcp_owner_id, ctx.owner_id):
        metadata = admissions.read_command_metadata(owner, command_id)
        if metadata is None:
            continue
        if metadata["status"] in {"completed", "rejected"}:
            return "resume"
        try:
            settled = facts.reconcile_command(owner, command_id, facts.command_kind(metadata["type"]), ctx.validate)["settled"]
        except Exception:
            settled = False
        return "resume" if settled else "uncertain"
    return "resume"  # Recorded but never admitted: nothing happened yet.


def _observe_sign_in(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application.client_mcp_auth import auth_status
    from row_bot.runtime import admissions
    if not record.get("_auth"):
        return _observe_commands(ctx, record, step)
    metadata = admissions.read_command_metadata(ctx.owner_id, record["_auth"])
    if metadata is None or metadata["status"] == "rejected":
        step["message"] = "Sign-in didn't start. Try again."
        return "failed"
    result = auth_status(owner_id=ctx.owner_id, command_id=record["_auth"], validate=ctx.validate)
    step["sign_in"]["authorization_url"] = result.get("authorization_url")
    if result["state"] == "signed_in":
        return "resume"
    if result["state"] in {"failed", "expired", "cancelled", "disconnected"}:
        step["message"] = result["message"][:512]
        return "failed"
    return ""


PACKAGES = {"npm_package", "pypi_package", "oci_image", "mcpb"}


def _package_problem(code: str) -> str:
    """A package that can't be prepared, in plain words."""
    return {"mcp_package_node_required": "Set up Node.js first.", "mcp_package_uv_required": "Set up uv first.",
            "mcp_package_docker_required": "Docker Desktop isn't running on this computer. Open it, then try again.",
            "mcp_package_integrity_changed": "The package changed since you checked it. Try again to review the new version.",
            "mcp_package_version_required": "It doesn't name an exact version to review.",
            "mcp_package_container_access_unsupported": "It asks Docker for access to this computer that Row-Bot doesn't grant.",
            "mcp_bundle_unavailable": "The bundle file is no longer here. Add it from the file again.",
            }.get(code.split(":", 1)[0], "The package couldn't be prepared. Try again, or check it in advanced settings.")


def _observe_runtime(ctx: Context, record: dict, step: dict) -> str:
    stage = next((s for s in ("install", "resolve") if (step["id"] + ":" + s) in record["_commands"]), None)
    if step["runtime"]["id"] in PACKAGES or stage is None or ctx.runtimes is None:
        return _observe_commands(ctx, record, step)
    receipt = _runtime_receipt(ctx, record, step, stage)
    return "resume" if receipt["status"] in {"completed", "rejected"} or receipt.get("installation", {}).get("quiesced") else ""


_OBSERVERS = {"sign_in": _observe_sign_in, "runtime": _observe_runtime}


# --- Owner commands ----------------------------------------------------------

def _bound(expected: dict | None) -> Callable[[dict], None]:
    """Only a review computed in this run, from the consented plan, may execute."""
    def check(review: dict) -> None:
        if expected is None or review.get("action_digest") != expected.get("action_digest"):
            raise PlanError("plan_changed")
    return check


def _once(record: dict, name: str, build: Callable[[], tuple[dict, dict]]) -> tuple[dict, dict | None]:
    """The owner command for one sub-step, recorded before it is sent."""
    saved = record["_commands"].get(name)
    if saved is not None:
        return saved, None
    command, review = build()
    record["_commands"][name] = command
    _save(record)
    return command, review


def _revision(ctx: Context, record: dict) -> str:
    from row_bot.application.capability_configuration_controls import read_mcp_configuration
    return read_mcp_configuration(validate=ctx.validate, target=record["target"]).revision or ""


def _completed(result: dict) -> None:
    if result.get("status") != "completed":
        raise PlanError("change_unconfirmed")


def _mcp_command(kind: str, **payload) -> dict:
    return {"command_id": str(uuid4()), "type": kind, "expected_revision": "0", "payload": payload}


def _hermes_recipe(ctx: Context, record: dict, step: dict) -> str | None:
    """A Hermes recipe is read at its pin now, after consent; what it connects to or runs is shown in place
    before anything is saved, and only that exact recipe is saved."""
    from row_bot.plugins import hermes_mcp
    reference = record["reference"]
    found = record.get("_hermes")
    if found is None:
        try:
            found = hermes_mcp.read_recipe(reference["name"], reference["pin"])
        except Exception as error:
            raise PlanError("recipe_unsupported", "Row-Bot can't use this recipe: it needs setup Row-Bot doesn't do.") from error
        record["_hermes"] = found
    cfg = next(iter(json.loads(found["import_json"])["mcpServers"].values()))
    lines = ([f"Connects to {cfg['url']}."] if cfg.get("url") else
             [f"Runs {' '.join([cfg['command'], *cfg.get('args', [])])[:200]} on this computer."])
    lines += [f"Asks for {', '.join(i['name'] for i in cfg.get('inputs', []))}, kept in your system keychain."] if cfg.get("inputs") else []
    fixed = [name for name, value in (cfg.get("env") or {}).items() if "{" not in value]
    lines += [f"Sets {', '.join(fixed)} for it."] if fixed else []
    step["review"] = {"summary": f"{record['name']} recipe at {reference['pin'][:12]}", "lines": [line[:256] for line in lines],
                      "items": [], "digest": "sha256:" + hashlib.sha256(found["import_json"].encode()).hexdigest()}
    if ctx.review_digest != step["review"]["digest"]:
        step["message"] = "Check what this recipe does, then continue."
        return "digest_changed"
    step["message"] = ""
    return None


def _mcp_consent(ctx: Context, record: dict, step: dict) -> str:
    """For a catalog entry, agreeing saves the connection, switched off."""
    if record.get("server_id"):
        return "done"
    from row_bot.application import capability_configuration_controls as configuration
    from row_bot.mcp_client.marketplace import MarketplaceEntry, entry_to_server_config
    recipe = record["reference"].get("kind") == "hermes_mcp"
    if recipe and "consent:save" not in record["_commands"]:
        paused = _hermes_recipe(ctx, record, step)
        if paused:
            return paused

    def build():
        if recipe:
            revision = _revision(ctx, record)
            intent = {"operation": "import", "import_json": record["_hermes"]["import_json"]}
            review = configuration.review_mcp_configuration_command(revision, intent, validate=ctx.validate)
            return _mcp_command("mcp.configuration.save", configuration_revision=revision, intent=intent), review
        listed = MarketplaceEntry(**record["reference"]["entry"])
        entry = listed
        if entry.source == "official":
            from row_bot.mcp_client.registry_snapshot import revalidate_entry
            entry = revalidate_entry(entry)
        if entry_to_server_config(entry) != entry_to_server_config(listed):
            raise PlanError("plan_changed")
        revision = _revision(ctx, record)
        intent = {"operation": "import", "import_json": sources.describe(entry)["import_json"]}
        review = configuration.review_mcp_configuration_command(revision, intent, validate=ctx.validate)
        return _mcp_command("mcp.configuration.save", configuration_revision=revision, intent=intent), review
    command, review = _once(record, "consent:save", build)
    result = configuration.execute_mcp_configuration_command(owner_id=ctx.mcp_owner_id, key=command["command_id"],
        command=command, validate=ctx.validate, validate_review=_bound(review))
    _completed(result)
    record["server_id"] = result["mcp_configuration"]["server_ids"][0]
    record["installed_id"] = "mcp:" + record["server_id"]  # Where the saved connection lives from now on.
    record["_recipe"] = _recipe(record)
    if recipe:  # The steps this recipe needs (keys, a package, a sign-in), now that it is known and saved.
        row = {"app": None, "name": record["name"], "blockers": [], "lifecycle": "available", "readiness": None,
               "target": None}
        steps = _mcp_steps(row, _saved(None, record["server_id"])[1], record["intent"])[0][1:]
        for index, later in enumerate(s for s in steps if s["type"] == "runtime"):
            later["id"] = f"runtime{index or ''}"
        record["steps"] = [step, *steps]
    return "done"


def _policy(ctx: Context, record: dict, name: str, intent: dict) -> None:
    from row_bot.application import capability_policy_controls as policy

    def build():
        revision = _revision(ctx, record)
        review = policy.review_mcp_policy_command(revision, intent, validate=ctx.validate, target=record["target"])
        return _mcp_command("mcp.configuration.control", configuration_revision=revision, intent=intent), review
    command, review = _once(record, name, build)
    _completed(policy.execute_mcp_policy_command(owner_id=ctx.mcp_owner_id, key=command["command_id"], command=command,
        validate=ctx.validate, validate_review=_bound(review), target=record["target"]))


def _connection(ctx: Context, record: dict, name: str, operation: str, live: Any = None) -> dict:
    """Test, connect, or (given the ``live`` runtime state) disconnect, recorded before it is sent."""
    from row_bot.application.capability_runtime_controls import execute_mcp_runtime_command, review_mcp_runtime_command

    def build():
        revision = live.cleanup_revision if live is not None else _revision(ctx, record)
        runtime_id = live.runtime_id if live is not None else None
        review = review_mcp_runtime_command(revision, record["server_id"], operation, runtime_id, validate=ctx.validate,
                                            target=record["target"])
        return _mcp_command("mcp.runtime.control", resource_revision=revision, server_id=record["server_id"],
                            operation=operation, expected_runtime_id=runtime_id), review
    command, review = _once(record, name, build)
    result = execute_mcp_runtime_command(owner_id=ctx.mcp_owner_id, key=command["command_id"], command=command,
        validate=ctx.validate, validate_review=_bound(review), observe_seconds=5, target=record["target"])
    return {**(result.get("mcp_runtime") or {}), "command_id": command["command_id"], "status": result.get("status")}


def _mcp_inputs(ctx: Context, record: dict, step: dict) -> str:
    """Save what the person entered: plain settings in the connection's configuration, keys in the
    keychain. Each is one owner command recorded before it is sent; running the step again never
    re-sends a key, it reads how the first attempt ended."""
    from row_bot.application import capability_configuration_controls as configuration
    from row_bot.application.client_mcp_auth import auth_status, execute_auth, review_auth
    from row_bot.integrations import inputs
    saved = record["_commands"].get("inputs:key")
    if saved is not None:
        state = auth_status(owner_id=ctx.owner_id, command_id=saved["command_id"], validate=ctx.validate)["state"]
        if state in {"starting", "uncertain"}:
            raise PlanError("change_unconfirmed")
        if state == "signed_in":
            return "done"
        del record["_commands"]["inputs:key"]
        return "inputs"
    cfg = _saved(record["target"], record["server_id"])[1]
    setup = facts.mcp_setup({}, cfg)
    declared = setup["inputs"]
    given = {key: str(value) for key, value in ctx.inputs.items()}
    legacy = [b for b in setup["bindings"] if b["kind"] != "input"]
    keys = {b["key"]: given.get(b["key"], "").strip() for b in legacy}
    try:
        plain, secret = inputs.values(declared, {**(cfg.get("input_values") or {}), **given})
    except inputs.InputError as error:
        key = str(error).partition(":")[2]
        label = next((item["label"] for item in declared if item["key"] == key), "a setting")
        step["message"] = (f"Add {label} to continue." if str(error).startswith("input_required") else
                           f"Check {label}: it isn't a value this app accepts.")[:512]
        return "inputs"
    if not all(keys.values()) or (setup["auth_mode"] == "api_key" and not (keys or secret) and not setup["credential_configured"]):
        step["message"] = "Paste your key to continue."
        return "inputs"
    if plain != (cfg.get("input_values") or {}):
        def build():
            revision = _revision(ctx, record)
            intent = {"operation": "edit", "server_id": record["server_id"], "fields": {"input_values": plain}}
            review = configuration.review_mcp_configuration_command(revision, intent, validate=ctx.validate,
                                                                    target=record["target"])
            return _mcp_command("mcp.configuration.save", configuration_revision=revision, intent=intent), review
        # Keyed by the values: settings corrected after a refused key are a new change, never a replay.
        command, review = _once(record, "inputs:values:" + _digest(plain)[:16], build)
        _completed(configuration.execute_mcp_configuration_command(owner_id=ctx.mcp_owner_id, key=command["command_id"],
            command=command, validate=ctx.validate, validate_review=_bound(review), target=record["target"]))
    if not (keys or secret):
        return "done"
    bindings = [{"kind": b["kind"], "name": b["name"], "key": b["key"], "prefix": b.get("prefix", "")} for b in legacy]
    bindings += [{"kind": "input", "name": key, "key": key, "prefix": ""} for key in secret]
    revision = _revision(ctx, record)
    review = review_auth(server_id=record["server_id"], configuration_revision=revision, action="start", mode="api_key",
                         label=record["name"], bindings=bindings, validate=ctx.validate, target=record["target"])
    record["_commands"]["inputs:key"] = {"command_id": str(uuid4())}
    _save(record)
    result = execute_auth(owner_id=ctx.owner_id, command_id=record["_commands"]["inputs:key"]["command_id"],
        server_id=record["server_id"], configuration_revision=revision, action="start", mode="api_key", label=record["name"],
        bindings=bindings, values={**keys, **secret}, validate=ctx.validate, validate_review=_bound(review),
        target=record["target"])
    if result["state"] != "signed_in":
        raise PlanError("key_refused", "The key couldn't be saved. Check it and try again.")
    return "done"


def _mcp_sign_in(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application.client_mcp_auth import execute_auth, review_auth
    if record.get("_auth"):
        seen = _observe_sign_in(ctx, record, step)
        if seen == "failed":
            raise PlanError("sign_in_failed", step["message"] or "Sign-in didn't finish. Try again.")
        return "done" if seen == "resume" else "sign_in"
    if not ctx.redirect_uri:
        raise PlanError("mcp_auth_callback_unavailable", "Signing in needs Row-Bot open on this computer.")
    client = _sign_in_client(ctx, record, step)
    if client is False:
        return "inputs"
    revision = _revision(ctx, record)
    review = review_auth(server_id=record["server_id"], configuration_revision=revision, action="start", mode="oauth",
                         label=record["name"], validate=ctx.validate, target=record["target"])
    record["_auth"] = str(uuid4())
    _save(record)
    result = execute_auth(owner_id=ctx.owner_id, command_id=record["_auth"], server_id=record["server_id"],
        configuration_revision=revision, action="start", mode="oauth", label=record["name"], redirect_uri=ctx.redirect_uri,
        client=client, validate=ctx.validate, validate_review=_bound(review), target=record["target"])
    step["sign_in"]["authorization_url"] = result.get("authorization_url")
    return "sign_in"


def _sign_in_client(ctx: Context, record: dict, step: dict) -> dict | None | bool:
    """How Row-Bot signs in: with the person's own OAuth app when the app needs one (or the person gave
    one before); otherwise with Row-Bot's published client metadata (CIMD) or a client registered for this
    connection (DCR), whichever the server's sign-in service supports. ``False``: the person's own app is
    needed, so the step waits for its client ID and secret, which go only to the keychain."""
    from urllib.parse import urlsplit
    from row_bot.mcp_client import auth
    cfg = _saved(record["target"], record["server_id"])[1]
    source = cfg.get("source") or {}
    ref = (cfg.get("auth") or {}).get("credential_ref")
    if ref:
        try:
            saved = auth.read_credentials(ref)
            if saved.get("client_source") == "own" and saved.get("client"):
                step["sign_in"]["method"] = "oauth_client"
                return saved["client"]  # Signing in again keeps the person's own app.
        except auth.McpAuthError:
            pass
    found = record.get("_signs_in")
    if found is None and source.get("oauth_client") != "required":
        try:
            found = record["_signs_in"] = auth.discover_sign_in(_address(cfg))
        except auth.McpAuthError:
            found = {"required": True}  # Unreachable now; the SDK's own discovery reports it when signing in.
    loopback = urlsplit(ctx.redirect_uri).hostname in {"127.0.0.1", "localhost", "::1"}
    own = source.get("oauth_client") == "required" or bool(found and found.get("metadata") is not None and not found.get("dcr")
                                                           and not (found.get("cimd") and loopback))
    if not own:
        step["sign_in"]["method"] = "oauth_cimd" if found and found.get("cimd") and loopback else "oauth_dcr"
        return None
    step["sign_in"]["method"] = "oauth_client"
    callback = "http://127.0.0.1" + auth.CALLBACK_PATH if loopback else ctx.redirect_uri
    step["inputs"] = [
        {"key": "client_id", "label": "Client ID", "secret": False, "required": True, "target": "header", "name": "client_id",
         "template": "", "default": "", "choices": [], "format": "string",
         "help_url": public_link(source.get("oauth_client_url")) or getattr(apps.match(facts._mcp_refs(cfg)), "docs_url", ""),
         "description": f"From an OAuth app you create for Row-Bot. Use {callback} as its callback URL."[:512]},
        {"key": "client_secret", "label": "Client secret", "secret": True, "required": False, "target": "header",
         "name": "client_secret", "template": "", "default": "", "choices": [], "format": "string", "help_url": "",
         "description": "Kept only in your system keychain."}]
    client_id, secret = str(ctx.inputs.get("client_id") or "").strip(), str(ctx.inputs.get("client_secret") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._~:/+=-]{1,256}", client_id) or len(secret) > 512 or any(c in secret for c in "\r\n\0"):
        step["message"] = f"{record['name']} needs your own OAuth app to sign in. Add its client ID to continue."
        return False
    step["message"] = ""
    return {"client_id": client_id, **({"client_secret": secret} if secret else {}), "redirect_uris": [ctx.redirect_uri],
            "token_endpoint_auth_method": "client_secret_post" if secret else "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}


def _runtime_receipt(ctx: Context, record: dict, step: dict, stage: str) -> dict:
    command = record["_commands"][step["id"] + ":" + stage]
    return ctx.runtimes.receipt(owner_id=ctx.owner_id, runtime_id=step["runtime"]["id"], command_id=command["command_id"],
                                validate=ctx.validate)


def _mcp_runtime(ctx: Context, record: dict, step: dict) -> str:
    if step["runtime"]["id"] in PACKAGES:
        from row_bot.application.mcp_runtime_installation import inspect_mcp_package, prepare_mcp_package
        from row_bot.mcp_client import packages
        if not ctx.local_owner:
            raise PlanError("owner_local_only")
        lock = record.setdefault("_locks", {}).get(step["id"])
        if lock is None:  # Exact versions and checksums, from the registry; nothing is installed or run.
            try:
                lock = packages.resolve(_saved(record["target"], record["server_id"])[1], check=ctx.validate)
            except (ValueError, OSError, TimeoutError) as error:
                raise PlanError("package_unresolved", _package_problem(str(error))) from None
            record["_locks"][step["id"]] = lock
        step["review"] = packages.review(lock)
        if ctx.review_digest != lock["digest"] and step["id"] + ":package" not in record["_commands"]:
            step["message"] = "Check what will be installed, then continue."
            return "digest_changed"  # The person sees exactly what will be installed before it is.
        step["message"] = ""

        def build():
            revision = _revision(ctx, record)
            try:
                inspected = inspect_mcp_package(owner_id=ctx.owner_id, server_id=record["server_id"], configuration_revision=revision,
                                                target=record["target"], validate=ctx.validate, lock=lock)
            except ValueError as error:
                raise PlanError("package_unavailable", _package_problem(str(error))) from None
            return {"command_id": str(uuid4()), "revision": revision, "preview_id": inspected["preview_id"],
                    "digest": inspected["digest"], "action_digest": inspected["action_digest"]}, inspected
        saved, review = _once(record, step["id"] + ":package", build)
        _completed(prepare_mcp_package(owner_id=ctx.owner_id, command_id=saved["command_id"], server_id=record["server_id"],
            configuration_revision=saved["revision"], preview_id=saved["preview_id"], digest=saved["digest"],
            target=record["target"], validate=ctx.validate, validate_review=_bound(review)))
        return "done"
    if ctx.runtimes is None or ctx.read_policy is None:
        raise PlanError("runtime_unavailable", "Setting up " + step["runtime"]["label"] + " needs Row-Bot open on this computer.")
    from row_bot.mcp_client import requirements
    runtime = step["runtime"]["id"]
    for stage in ("install", "resolve"):
        if step["id"] + ":" + stage not in record["_commands"]:
            continue
        receipt = _runtime_receipt(ctx, record, step, stage)
        if receipt["status"] != "completed":
            if receipt["status"] == "rejected" or receipt.get("installation", {}).get("quiesced"):
                raise PlanError("runtime_failed", "Setting up " + step["runtime"]["label"] + " didn't finish. Try again.")
            return "running"
        if stage == "install":
            return "done"
        break
    stage = "install" if step["id"] + ":resolve" in record["_commands"] else "resolve"
    source = record["_commands"][step["id"] + ":resolve"]["command_id"] if stage == "install" else None
    review = ctx.runtimes.review(owner_id=ctx.owner_id, runtime_id=runtime, operation=stage,
        resource_revision=requirements.runtime_install_revision(runtime), source_command_id=source,
        validate=ctx.validate, read_policy=ctx.read_policy)
    command = {"command_id": str(uuid4()), "type": "mcp.runtime." + stage, "payload": {"runtime_id": runtime,
               "source_command_id": source, "resource_revision": review.resource_revision, "action_digest": review.action_digest}}
    record["_commands"][step["id"] + ":" + stage] = command
    _save(record)
    ctx.runtimes.execute(command, owner_id=ctx.owner_id, key=command["command_id"], validate=ctx.validate,
        read_policy=ctx.read_policy,
        validate_review=lambda value: _bound({"action_digest": review.action_digest})({"action_digest": value.action_digest}))
    return "running"


def _address(cfg: dict) -> str:
    """The connection's address with the person's own values (a tenant) filled in."""
    from row_bot.integrations import inputs
    return inputs.resolve(cfg, {}, partial=True)["url"] if cfg.get("inputs") else cfg.get("url", "")


def _mcp_test(ctx: Context, record: dict, step: dict) -> str:
    sign_in = next((s for s in record["steps"] if s["type"] == "sign_in"), None)
    if sign_in is not None and sign_in["state"] == "skipped" and "_signs_in" not in record:
        # One unauthenticated request shows whether the app asks for a sign-in; if it does, sign in first.
        from row_bot.mcp_client.auth import McpAuthError, discover_sign_in
        try:
            record["_signs_in"] = discover_sign_in(_address(_saved(record["target"], record["server_id"])[1]))
        except McpAuthError:
            record["_signs_in"] = {"required": False}  # Unreachable: the connection check says why.
        if record["_signs_in"]["required"]:
            sign_in.update(state="pending", message="")
            step["state"] = "pending"
            return "again"
    outcome = _connection(ctx, record, "test:probe", "test")
    if outcome.get("state") == "tested":
        record["_test"] = outcome["command_id"]
        return "done"
    if outcome.get("state") == "failed" or outcome.get("code") == "mcp_connection_failed":
        raise PlanError("mcp_connection_failed")
    return "running"


def _saved(target: dict | None, server_id: str) -> tuple[str, dict]:
    from row_bot.application.capability_configuration_controls import _server_id
    from row_bot.mcp_client import config, targets
    saved = config.read_saved_configuration(targets.normalize(target))
    return next(((n, c) for n, c in saved.document["servers"].items() if _server_id(n) == server_id), ("", {}))


def _mcp_on() -> bool:
    from row_bot.mcp_client import config
    return config.read_saved_configuration(None).document.get("enabled") is True


def _recipe(record: dict) -> str:
    """What runs and where it connects."""
    cfg = _saved(record["target"], record["server_id"])[1]
    return _digest({key: cfg.get(key) for key in ("transport", "url", "command", "args")})


def _note(target: dict | None, server_id: str) -> str:
    from row_bot.mcp_client.conflicts import overlap_note
    return overlap_note(*_saved(target, server_id))


def _tools(ctx: Context | None, record: dict) -> list[dict]:
    """The tools to choose access for, with the safety recorded for each: tested ones while
    connecting, otherwise the ones already accepted."""
    if not record.get("_test"):
        catalog = (_saved(record["target"], record["server_id"])[1].get("tools") or {}).get("catalog") or {}
        return [{"name": n, "description": str(r.get("description") or ""), "effect": r.get("effect", "unknown"),
                 "destructive": bool(r.get("destructive")), "requires_approval": bool(r.get("requires_approval"))}
                for n, r in sorted(catalog.items()) if isinstance(r, dict)]
    from row_bot.application.capability_catalog_controls import tested_tools
    try:
        return tested_tools(owner_id=ctx.mcp_owner_id, server_id=record["server_id"], test_command_id=record["_test"],
                            target=record["target"])
    except Exception:
        raise PlanError("plan_changed") from None


def _tool_view(tool: dict, state: str) -> dict:
    return {"name": tool["name"][:256], "title": tool["name"].replace("_", " ").replace("-", " ").strip().capitalize()[:128],
            "description": sources.plain_text(tool.get("description", ""), 512), "effect": tool["effect"], "state": state,
            "always_asks": presets.locked(tool)}


def current_access(row: dict) -> dict | None:
    """What an installed connection may do now, read from its saved settings only."""
    if row["kind"] != "mcp" or row["lifecycle"] == "available":
        return None
    record = {"target": None if row.get("target") in (None, {"kind": "standalone"}) else row["target"], "server_id": row["owner_ref"]}
    saved = (_saved(record["target"], record["server_id"])[1].get("tools") or {})
    tools = _tools(None, record)
    return {"preset": presets.current(saved) if saved.get("catalog") else presets.DEFAULT, "tools_digest": _digest(tools),
            "tools": [_tool_view(t, presets.actual(saved, t["name"])) for t in tools[:256]],
            "note": _note(record["target"], record["server_id"])}


def _mcp_access(ctx: Context, record: dict, step: dict) -> str:
    tools = _tools(ctx, record)
    digest, chosen = _digest(tools), record.get("overrides") or {}
    # A re-check keeps the tools accepted before as they are; the preset applies to new ones.
    saved = (_saved(record["target"], record["server_id"])[1].get("tools") or {}) if record.get("_test") else {}
    kept = set(saved.get("accepted_names") or [])

    def state(tool: dict) -> str:
        if tool["name"] in chosen:
            return chosen[tool["name"]]
        if tool["name"] in kept:
            return presets.actual(saved, tool["name"])
        return presets.tool_state(record["preset"], tool)
    step["access"] = {"preset": record["preset"], "tools_digest": digest, "tools": [_tool_view(t, state(t)) for t in tools[:256]],
                      "note": _note(record["target"], record["server_id"])}
    if ctx.tools_digest == digest:
        step["message"] = ""
        return "done"
    step["message"] = "The tools changed. Review them again." if ctx.tools_digest else "Review what this app can do, then allow it."
    return "access"


def _mcp_change(ctx: Context, record: dict, step: dict) -> str:
    """Turn a connection off, or remove a standalone one (with its saved key when cleanup was chosen)."""
    if record["intent"] == "turn_off":
        _policy(ctx, record, "enable:off", {"operation": "server_enabled", "server_id": record["server_id"], "enabled": False})
        return _disconnect(ctx, record)
    from row_bot.application import capability_configuration_controls as configuration

    def build():
        revision = _revision(ctx, record)
        intent = {"operation": "delete", "server_id": record["server_id"], "delete_credentials": record["consent"]["cleanup"]}
        review = configuration.review_mcp_configuration_command(revision, intent, validate=ctx.validate)
        return _mcp_command("mcp.configuration.save", configuration_revision=revision, intent=intent), review
    command, review = _once(record, "enable:remove", build)
    _completed(configuration.execute_mcp_configuration_command(owner_id=ctx.mcp_owner_id, key=command["command_id"],
        command=command, validate=ctx.validate, validate_review=_bound(review)))
    return "done"


def _disconnect(ctx: Context, record: dict) -> str:
    """Turning off also stops a live session, so the connection really stops and can be checked again later."""
    from row_bot.application.capability_runtime_controls import read_mcp_runtime_state
    live = None
    if "enable:disconnect" not in record["_commands"]:
        live = read_mcp_runtime_state(record["server_id"], validate=ctx.validate, target=record["target"])
        if live.runtime_id is None or live.state in {"stopping", "stopped", "missing"}:
            return "done"
    outcome = _connection(ctx, record, "enable:disconnect", "disconnect", live)
    return "done" if outcome.get("state") in {"stopped", "missing"} or outcome.get("status") == "completed" else "running"


def _mcp_enable(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application import capability_catalog_controls as catalog
    from row_bot.application.capability_policy_controls import read_mcp_policy
    if record["intent"] in _DONE:
        return _mcp_change(ctx, record, step)
    if record["intent"] != "access" and record["target"] is None and not record["consent"].get("turns_on_mcp") and not _mcp_on():
        raise PlanError("plan_changed")  # Turning MCP on was not part of this consent.
    chosen = record.get("overrides") or {}
    if record.get("_test"):
        def build():
            revision = _revision(ctx, record)
            review = catalog.review_mcp_catalog_command(owner_id=ctx.mcp_owner_id, configuration_revision=revision,
                server_id=record["server_id"], test_command_id=record["_test"], validate=ctx.validate,
                target=record["target"], preset=record["preset"], overrides=chosen or None)
            return _mcp_command("mcp.catalog.accept", configuration_revision=revision, server_id=record["server_id"],
                                test_command_id=record["_test"], preset=record["preset"],
                                **({"overrides": chosen} if chosen else {})), review
        command, review = _once(record, "enable:accept", build)
        _completed(catalog.execute_mcp_catalog_command(owner_id=ctx.mcp_owner_id, key=command["command_id"], command=command,
            validate=ctx.validate, validate_review=_bound(review), target=record["target"]))
    elif record["intent"] == "access":
        _policy(ctx, record, "enable:preset", {"operation": "preset", "server_id": record["server_id"], "preset": record["preset"],
                                               **({"overrides": chosen} if chosen else {})})
        return "done"
    state = read_mcp_policy(server_id=record["server_id"], validate=ctx.validate, target=record["target"])
    if state.server_enabled is not True:
        _policy(ctx, record, "enable:server", {"operation": "server_enabled", "server_id": record["server_id"], "enabled": True})
    if record["target"] is None and state.global_enabled is not True:
        _policy(ctx, record, "enable:global", {"operation": "global_enabled", "enabled": True})
    if record["target"] is not None:
        from row_bot.plugins.state import is_plugin_enabled
        if not is_plugin_enabled(record["target"]["plugin_id"]):
            return "done"  # Turning the package on connects it.
    from row_bot.application.capability_runtime_controls import read_mcp_runtime_state
    if "enable:connect" not in record["_commands"] and read_mcp_runtime_state(
            record["server_id"], validate=ctx.validate, target=record["target"]).state == "connected":
        return "done"
    outcome = _connection(ctx, record, "enable:connect", "connect")
    if outcome.get("state") == "connected":
        return "done"
    if outcome.get("code") == "mcp_connection_failed" or outcome.get("state") in {"failed", "dependency_missing", "stopped"}:
        raise PlanError("mcp_connection_failed")
    return "running"


def _done(*_args) -> str:
    return "done"


def _latest(record: dict, step: dict) -> str:
    """Nothing newer: skip the change and say so."""
    step["message"] = "You have the latest version."
    next(s for s in record["steps"] if s["type"] == "enable")["state"] = "skipped"
    record["_done_message"] = step["message"]
    return "done"


def _skill_test(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application import client_skill_hub as hub
    reference = record["reference"]
    if record["intent"] == "update":  # Read the newest version from its source now, after consent.
        name = record["item_id"].removeprefix("skill:")
        saved, _ = _once(record, "test:review", lambda: ({"command_id": str(uuid4()),
                                                          "revision": hub._record_revision(_hub_record_named(name))}, {}))
        result = hub.execute_public_skill_maintenance(owner_id=ctx.owner_id, command_id=saved["command_id"], name=name,
            expected_revision=saved["revision"], action="review_update", validate=ctx.validate)
        summary = result.get("update_preview")
        if summary is None:
            raise PlanError("skill_preview_expired")
        if not summary.get("changes"):
            return _latest(record, step)
        record["_skill"] = {"preview_id": summary["preview_id"], "content_hash": summary["content_hash"], "revision": saved["revision"]}
    elif reference.get("install_ref"):  # A featured skill: read its pinned folder now, after consent.
        summary = hub.preview_skill_reference(owner_id=ctx.owner_id, install_ref=reference["install_ref"], name=reference["name"],
                                              publisher=reference["publisher"])
    elif reference.get("upload"):
        summary = hub.preview_uploaded_skill(owner_id=ctx.owner_id, upload=reference["upload"])
    elif reference.get("link"):
        summary = hub.preview_skill_link(owner_id=ctx.owner_id, link=reference["link"])
    else:
        summary = hub.preview_public_skill(owner_id=ctx.owner_id, revision=reference["revision"], entry_id=reference["entry_id"])
    if summary["scan"]["blocked"]:
        raise PlanError("skill_blocked", "Row-Bot's safety check blocked this skill.")
    record.setdefault("_skill", {"preview_id": summary["preview_id"], "content_hash": summary["content_hash"]})
    files = summary.get("review_files") or []
    step["message"] = f"{len(files)} files checked." + (f" {sum(f['executable'] for f in files)} scripts; adding never runs them."
                                                       if any(f["executable"] for f in files) else "")
    return "done"


def _hub_record_named(name: str):
    from row_bot.skills_hub.provenance import get_record
    record = get_record(name)
    if record is None:
        raise PlanError("plan_changed")
    return record


def _package_test(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.plugins.hermes_catalog import inspect_package
    reference = record["reference"]
    if record["intent"] == "fix":  # The package's own local check, nothing more.
        result = _plugin_command(ctx, record, "test:check", "plugin.test")
        if result.get("status") in {"accepted", "partial"}:
            return "running"
        _completed(result)
        from row_bot.application.plugin_commands import read_plugin_detail
        if read_plugin_detail(record["item_id"].removeprefix("plugin:"), validate=ctx.validate)["health"]["status"] != "passed":
            raise PlanError("package_failed", "The package's check didn't pass. Open its advanced settings to see why.")
        step["message"] = "Checks passed."
        return "done"
    if record["intent"] == "update":
        # A package added from the Hermes catalog updates to the catalog's pin, never the repository's head.
        from row_bot.plugins import hermes_catalog
        listed = next((e for e in hermes_catalog.read_catalog()["entries"] if reference.get("identity")
                       and "plugin:" + e["source_identity"] == reference["identity"].rsplit("@", 1)[0]), None)
        summary = inspect_package(owner_id=ctx.owner_id, reference=listed["id"] if listed else reference["source_url"])
        if summary["plugin_id"] != record["item_id"].removeprefix("plugin:"):
            raise PlanError("plan_changed")
        if summary["pin"] and summary["pin"] == reference["pin"]:
            return _latest(record, step)
    else:
        if reference.get("reference", "").startswith("hermes:"):
            from row_bot.plugins import hermes_catalog
            listed = next((e for e in hermes_catalog.read_catalog()["entries"] if e["id"] == reference["reference"]), None)
            if listed is None or (listed["pin"], listed["source_identity"]) != (reference.get("pin"), reference.get("identity")):
                raise PlanError("plan_changed")  # The catalog moved since consent: never fetch another version.
        summary = inspect_package(owner_id=ctx.owner_id, reference=reference.get("upload") or reference["reference"],
                                  local=bool(reference.get("upload")))
    record["_package"] = {"preview_id": summary["preview_id"], "plugin_id": summary["plugin_id"]}
    step["message"] = f"{len(summary['skills'])} skills and {len(summary['servers'])} connections found."
    return "done"


def _lifecycle(ctx: Context, record: dict, name: str, action: str, preview_id: str = "") -> dict:
    """One package lifecycle command (install, update, remove, purge), recorded before it is sent."""
    from row_bot.application.client_plugin_lifecycle import execute_plugin_lifecycle, review_plugin_lifecycle
    if not ctx.local_owner:
        raise PlanError("owner_local_only")
    plugin_id = record.get("_package", {}).get("plugin_id") or record["item_id"].removeprefix("plugin:")

    def build():
        review = review_plugin_lifecycle(action, plugin_id, validate=ctx.validate, owner_id=ctx.owner_id, preview_id=preview_id)
        from row_bot.plugins.lifecycle_review import NO_CHANGES
        declared = [line for line in review.get("changes") or [] if not line.startswith("File ") and line != NO_CHANGES]
        if action == "update" and declared:  # New access is never granted by an update; it needs its own review.
            raise PlanError("update_needs_review", "This update changes what the package can do. Remove it and add it again "
                                                   "to review the changes.")
        return {"command_id": str(uuid4()), "action": action, "plugin_id": plugin_id, "preview_id": preview_id,
                "revision": review["revision"]}, review
    command, _ = _once(record, name, build)
    result = execute_plugin_lifecycle(dict(command), owner_id=ctx.owner_id, validate=ctx.validate)
    if result.get("status") == "uncertain":
        raise PlanError("change_unconfirmed")
    if result.get("status") != "completed":
        raise PlanError("package_not_changed", str(result.get("message") or "The package couldn't be changed."))
    return result


def _add(ctx: Context, record: dict, step: dict) -> str:
    if record["kind"] == "skill":
        from row_bot.application.client_skill_hub import install_previewed_skill
        saved, _ = _once(record, "enable:add", lambda: ({"command_id": str(uuid4())}, {}))
        result = install_previewed_skill(owner_id=ctx.owner_id, command_id=saved["command_id"],
            preview_id=record["_skill"]["preview_id"], content_hash=record["_skill"]["content_hash"], make_available=True,
            validate=ctx.validate)
        if not result.get("success"):
            raise PlanError("skill_not_added", str(result.get("message") or "The skill couldn't be added."))
        record["installed_id"] = "skill:" + str(result.get("skill_name") or "")
        step["message"] = "Added and turned on."
        return "done"
    _lifecycle(ctx, record, "enable:install", "install", record["_package"]["preview_id"])
    record["installed_id"] = "plugin:" + record["_package"]["plugin_id"]
    return "done"


def _switch(ctx: Context, record: dict, step: dict) -> str:
    """Switch an installed skill or package on or off through its owner command."""
    value = record["intent"] != "turn_off"
    if record["kind"] == "skill":
        from row_bot import skills
        from row_bot.application.skill_commands import execute_skill_command, review_skill_command
        payload = {"revision": skills.read_client_skills()["revision"], "name": record["item_id"].removeprefix("skill:"),
                   "preference": "availability", "value": value}
        result = _skill_command(ctx, record, "enable:skill", "skill.preference", payload, review_skill_command, execute_skill_command)
    else:
        result = _plugin_command(ctx, record, "enable:package", "plugin.enable" if value else "plugin.disable")
    if result.get("status") in {"accepted", "partial"}:
        return "running"
    _completed(result)
    return "done"


def _plugin_command(ctx: Context, record: dict, name: str, kind: str) -> dict:
    """One package command (test, enable, disable), reviewed in this run and recorded before it is sent."""
    from row_bot.application.plugin_commands import execute_plugin_command, read_plugin_detail, review_plugin_command
    plugin_id = record["item_id"].removeprefix("plugin:")

    def build():
        payload = {"plugin_id": plugin_id, "revision": read_plugin_detail(plugin_id, validate=ctx.validate)["revision"]}
        review = review_plugin_command(kind, payload, validate=ctx.validate)
        return {"command_id": str(uuid4()), "type": kind, "payload": {**payload, "action_digest": review["action_digest"]}}, review
    command, review = _once(record, name, build)
    return execute_plugin_command(owner_id=ctx.owner_id, key=command["command_id"], command=command,
        validate=ctx.validate, validate_review=_bound(review))


def _skill_command(ctx: Context, record: dict, name: str, kind: str, payload: dict, review_command, execute_command) -> dict:
    def build():
        review = review_command(kind, payload, validate=ctx.validate)
        return {"command_id": str(uuid4()), "type": kind, "payload": {**payload, "review_id": "plan"}}, review
    command, review = _once(record, name, build)
    return execute_command(command, owner_id=ctx.owner_id, authority_id=ctx.owner_id, key=command["command_id"],
        validate=ctx.validate, validate_action=lambda _kind: None, validate_review=lambda _original, value: _bound(review)(value))


def _remove(ctx: Context, record: dict, step: dict) -> str:
    """Owned-only removal: a skill's own files, or a package and its parts; data stays unless cleanup was chosen."""
    if record["kind"] == "plugin":
        if record["reference"].get("lifecycle") != "data_retained":
            _lifecycle(ctx, record, "enable:remove", "remove")
        if record["consent"]["cleanup"]:
            _lifecycle(ctx, record, "enable:purge", "purge")
        return "done"
    from row_bot.application import client_skill_hub as hub
    from row_bot.skills_hub.provenance import get_record
    name = record["item_id"].removeprefix("skill:")
    if get_record(name) is not None or "enable:uninstall" in record["_commands"]:
        saved, _ = _once(record, "enable:uninstall", lambda: ({"command_id": str(uuid4()),
                                                               "revision": hub._record_revision(_hub_record_named(name))}, {}))
        result = hub.execute_public_skill_maintenance(owner_id=ctx.owner_id, command_id=saved["command_id"], name=name,
            expected_revision=saved["revision"], action="uninstall", confirmed=True, validate=ctx.validate)
        if not result.get("success"):
            raise PlanError("skill_not_removed", "The skill couldn't be removed. Try again.")
        return "done"
    from row_bot.application.skill_commands import execute_skill_command, read_skill_detail, review_skill_command
    detail = read_skill_detail(name, validate=ctx.validate)
    payload = {"revision": detail["library_revision"], "name": name, "skill_revision": detail["skill"]["revision"]}
    _completed(_skill_command(ctx, record, "enable:delete", "skill.delete", payload, review_skill_command, execute_skill_command))
    return "done"


def _update(ctx: Context, record: dict, step: dict) -> str:
    if record["kind"] == "plugin":
        _lifecycle(ctx, record, "enable:update", "update", record["_package"]["preview_id"])
        return "done"
    from row_bot.application import client_skill_hub as hub
    saved, _ = _once(record, "enable:update", lambda: ({"command_id": str(uuid4())}, {}))
    result = hub.execute_public_skill_maintenance(owner_id=ctx.owner_id, command_id=saved["command_id"],
        name=record["item_id"].removeprefix("skill:"), expected_revision=record["_skill"]["revision"], action="update",
        preview_id=record["_skill"]["preview_id"], content_hash=record["_skill"]["content_hash"], validate=ctx.validate)
    if not result.get("success"):
        raise PlanError("skill_not_updated", "The update couldn't be applied. Try again.")
    return "done"


def _enable(ctx: Context, record: dict, step: dict) -> str:
    return {"turn_on": _switch, "turn_off": _switch, "fix": _switch, "remove": _remove, "update": _update}.get(
        record["intent"], _add)(ctx, record, step)


_HANDLERS: dict[tuple[str, str], Callable[[Context, dict, dict], str]] = {
    ("mcp", "consent"): _mcp_consent, ("mcp", "inputs"): _mcp_inputs, ("mcp", "runtime"): _mcp_runtime,
    ("mcp", "sign_in"): _mcp_sign_in, ("mcp", "test"): _mcp_test, ("mcp", "access"): _mcp_access, ("mcp", "enable"): _mcp_enable,
    ("mcp", "local_app_check"): _local_app, ("plugin", "local_app_check"): _local_app,
    ("skill", "consent"): _done, ("skill", "test"): _skill_test, ("skill", "enable"): _enable,
    ("plugin", "consent"): _done, ("plugin", "test"): _package_test, ("plugin", "enable"): _enable,
}
