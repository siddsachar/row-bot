"""Claude (your Claude plan): every model call runs the person's own Claude Code.

Row-Bot never reads, stores, refreshes or relays a Claude credential. It finds the ``claude`` program (the
person's own, or the one "Use my Claude plan" installed from Anthropic, ``claude_install.py``), asks it
``claude auth status``, and runs it as installed for each call (``transports/claude_plan_cli.py``). Signing
in happens only in Claude Code, with ``claude auth login`` in Claude's own sign-in panel
(``claude_sign_in.py``).

Claude Code is started with its non-essential traffic off (no telemetry, error reports or automatic
updates from these runs), without Row-Bot's own keys and settings in its environment, and without any
variable that would send it to an API key, another endpoint or another cloud instead of the person's plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from row_bot.providers.models import ModelInfo, ModelModality, ModelTask, TransportMode

logger = logging.getLogger(__name__)

CLAUDE_SUBSCRIPTION_PROVIDER_ID = "claude_subscription"
CLAUDE_PLAN_LABEL = "Claude (your Claude plan)"
#: The first Claude Code release whose print mode Row-Bot's protocol was checked against.
MINIMUM_CLAUDE_CODE_VERSION = "2.1.263"
CLAUDE_CODE_INSTALL_URL = "https://code.claude.com/docs/en/setup"
#: Settings say this once, for a profile the old Claude sign-in had set up, until Claude is ready.
SET_UP_AGAIN = "set_up_again"
#: ``claude auth status`` names a Claude plan sign-in this way; anything else is not the person's plan.
PLAN_SIGN_IN_METHOD = "claude.ai"

# Variables Claude Code reads that would take a call off the person's plan sign-in, or change the request
# Row-Bot builds: an API key or bearer token, another endpoint, extra headers or body, another cloud.
# Everything named ANTHROPIC_* or CLAUDE* is left out except these two.
_KEPT_CLAUDE_VARIABLES = frozenset({"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH"})
_LEFT_OUT_VARIABLES = frozenset({"MAX_THINKING_TOKENS"})
# Set for every run: no telemetry, error reports or auto-updates (AGENTS.md), no compaction of the
# conversation Row-Bot sends, and Row-Bot's tools listed to the model rather than behind Claude Code's search.
CLAUDE_CODE_RUN_VARIABLES: Mapping[str, str] = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTO_COMPACT": "1",
    "DISABLE_COMPACT": "1",
    "ENABLE_TOOL_SEARCH": "false",
}

_STATUS_SECONDS = 15.0
_COMMAND_SECONDS = 20.0
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_PLAN_NAMES = {"pro": "Claude Pro", "max": "Claude Max", "team": "Claude Team", "enterprise": "Claude Enterprise"}

State = Literal["not_installed", "signed_out", "too_old", "ready", "error"]


@dataclass(frozen=True)
class ClaudePlanStatus:
    """What Settings shows for Claude, and whether a call can run. ``program`` stays on the server."""

    state: State
    version: str = ""
    plan: str = ""
    detail: str = ""
    program: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def to_json(self, *, set_up_again: bool = False, signed_in_before: bool = False) -> dict[str, Any]:
        return {
            "state": self.state,
            "version": self.version,
            "minimum_version": MINIMUM_CLAUDE_CODE_VERSION,
            "plan": self.plan,
            "detail": self.detail,
            "set_up_again": bool(set_up_again and not self.ready),
            # Claude had this profile signed in once (it listed its models): a signed-out Claude signed them out.
            "signed_in_before": bool(signed_in_before and self.state == "signed_out"),
        }


class ClaudePlanUnavailable(RuntimeError):
    """Claude Code can't take a call right now; the message says why and what to do."""

    def __init__(self, message: str, *, state: str = "error") -> None:
        super().__init__(message)
        self.state = state


# ── Finding and running Claude Code ─────────────────────────────────────────


