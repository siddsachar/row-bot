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
owner receipts; it never sends a command.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import copy
import hashlib
import json
import threading
from typing import Any
from uuid import uuid4

from row_bot.integrations import apps, facts, presets, sources

_LOCK = threading.RLock()
_RUNNING: set[str] = set()
_CANCELLED: set[str] = set()
_MESSAGES = {
    "plan_changed": "Something changed since you agreed. Review it again.",
    "plan_cancelled": "Stopped. Anything already done is kept.",
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
    declared = [(kind, key) for kind in ("headers", "env") for key, value in (cfg.get(kind) or {}).items()
                if value == "" and row["lifecycle"] == "available"]
    if setup["auth_mode"] == "api_key":
        steps.append(_step("inputs", "done" if signed_in else "pending", "Add your key", inputs=[
            {"key": b["key"], "label": b["name"], "secret": True, "required": True, "target": b["kind"], "name": b["name"],
             "template": b.get("prefix", "") + "{value}", "default": "", "choices": [], "help_url": app.key_url if app else ""}
            for b in setup["bindings"]]))
    elif declared:
        steps.append(_step("inputs", "unsupported", "Add your settings", "This connection needs settings Row-Bot can't fill in yet.",
            inputs=[{"key": key, "label": key, "secret": True, "required": True, "target": "header" if kind == "headers" else "env",
                     "name": key, "template": "{value}", "default": "", "choices": [], "help_url": ""} for kind, key in declared]))
    command = str(cfg.get("command") or "").lower().removesuffix(".cmd").removesuffix(".exe")
    if not hosted and command.endswith(("uv", "uvx")):
        steps.append(_step("runtime", "unsupported", "Set up Python tools", "Python-based tools arrive in a later update.",
                           runtime={"id": "uv", "label": "uv"}))
    elif not hosted and command.endswith("docker"):
        steps.append(_step("runtime", "unsupported", "Set up containers", "Container-based tools arrive in a later update.",
                           runtime={"id": "docker", "label": "Docker"}))
    for requirement in [] if hosted else requirements:
        if requirement["available"] or requirement["id"] == "uv":
            continue
        supported = requirement["id"] == "node" and requirement["installable"]
        steps.append(_step("runtime", "pending" if supported else "unsupported", "Set up " + requirement["label"],
                           "" if supported else requirement["label"] + " must be installed separately.",
                           runtime={"id": requirement["id"], "label": requirement["label"]}))
    if setup["package_required"]:
        steps.append(_step("runtime", title="Prepare the package", runtime={"id": "npm_package", "label": "npm package"}))
    for index, step in enumerate(s for s in steps if s["type"] == "runtime"):
        step["id"] = f"runtime{index or ''}"
    local_app = app or apps.catalog()[0].get((row["app"] or {}).get("id", ""))
    if local_app and local_app.local_app:
        steps.append(_step("local_app_check", "unsupported", "Open " + local_app.name,
                           "Checking for " + local_app.local_app + " arrives in a later update.",
                           local_app={"label": local_app.local_app, "help_url": local_app.docs_url}))
    if setup["auth_mode"] == "oauth":
        steps.append(_step("sign_in", "done" if signed_in else "pending", "Sign in to " + name,
                           sign_in={"method": "oauth_dcr", "authorization_url": None}))
    elif setup["auth_mode"] == "unsupported" or (hosted and setup["auth_mode"] == "unknown" and not signed_in
                                                  and (cfg.get("source") or {}).get("requires_auth")):
        steps.append(_step("sign_in", "unsupported", "Sign in to " + name, "Signing in to this app arrives in a later update.",
                           sign_in={"method": "oauth_dcr", "authorization_url": None}))
    installed = row["lifecycle"] != "available"
    accepted = setup["catalog_accepted"] and not codes & {"tools_changed", "tools_not_accepted"}
    retest = bool(codes & {"connection_failed", "expired", "tools_changed", "tools_not_accepted", "sign_in_required", "key_required"})
    checked = installed and accepted and not (intent == "fix" and retest)
    steps.append(_step("test", "done" if checked or intent == "access" else "pending", "Check the connection"))
    steps.append(_step("access", "done" if checked and intent != "access" else "pending", f"Choose what {name} can do",
                       access={"preset": presets.current(cfg.get("tools") or {}) if accepted else presets.DEFAULT,
                               "tools": [], "tools_digest": ""}))
    steps.append(_step("enable", "done" if row["lifecycle"] == "installed" and row["readiness"] == "ready" and intent != "access"
                       else "pending", "Turn on " + name))
    consent = {"destinations": [setup["destination"]] if hosted else [], "runs_locally": not hosted,
               "downloads": [s["runtime"]["label"] for s in steps if s["type"] == "runtime" and s["state"] == "pending"],
               "access_preset": presets.DEFAULT}
    declaration = {"transport": cfg.get("transport"), "url": cfg.get("url", ""), "command": cfg.get("command", ""),
                   "args": cfg.get("args", []), "headers": sorted(cfg.get("headers") or {}), "env": sorted(cfg.get("env") or {}),
                   "auth": setup["auth_mode"], "bindings": setup["bindings"], "source": cfg.get("source") or {}}
    return steps, consent, declaration


def compute(row: dict, reference: dict, *, intent: str = "") -> dict | None:
    """The plan for one entry and intent, or None when nothing needs doing.

    ``reference`` carries what the owners need: ``cfg`` (an installed MCP
    configuration), ``entry`` (a catalog MCP record), or a skill or package
    catalog reference.
    """
    kind, available, action = row["kind"], row["lifecycle"] == "available", row["next_action"]["kind"]
    intent = intent or ("connect" if available and kind == "mcp" else "add" if available else
                        "turn_on" if action == "turn_on" else "fix" if action not in {"try", "none", "delete_data"} else "")
    if (not intent or (intent == "access" and (kind != "mcp" or available))
            or intent not in {"connect", "add", "turn_on", "fix", "access"}):
        return None
    name = (row["app"] or {}).get("name") or row["name"]
    consent = {"destinations": [], "runs_locally": True, "downloads": [], "access_preset": presets.DEFAULT}
    declaration: dict = {}
    if kind == "mcp":
        from row_bot.mcp_client.marketplace import MarketplaceEntry, entry_to_server_config
        entry = reference.get("entry")
        cfg = reference.get("cfg") or entry_to_server_config(entry if not isinstance(entry, dict) else MarketplaceEntry(**entry))
        steps, consent, declaration = _mcp_steps(row, cfg, intent)
    elif available:
        what = "skill" if kind == "skill" else "package"
        steps = [_step("consent", title="Before you add " + name), _step("test", title=f"Check the {what}"),
                 _step("enable", title="Add and turn on" if kind == "skill" else "Add " + name)]
        consent["downloads"] = [row["source_url"] or name]
        if reference.get("kind") not in {"skill", "plugin"}:
            steps[1].update(state="unsupported", message="Add this one from its marketplace page for now.")
        declaration = {key: reference.get(key) for key in ("reference", "pin", "identity", "revision", "entry_id")}
    else:
        steps = [_step("consent", title="Turn on " + name), _step("enable", title="Turn on " + name)]
        if intent == "fix":
            steps[1].update(state="unsupported", title="Fix " + name, message="Open this item's settings to finish it for now.")
    unsupported = next((s for s in steps if s["state"] == "unsupported"), None)
    plan = {"schema_version": 1, "plan_id": None, "item_id": row["id"], "kind": kind, "name": name, "intent": intent,
            "state": "ready", "pause": None, "message": "", "steps": steps, "consent": consent,
            "supported": unsupported is None, "unsupported_reason": unsupported["message"] if unsupported else ""}
    plan["digest"] = _digest({"item_id": row["id"], "intent": intent, "declaration": declaration,
                              "steps": [(s["id"], s["state"]) for s in steps]})
    plan["current_step"] = next((s["id"] for s in steps if s["state"] not in {"done", "skipped"}), None)
    return plan


def next_action(plan: dict) -> dict:
    state, pause = plan["state"], plan.get("pause")
    if not plan.get("supported", True):
        kind = "none"
    elif state == "ready":
        kind = {"connect": "connect", "add": "add", "turn_on": "turn_on"}.get(plan["intent"], "continue_setup")
    elif state == "paused":
        kind = {"inputs": "add_key", "access": "continue_setup", "digest_changed": "fix", "resume": "continue_setup"}.get(pause, "none")
    else:
        kind = "retry" if state in {"failed", "uncertain"} else "try" if state == "completed" else "none"
    return {"kind": kind, "label": "Allow" if pause == "access" else facts.LABELS[kind]}


def view(plan: dict) -> dict:
    """The public plan; owner command records and references stay private."""
    value = {key: copy.deepcopy(item) for key, item in plan.items()
             if not key.startswith("_") and key not in {"reference", "owner", "target", "server_id", "preset"}}
    value["next_action"] = next_action(plan)
    value["consent_token"] = ""
    return value


# --- Persistence -------------------------------------------------------------

def _save(record: dict, *, terminal: bool = False) -> None:
    from row_bot.runtime import admissions
    value = {"command_id": record["plan_id"], "status": "completed" if terminal else "admitting", "plan": record}
    if terminal:
        admissions.complete_command(record["owner"], record["plan_id"], value)
    else:
        admissions.command_progress(record["owner"], record["plan_id"], value)


def _load(owner_id: str, plan_id: str) -> tuple[dict, bool]:
    from row_bot.runtime import admissions
    metadata = admissions.read_command_metadata(owner_id, plan_id)
    saved = admissions.receipt(owner_id, plan_id) if metadata and metadata["type"] == "integrations.plan" else None
    if not saved or not isinstance(saved.get("plan"), dict):
        raise PlanError("not_found")
    return saved["plan"], metadata["status"] != "completed"


# --- Runner ------------------------------------------------------------------

def start(ctx: Context, row: dict, reference: dict, *, digest: str, intent: str = "", preset: str = "",
          plan_id: str = "") -> dict:
    """Admit one consented plan and run it to its first pause."""
    from row_bot.runtime import admissions
    plan = compute(row, reference, intent=intent)
    if plan is None or plan["digest"] != digest:
        raise PlanError("plan_changed")
    if not plan["supported"]:
        raise PlanError("plan_unsupported")
    if preset and preset not in presets.PRESETS:
        raise PlanError("invalid_access_preset")
    if row["kind"] == "plugin" and plan["intent"] == "add" and not ctx.local_owner:
        raise PlanError("owner_local_only")
    installed = row["lifecycle"] != "available"
    target = row.get("target") if installed else None
    reference = dict(reference)
    if "entry" in reference and not isinstance(reference["entry"], dict):
        reference["entry"] = asdict(reference["entry"])
    plan_id = plan_id or str(uuid4())
    record = {**plan, "plan_id": plan_id, "owner": ctx.owner_id, "state": "running", "preset": preset or presets.DEFAULT,
              "reference": {k: v for k, v in reference.items() if k != "cfg"},
              "target": None if target in (None, {"kind": "standalone"}) else target,
              "server_id": row["owner_ref"] if row["kind"] == "mcp" and installed else None, "_commands": {}}
    record["steps"][0]["state"] = "done"
    command = {"command_id": plan_id, "type": "integrations.plan", "item_id": row["id"], "intent": plan["intent"], "digest": digest}
    try:
        prior = admissions.claim_command(ctx.owner_id, plan_id, command, "integrations:plan:" + row["id"], exclusive_target=True,
                                         initial_result={"command_id": plan_id, "status": "admitting", "plan": record})
    except admissions.AdmissionError as error:
        raise PlanError(str(error)) from None
    if prior is not None:  # This exact plan already finished: report it, never run it again.
        return view(prior["plan"])
    return _run(ctx, record)


def resume(ctx: Context, plan_id: str, *, preset: str = "") -> dict:
    """Continue from the current step: after a sign-in, an input, access, or a finished background step."""
    record, open_ = _load(ctx.owner_id, plan_id)
    if not open_:
        raise PlanError("plan_not_resumable")
    if preset:
        if preset not in presets.PRESETS:
            raise PlanError("invalid_access_preset")
        record["preset"] = preset
    return _run(ctx, record)


def cancel(ctx: Context, plan_id: str) -> dict:
    """Stop a plan. A pending sign-in is cancelled; finished steps are kept, never undone."""
    record, open_ = _load(ctx.owner_id, plan_id)
    if open_:
        with _LOCK:
            if plan_id in _RUNNING:
                _CANCELLED.add(plan_id)
                raise PlanError("operation_pending")
        if record.get("_auth"):
            from row_bot.application.client_mcp_auth import cancel_auth
            try:
                cancel_auth(owner_id=ctx.owner_id, command_id=record["_auth"], validate=ctx.validate)
            except Exception:
                pass  # A finished or expired sign-in has nothing left to cancel.
        record.update(state="cancelled", pause=None, message=_MESSAGES["plan_cancelled"])
        _save(record, terminal=True)
    return view(record)


def read_plan(ctx: Context, plan_id: str) -> dict:
    """The plan's state, reconciled from owner receipts. Reading never sends a command."""
    record, open_ = _load(ctx.owner_id, plan_id)
    if open_ and plan_id not in _RUNNING and record["state"] in {"running", "paused", "uncertain"}:
        before = json.dumps(record, sort_keys=True)
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
            _save(record, terminal=seen == "failed")
    return view(record)


def open_plan(ctx: Context, item_id: str) -> dict | None:
    """This owner's unfinished plan for an item, so a client can always find, continue or cancel it."""
    from row_bot.runtime import admissions
    pending = admissions.read_unfinished_target_commands("integrations:plan:" + item_id)
    mine = next((command for command in pending["items"] if command["owner_id"] == ctx.owner_id), None)
    return read_plan(ctx, mine["command_id"]) if mine else None


def _run(ctx: Context, record: dict) -> dict:
    plan_id = record["plan_id"]
    with _LOCK:
        if plan_id in _RUNNING:
            raise PlanError("operation_pending")
        _RUNNING.add(plan_id)
    base = ctx.validate

    def validate() -> None:
        base()
        if plan_id in _CANCELLED:
            raise PlanError("plan_cancelled")
    ctx = Context(**{**ctx.__dict__, "validate": validate})
    try:
        record.update(state="running", pause=None, message="")
        for step in record["steps"]:
            if step["state"] in {"done", "skipped"}:
                continue
            record["current_step"] = step["id"]
            step["state"] = "running"
            _save(record)
            outcome = _HANDLERS[(record["kind"], step["type"])](ctx, record, step)
            if outcome == "done":
                step["state"] = "done"
                continue
            if outcome != "running":
                step["state"] = "waiting"
                record.update(state="paused", pause=outcome)
            _save(record)
            return view(record)
        record.update(state="completed", current_step=None, message="Ready to use.")
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
        kind = "plugin" if metadata["type"].startswith("plugin.lifecycle.") else \
            "skill" if metadata["type"].startswith("skill.hub.") else "mcp"
        try:
            settled = facts.reconcile_command(owner, command_id, kind, ctx.validate)["settled"]
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


def _observe_runtime(ctx: Context, record: dict, step: dict) -> str:
    stage = next((s for s in ("install", "resolve") if (step["id"] + ":" + s) in record["_commands"]), None)
    if step["runtime"]["id"] == "npm_package" or stage is None or ctx.runtimes is None:
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


def _mcp_consent(ctx: Context, record: dict, step: dict) -> str:
    """For a catalog entry, agreeing saves the connection, switched off."""
    if record.get("server_id"):
        return "done"
    from row_bot.application import capability_configuration_controls as configuration
    from row_bot.mcp_client.marketplace import MarketplaceEntry, entry_to_server_config

    def build():
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


def _connection(ctx: Context, record: dict, name: str, operation: str) -> dict:
    from row_bot.application.capability_runtime_controls import execute_mcp_runtime_command, review_mcp_runtime_command

    def build():
        revision = _revision(ctx, record)
        review = review_mcp_runtime_command(revision, record["server_id"], operation, None, validate=ctx.validate,
                                            target=record["target"])
        return _mcp_command("mcp.runtime.control", resource_revision=revision, server_id=record["server_id"],
                            operation=operation, expected_runtime_id=None), review
    command, review = _once(record, name, build)
    result = execute_mcp_runtime_command(owner_id=ctx.mcp_owner_id, key=command["command_id"], command=command,
        validate=ctx.validate, validate_review=_bound(review), observe_seconds=5, target=record["target"])
    return {**(result.get("mcp_runtime") or {}), "command_id": command["command_id"], "status": result.get("status")}


def _mcp_inputs(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application.client_mcp_auth import auth_status, execute_auth, review_auth
    saved = record["_commands"].get("inputs:key")
    if saved is not None:  # Running again never re-sends a secret; it reads the original outcome.
        state = auth_status(owner_id=ctx.owner_id, command_id=saved["command_id"], validate=ctx.validate)["state"]
        if state in {"starting", "uncertain"}:
            raise PlanError("change_unconfirmed")
        if state != "signed_in":
            del record["_commands"]["inputs:key"]
            return "inputs"
        return "done"
    values = {i["key"]: str(ctx.inputs.get(i["key"], "")) for i in step["inputs"]}
    if not all(values.values()):
        step["message"] = "Paste your key to continue."
        return "inputs"
    bindings = [{"kind": i["target"], "name": i["name"], "key": i["key"], "prefix": i["template"].removesuffix("{value}")}
                for i in step["inputs"]]
    revision = _revision(ctx, record)
    review = review_auth(server_id=record["server_id"], configuration_revision=revision, action="start", mode="api_key",
                         label=record["name"], bindings=bindings, validate=ctx.validate, target=record["target"])
    record["_commands"]["inputs:key"] = {"command_id": str(uuid4())}
    _save(record)
    result = execute_auth(owner_id=ctx.owner_id, command_id=record["_commands"]["inputs:key"]["command_id"],
        server_id=record["server_id"], configuration_revision=revision, action="start", mode="api_key", label=record["name"],
        bindings=bindings, values=values, validate=ctx.validate, validate_review=_bound(review), target=record["target"])
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
    revision = _revision(ctx, record)
    review = review_auth(server_id=record["server_id"], configuration_revision=revision, action="start", mode="oauth",
                         label=record["name"], validate=ctx.validate, target=record["target"])
    record["_auth"] = str(uuid4())
    _save(record)
    result = execute_auth(owner_id=ctx.owner_id, command_id=record["_auth"], server_id=record["server_id"],
        configuration_revision=revision, action="start", mode="oauth", label=record["name"], redirect_uri=ctx.redirect_uri,
        validate=ctx.validate, validate_review=_bound(review), target=record["target"])
    step["sign_in"]["authorization_url"] = result.get("authorization_url")
    return "sign_in"


def _runtime_receipt(ctx: Context, record: dict, step: dict, stage: str) -> dict:
    command = record["_commands"][step["id"] + ":" + stage]
    return ctx.runtimes.receipt(owner_id=ctx.owner_id, runtime_id=step["runtime"]["id"], command_id=command["command_id"],
                                validate=ctx.validate)


def _mcp_runtime(ctx: Context, record: dict, step: dict) -> str:
    if step["runtime"]["id"] == "npm_package":
        from row_bot.application.mcp_runtime_installation import inspect_mcp_package, prepare_mcp_package

        def build():
            revision = _revision(ctx, record)
            inspected = inspect_mcp_package(owner_id=ctx.owner_id, server_id=record["server_id"], configuration_revision=revision,
                                            target=record["target"], validate=ctx.validate)
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


def _mcp_test(ctx: Context, record: dict, step: dict) -> str:
    outcome = _connection(ctx, record, "test:probe", "test")
    if outcome.get("state") == "tested":
        record["_test"] = outcome["command_id"]
        return "done"
    if outcome.get("state") == "failed" or outcome.get("code") == "mcp_connection_failed":
        raise PlanError("mcp_connection_failed")
    return "running"


def _tools(ctx: Context, record: dict) -> list[dict]:
    if not record.get("_test"):  # Changing access works on the tools already accepted.
        from row_bot.application.capability_configuration_controls import _server_id
        from row_bot.mcp_client import config
        saved = config.read_saved_configuration(record["target"])
        cfg = next(c for n, c in saved.document["servers"].items() if _server_id(n) == record["server_id"])
        return [{"name": n, "effect": r.get("effect", "unknown"), "destructive": bool(r.get("destructive")),
                 "requires_approval": bool(r.get("requires_approval"))}
                for n, r in sorted(((cfg.get("tools") or {}).get("catalog") or {}).items())]
    from row_bot.application.capability_catalog_controls import read_tested_mcp_catalog
    rows, cursor = [], None
    while True:
        page = read_tested_mcp_catalog(owner_id=ctx.mcp_owner_id, server_id=record["server_id"], test_command_id=record["_test"],
                                       cursor=cursor, limit=50, validate=ctx.validate, target=record["target"])
        if page.availability != "available":
            raise PlanError("plan_changed")
        rows += [{"name": t.name, "effect": t.effect, "destructive": t.destructive, "requires_approval": t.requires_approval}
                 for t in page.items]
        cursor = page.next_cursor
        if not cursor:
            return rows


def _manual(record: dict) -> bool:
    from row_bot.application.capability_configuration_controls import _server_id
    from row_bot.mcp_client import config
    from row_bot.mcp_client.conflicts import requires_manual_tool_selection
    saved = config.read_saved_configuration(record["target"])
    name, cfg = next((n, c) for n, c in saved.document["servers"].items() if _server_id(n) == record["server_id"])
    return requires_manual_tool_selection(name, cfg)


def _mcp_access(ctx: Context, record: dict, step: dict) -> str:
    tools, manual = _tools(ctx, record), _manual(record)
    digest = _digest([tools, manual])
    step["access"] = {"preset": record["preset"], "tools_digest": digest, "tools": [
        {"name": t["name"], "title": t["name"].replace("_", " ").capitalize()[:128], "effect": t["effect"],
         "state": "off" if manual else presets.tool_state(record["preset"], t)} for t in tools[:256]]}
    if ctx.tools_digest == digest:
        step["message"] = ""
        return "done"
    step["message"] = ("The tools changed. Review them again." if ctx.tools_digest else
                       "This app's tools stay off until you choose them in its settings." if manual else
                       "Review what this app can do, then allow it.")
    return "access"


def _mcp_enable(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application import capability_catalog_controls as catalog
    from row_bot.application.capability_policy_controls import read_mcp_policy
    if record.get("_test"):
        def build():
            revision = _revision(ctx, record)
            review = catalog.review_mcp_catalog_command(owner_id=ctx.mcp_owner_id, configuration_revision=revision,
                server_id=record["server_id"], test_command_id=record["_test"], validate=ctx.validate,
                target=record["target"], preset=record["preset"])
            return _mcp_command("mcp.catalog.accept", configuration_revision=revision, server_id=record["server_id"],
                                test_command_id=record["_test"], preset=record["preset"]), review
        command, review = _once(record, "enable:accept", build)
        _completed(catalog.execute_mcp_catalog_command(owner_id=ctx.mcp_owner_id, key=command["command_id"], command=command,
            validate=ctx.validate, validate_review=_bound(review), target=record["target"]))
    elif record["intent"] == "access":
        _policy(ctx, record, "enable:preset", {"operation": "preset", "server_id": record["server_id"], "preset": record["preset"]})
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


def _skill_test(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.application.client_skill_hub import preview_public_skill
    reference = record["reference"]
    summary = preview_public_skill(owner_id=ctx.owner_id, revision=reference["revision"], entry_id=reference["entry_id"])
    if summary["scan"]["blocked"]:
        raise PlanError("skill_blocked", "Row-Bot's safety check blocked this skill.")
    record["_skill"] = {"preview_id": summary["preview_id"], "content_hash": summary["content_hash"]}
    step["message"] = f"{len(summary.get('review_files') or [])} files checked."
    return "done"


def _package_test(ctx: Context, record: dict, step: dict) -> str:
    from row_bot.plugins.hermes_catalog import inspect_package
    summary = inspect_package(owner_id=ctx.owner_id, reference=record["reference"]["reference"])
    record["_package"] = {"preview_id": summary["preview_id"], "plugin_id": summary["plugin_id"]}
    step["message"] = f"{len(summary['skills'])} skills and {len(summary['servers'])} connections found."
    return "done"


def _add(ctx: Context, record: dict, step: dict) -> str:
    if record["kind"] == "skill":
        from row_bot.application.client_skill_hub import install_previewed_skill
        saved, _ = _once(record, "enable:add", lambda: ({"command_id": str(uuid4())}, {}))
        result = install_previewed_skill(owner_id=ctx.owner_id, command_id=saved["command_id"],
            preview_id=record["_skill"]["preview_id"], content_hash=record["_skill"]["content_hash"], make_available=True,
            validate=ctx.validate)
        if not result.get("success"):
            raise PlanError("skill_not_added", str(result.get("message") or "The skill couldn't be added."))
        step["message"] = "Added and turned on."
        return "done"
    from row_bot.application.client_plugin_lifecycle import execute_plugin_lifecycle, review_plugin_lifecycle
    if not ctx.local_owner:
        raise PlanError("owner_local_only")
    package = record["_package"]

    def build():
        review = review_plugin_lifecycle("install", package["plugin_id"], validate=ctx.validate, owner_id=ctx.owner_id,
                                         preview_id=package["preview_id"])
        return {"command_id": str(uuid4()), "action": "install", "plugin_id": package["plugin_id"],
                "preview_id": package["preview_id"], "revision": review["revision"]}, review
    command, _ = _once(record, "enable:install", build)
    result = execute_plugin_lifecycle(dict(command), owner_id=ctx.owner_id, validate=ctx.validate)
    if result.get("status") == "uncertain":
        raise PlanError("change_unconfirmed")
    if result.get("status") != "completed":
        raise PlanError("package_not_added", str(result.get("message") or "The package couldn't be added."))
    return "done"


def _turn_on(ctx: Context, record: dict, step: dict) -> str:
    """Switch an installed skill or package on through its owner command."""
    if record["kind"] == "skill":
        from row_bot import skills
        from row_bot.application.skill_commands import execute_skill_command, review_skill_command
        payload = {"revision": skills.read_client_skills()["revision"], "name": record["item_id"].removeprefix("skill:"),
                   "preference": "availability", "value": True}

        def build():
            review = review_skill_command("skill.preference", payload, validate=ctx.validate)
            return {"command_id": str(uuid4()), "type": "skill.preference", "payload": {**payload, "review_id": "plan"}}, review
        command, review = _once(record, "enable:skill", build)
        result = execute_skill_command(command, owner_id=ctx.owner_id, authority_id=ctx.owner_id, key=command["command_id"],
            validate=ctx.validate, validate_action=lambda _kind: None,
            validate_review=lambda _original, value: _bound(review)(value))
    else:
        from row_bot.application.plugin_commands import execute_plugin_command, read_plugin_detail, review_plugin_command
        plugin_id = record["item_id"].removeprefix("plugin:")

        def build():
            payload = {"plugin_id": plugin_id, "revision": read_plugin_detail(plugin_id, validate=ctx.validate)["revision"]}
            review = review_plugin_command("plugin.enable", payload, validate=ctx.validate)
            return {"command_id": str(uuid4()), "type": "plugin.enable",
                    "payload": {**payload, "action_digest": review["action_digest"]}}, review
        command, review = _once(record, "enable:package", build)
        result = execute_plugin_command(owner_id=ctx.owner_id, key=command["command_id"], command=command,
            validate=ctx.validate, validate_review=_bound(review))
    if result.get("status") in {"accepted", "partial"}:
        return "running"
    _completed(result)
    return "done"


def _enable(ctx: Context, record: dict, step: dict) -> str:
    return (_turn_on if record["intent"] == "turn_on" else _add)(ctx, record, step)


_HANDLERS: dict[tuple[str, str], Callable[[Context, dict, dict], str]] = {
    ("mcp", "consent"): _mcp_consent, ("mcp", "inputs"): _mcp_inputs, ("mcp", "runtime"): _mcp_runtime,
    ("mcp", "sign_in"): _mcp_sign_in, ("mcp", "test"): _mcp_test, ("mcp", "access"): _mcp_access, ("mcp", "enable"): _mcp_enable,
    ("skill", "consent"): _done, ("skill", "test"): _skill_test, ("skill", "enable"): _enable,
    ("plugin", "consent"): _done, ("plugin", "test"): _package_test, ("plugin", "enable"): _enable,
}