def _install_folders() -> list[Path]:
    """Where Claude Code's installers put ``claude``, for a Row-Bot started without the shell's PATH."""
    home = Path.home()
    folders = [home / ".local" / "bin", home / ".claude" / "local", home / ".npm-global" / "bin",
               home / ".bun" / "bin", home / ".volta" / "bin", home / "bin"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            folders.append(Path(appdata) / "npm")
    else:
        folders += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return folders


def find_claude_code() -> str:
    """The person's ``claude`` program: on PATH first, then the usual install folders; "" when absent."""
    # Windows: the native claude.exe before an npm claude.cmd shim, which runs through cmd.exe.
    names = ("claude.exe", "claude.cmd", "claude") if sys.platform == "win32" else ("claude",)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for folder in _install_folders():
        for name in names:
            candidate = folder / name
            if candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
                return str(candidate)
    return ""


def claude_code_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment Claude Code runs with: the person's own, minus Row-Bot's settings and secrets and
    anything that would move a call off their plan sign-in, plus the run variables above."""
    from row_bot.application.external_terminal import terminal_environment

    kept: dict[str, str] = {}
    for name, value in terminal_environment(environ).items():
        upper = name.upper()
        if upper in _LEFT_OUT_VARIABLES:
            continue
        if upper.startswith(("ANTHROPIC_", "CLAUDE")) and upper not in _KEPT_CLAUDE_VARIABLES:
            continue
        kept[name] = value
    kept.update(CLAUDE_CODE_RUN_VARIABLES)
    return kept


def claude_code_workdir() -> Path:
    """An empty folder of this profile's own for Claude Code to run in; the same one every call."""
    from row_bot.data_paths import get_row_bot_data_dir

    folder = get_row_bot_data_dir() / "runtime" / "claude-code"
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    return folder


def _run(program: str, *args: str) -> subprocess.CompletedProcess[str]:
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([program, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=_COMMAND_SECONDS, check=False, stdin=subprocess.DEVNULL,
                          cwd=str(claude_code_workdir()), env=claude_code_environment(), **options)


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(str(text or ""))
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def _plan_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("claude "):
        return text
    return _PLAN_NAMES.get(text.lower(), f"Claude {text[:1].upper()}{text[1:]}")


def check_claude_code() -> ClaudePlanStatus:
    """Ask the installed Claude Code for its version and sign-in; never reads its files."""
    program = find_claude_code()
    if not program:
        return ClaudePlanStatus("not_installed")
    try:
        answer = _run(program, "--version")
    except (OSError, subprocess.SubprocessError):
        logger.info("Claude Code didn't start to report its version", exc_info=True)
        return ClaudePlanStatus("error", detail="Claude Code didn't start on this computer.", program=program)
    version = parse_version(answer.stdout)
    if answer.returncode != 0 or version is None:
        return ClaudePlanStatus("error", detail="Claude Code didn't say which version it is.", program=program)
    shown = ".".join(str(part) for part in version)
    if version < parse_version(MINIMUM_CLAUDE_CODE_VERSION):  # type: ignore[operator]
        return ClaudePlanStatus("too_old", version=shown, program=program)
    try:
        answer = _run(program, "auth", "status")
    except (OSError, subprocess.SubprocessError):
        logger.info("Claude Code didn't answer auth status", exc_info=True)
        return ClaudePlanStatus("error", version=shown, detail="Claude Code didn't say whether it is signed in.",
                                program=program)
    try:
        report = json.loads(answer.stdout)
    except ValueError:
        report = None
    if not isinstance(report, dict) or not isinstance(report.get("loggedIn"), bool):
        return ClaudePlanStatus("error", version=shown, detail="Claude Code didn't say whether it is signed in.",
                                program=program)
    if not report["loggedIn"]:
        return ClaudePlanStatus("signed_out", version=shown, program=program)
    if report.get("authMethod") != PLAN_SIGN_IN_METHOD or report.get("apiProvider", "firstParty") != "firstParty":
        return ClaudePlanStatus(
            "signed_out", version=shown, program=program,
            detail="Claude Code is signed in without a Claude plan, so it would bill another way.",
        )
    return ClaudePlanStatus("ready", version=shown, plan=_plan_name(report.get("subscriptionType")),
                            program=program)


_status_lock = threading.Lock()
_status_cache: tuple[float, ClaudePlanStatus] | None = None


def claude_plan_status(*, fresh: bool = False) -> ClaudePlanStatus:
    """``check_claude_code`` at most every few seconds; ``fresh`` asks again now."""
    global _status_cache
    with _status_lock:
        now = time.monotonic()
        if not fresh and _status_cache is not None and now - _status_cache[0] < _STATUS_SECONDS:
            return _status_cache[1]
        status = check_claude_code()
        _status_cache = (time.monotonic(), status)
        return status


def forget_claude_plan_status() -> None:
    global _status_cache
    with _status_lock:
        _status_cache = None


#: Where the person fixes anything about Claude: the words of Settings › Providers.
CLAUDE_PLAN_SETTINGS = "Settings › Providers › Claude (your Claude plan)"
SIGNED_OUT_MESSAGE = "Claude signed you out. Sign in again to keep using your plan."


def too_old_message() -> str:
    return f"Row-Bot needs Claude Code {MINIMUM_CLAUDE_CODE_VERSION} or newer."


def unavailable_message(status: ClaudePlanStatus) -> str:
    """What a call says when Claude Code can't take it, with the one thing to do (never a terminal)."""
    if status.state == "not_installed":
        return f"Your Claude plan isn't set up. Choose Use my Claude plan in {CLAUDE_PLAN_SETTINGS}."
    if status.state == "too_old":
        return f"{too_old_message()} Update Claude Code in {CLAUDE_PLAN_SETTINGS}."
    if status.state == "signed_out":
        if status.detail:
            return f"{status.detail} Sign in with your Claude plan in {CLAUDE_PLAN_SETTINGS}."
        return f"{SIGNED_OUT_MESSAGE} Sign in in {CLAUDE_PLAN_SETTINGS}."
    return (f"Row-Bot couldn't use Claude Code: {status.detail or 'it did not answer.'} Check it in "
            f"{CLAUDE_PLAN_SETTINGS}.")


def require_claude_plan() -> ClaudePlanStatus:
    """The status of a Claude Code that can take a call, else the reason it can't."""
    status = claude_plan_status()
    if not status.ready:
        status = claude_plan_status(fresh=True)
    if status.ready:
        return status
    if status.state == "signed_out":
        from row_bot.providers.runtime import ProviderSignInRequired

        raise ProviderSignInRequired(CLAUDE_SUBSCRIPTION_PROVIDER_ID, "Claude", unavailable_message(status))
    raise ClaudePlanUnavailable(unavailable_message(status), state=status.state)


def claude_subscription_runtime_available() -> bool:
    try:
        return claude_plan_status().ready
    except Exception:
        logger.debug("Claude Code status unavailable", exc_info=True)
        return False


# ── The provider's saved settings ───────────────────────────────────────────


def _saved_entry() -> dict[str, Any]:
    from row_bot.providers.config import load_provider_config

    entry = load_provider_config().get("providers", {}).get(CLAUDE_SUBSCRIPTION_PROVIDER_ID, {})
    return dict(entry) if isinstance(entry, dict) else {}


def _set_up_again() -> bool:
    return bool(_saved_entry().get(SET_UP_AGAIN))


def claude_plan_provider_status(*, fresh: bool = False) -> dict[str, Any]:
    """The provider status card for Claude: Claude Code's state, no credential facts of any kind."""
    status = claude_plan_status(fresh=fresh)
    set_up_again = _set_up_again()
    if status.ready and set_up_again:
        _clear_set_up_again()
    return {
        "provider_id": CLAUDE_SUBSCRIPTION_PROVIDER_ID,
        "configured": status.ready,
        "runtime_enabled": status.ready,
        "source": "claude_code" if status.state != "not_installed" else "",
        "plan_type": status.plan,
        "cli_installed": status.state != "not_installed",
        "cli_version": status.version,
        "claude_plan": status.to_json(set_up_again=set_up_again, signed_in_before=_signed_in_before()),
        "last_error": "" if status.ready else unavailable_message(status),
    }


def _signed_in_before() -> bool:
    cache = _saved_entry().get("catalog_cache")
    return isinstance(cache, dict) and bool(cache.get("models"))


def _clear_set_up_again() -> None:
    from row_bot.providers.config import update_provider_config

    def _update(cfg: dict[str, Any]) -> None:
        entry = cfg.get("providers", {}).get(CLAUDE_SUBSCRIPTION_PROVIDER_ID)
        if isinstance(entry, dict):
            entry.pop(SET_UP_AGAIN, None)

    try:
        update_provider_config(_update)
    except Exception:
        logger.debug("Could not clear Claude's set-up-again note", exc_info=True)


# Keys only the old Claude sign-in (Row-Bot's own OAuth, or a reference to Claude Code's credential file) wrote.
_OLD_SIGN_IN_KEYS = ("oauth_bundle_ref", "oauth_bundle_previous", "oauth_bundle_command", "auth_method",
                     "fingerprint", "expires_at", "external_reference_exists", "external_reference_path_hash",
                     "last_runtime_probe", "subscription_options_command", "subscription_probe_command")


def retire_old_claude_sign_in() -> bool:
    """Once, on upgrade: delete the Claude tokens the old sign-in left (keychain and files) and its settings.

    A profile that had it set up is told, once in Settings, to set Claude up again. True when there was
    anything to retire.
    """
    entry = _saved_entry()
    if not any(key in entry for key in _OLD_SIGN_IN_KEYS) and entry.get("source") not in {"external_cli",
                                                                                       "oauth_pkce"}:
        return False
    from row_bot.providers.auth_store import forget_provider_oauth_credentials

    had_setup = bool(entry.get("configured") or entry.get("oauth_bundle_ref")
                     or entry.get("source") in {"external_cli", "oauth_pkce"})
    replacement: dict[str, Any] = {"provider_id": CLAUDE_SUBSCRIPTION_PROVIDER_ID}
    if had_setup:
        replacement[SET_UP_AGAIN] = True
    stored = forget_provider_oauth_credentials(CLAUDE_SUBSCRIPTION_PROVIDER_ID, entry=replacement)
    logger.info("Retired the old Claude sign-in (stored tokens deleted: %s)", stored)
    return True


# ── Models: Claude Code's own list ──────────────────────────────────────────


def _model_info(
    model_id: str,
    *,
    display_name: str = "",
    efforts: tuple[str, ...] | None = None,
    adaptive: bool = True,
    source: str = "claude_code_models",
) -> ModelInfo:
    from row_bot.providers.reasoning import ReasoningCapabilities, reasoning_metadata_for_catalog

    if efforts is None:
        reasoning = reasoning_metadata_for_catalog(CLAUDE_SUBSCRIPTION_PROVIDER_ID, model_id, {})
    elif efforts:
        reasoning = ReasoningCapabilities(
            supported_efforts=efforts, thinking_mode="adaptive" if adaptive else "none",
            source="claude_code_models", request_style="anthropic",
        ).to_json()
    else:
        reasoning = None
    return ModelInfo(
        provider_id=CLAUDE_SUBSCRIPTION_PROVIDER_ID,
        model_id=model_id,
        display_name=display_name or model_id,
        context_window=1_000_000 if model_id.endswith("[1m]") else 200_000,
        transport=TransportMode.ANTHROPIC_MESSAGES,
        capabilities=frozenset({"text", "chat", "streaming", "tool_calling", "vision"}),
        input_modalities=frozenset({ModelModality.TEXT.value, ModelModality.IMAGE.value}),
        output_modalities=frozenset({ModelModality.TEXT.value}),
        tasks=frozenset({ModelTask.CHAT.value}),
        tool_calling=True,
        streaming=True,
        endpoint_compatibility=frozenset({TransportMode.ANTHROPIC_MESSAGES}),
        source_confidence=source,
        risk_label="subscription",
        source=source,
        reasoning=reasoning,
    )


def model_infos_from_initialize(models: Any) -> list[ModelInfo]:
    """Claude models from the model list in Claude Code's ``initialize`` answer.

    A row names its exact model in ``resolvedModel``; a row without one, or an alias of a model already
    listed (``default``), is skipped so every saved choice names one exact model.
    """
    infos: list[ModelInfo] = []
    seen: set[str] = set()
    for row in models if isinstance(models, list) else []:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("resolvedModel") or "").strip()
        if not model_id or not re.fullmatch(r"[A-Za-z0-9._\-\[\]]{1,128}", model_id) or model_id in seen:
            continue
        seen.add(model_id)
        described = str(row.get("description") or "").split("·")[0].strip()
        name = described or str(row.get("displayName") or "").strip() or model_id
        efforts = row.get("supportedEffortLevels") if row.get("supportsEffort") else []
        infos.append(_model_info(
            model_id,
            display_name=name if name.lower().startswith("claude") else f"Claude {name}",
            efforts=tuple(str(item) for item in efforts if isinstance(item, str)) if isinstance(efforts, list) else (),
            adaptive=bool(row.get("supportsAdaptiveThinking")),
        ))
    return infos


def discover_claude_plan_models() -> list[ModelInfo]:
    """Claude Code's model list, from its ``initialize`` answer; no message is sent to any model."""
    from row_bot.providers.transports.claude_plan_cli import initialize_claude_code

    status = claude_plan_status()
    if not status.ready:
        return []
    answer = initialize_claude_code(status.program)
    return model_infos_from_initialize(answer.get("models"))


def _cache_row(info: ModelInfo) -> dict[str, Any]:
    return {"id": info.model_id, "display_name": info.display_name,
            "reasoning": dict(info.reasoning) if info.reasoning else None}


def _save_catalog_cache(infos: list[ModelInfo]) -> None:
    from row_bot.providers.config import update_provider_config

    def _update(cfg: dict[str, Any]) -> None:
        entry = cfg.setdefault("providers", {}).setdefault(CLAUDE_SUBSCRIPTION_PROVIDER_ID, {})
        entry["provider_id"] = CLAUDE_SUBSCRIPTION_PROVIDER_ID
        entry["catalog_cache"] = {"fetched_at": datetime.now(timezone.utc).isoformat(),
                                  "models": [_cache_row(info) for info in infos]}

    update_provider_config(_update)


def _load_catalog_cache() -> list[ModelInfo]:
    cache = _saved_entry().get("catalog_cache")
    rows = cache.get("models") if isinstance(cache, dict) else None
    infos: list[ModelInfo] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        info = _model_info(str(row["id"]), display_name=str(row.get("display_name") or ""), efforts=())
        if isinstance(row.get("reasoning"), dict):
            info = replace(info, reasoning=dict(row["reasoning"]))
        infos.append(info)
    return infos


def list_claude_subscription_model_infos(*, force_refresh: bool = False) -> list[ModelInfo]:
    """The Claude models this plan offers: asked of Claude Code on a refresh, else the last list it gave."""
    if force_refresh:
        try:
            infos = discover_claude_plan_models()
        except Exception as exc:
            logger.warning("Claude Code didn't list its models: %s", exc)
            infos = []
        if infos:
            _save_catalog_cache(infos)
            return infos
    return _load_catalog_cache()


def list_claude_subscription_model_infos_for_status() -> list[ModelInfo]:
    """The last model list Claude Code gave, without starting it."""
    return _load_catalog_cache()


def claude_plan_model_info(model_id: str) -> ModelInfo | None:
    """A Claude model's facts: from Claude Code's list, else a Claude model as Claude Code takes any.

    A chat saved on a model the list no longer names keeps its choice; Claude Code says itself if the plan
    can't use it.
    """
    for info in _load_catalog_cache():
        if info.model_id == model_id:
            return info
    if re.fullmatch(r"claude-[A-Za-z0-9._\-]{1,120}(\[1m\])?", str(model_id or "")):
        return _model_info(model_id, display_name=model_id)
    return None
