"""Row-Bot modular NiceGUI frontend.

Refactored UI using the ``ui/`` package.

Run:   python app.py              →   http://localhost:8080
    ROW_BOT_PORT=8081 python app.py → http://localhost:8081
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import os
import sys
import time
from pathlib import Path

_DISCORD_BENIGN_VOICE_LOGGERS = (
    "discord.client",
    "discord.gateway",
)

# ── Configure root logger (same as production app) ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
for _noisy in ("httpx", "httpcore", "urllib3", "asyncio", "multipart",
               "watchfiles", "nicegui", "uvicorn.error", "uvicorn.access",
               "sentence_transformers", "transformers", "huggingface_hub",
               "googleapiclient", "googleapiclient.discovery_cache",
               "primp", "ddgs", "ddgs.ddgs", "faster_whisper",
               "streamlit", "kaleido", "choreographer", "pyngrok",
               "pyngrok.process", "pyngrok.process.ngrok"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
for _discord_noisy in _DISCORD_BENIGN_VOICE_LOGGERS:
    logging.getLogger(_discord_noisy).setLevel(logging.ERROR)

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
from row_bot.brand import APP_BRAND_ACCENT, APP_DISPLAY_NAME, APP_HOST_ENV, APP_PING_ID, APP_USER_AGENT
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime_paths import static_dir
from row_bot.version import __version__ as _app_version
os.environ.setdefault("USER_AGENT", APP_USER_AGENT)

logger = logging.getLogger(__name__)

try:
    from row_bot.migration.row_bot_legacy_rebrand import ensure_legacy_rebrand_migration

    _migration_result = ensure_legacy_rebrand_migration()
    if _migration_result.get("status") in {"completed", "already_completed"}:
        _migration_status = str(_migration_result.get("status") or "").replace("_", " ")
        _migration_report = _migration_result.get("report_path", "")
        if _migration_report:
            logger.info("%s data migration %s; report=%s", APP_DISPLAY_NAME, _migration_status, _migration_report)
        else:
            logger.info("%s data migration %s", APP_DISPLAY_NAME, _migration_status)
    for _warning in _migration_result.get("warnings", [])[:5]:
        logger.warning("%s migration warning: %s", APP_DISPLAY_NAME, _warning)
except Exception:
    logger.exception("%s data migration failed", APP_DISPLAY_NAME)
    raise


def _safe_console_print(message: object) -> None:
    text = str(message)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        builtins.print(text)
    except UnicodeEncodeError:
        builtins.print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))

from row_bot.stability import (
    install_asyncio_exception_handler,
    mark_shutdown,
    record_client_error,
    setup_stability_monitoring,
    start_performance_monitor,
    stop_performance_monitor,
)

setup_stability_monitoring()

try:
    from row_bot.startup_diagnostics import preflight_optional_native_packages

    preflight_optional_native_packages(logger)
except Exception:
    logger.debug("Startup diagnostics failed", exc_info=True)

from nicegui import ui, app, run
from fastapi import HTTPException
from row_bot.app_port import get_app_port
from row_bot.ui.performance import log_ui_perf
from row_bot.ui.timer_utils import deactivate_on_disconnect, defer_ui, safe_timer, safe_ui_task

_APP_PORT = get_app_port()
_APP_HOST = os.environ.get(APP_HOST_ENV) or None


@app.post("/api/voice/realtime/client-secret")
async def _voice_realtime_client_secret() -> dict:
    """Mint a browser-safe OpenAI Realtime client secret.

    The long-lived provider key stays on the server. The browser receives only
    the ephemeral credential returned by OpenAI.
    """
    try:
        from row_bot.voice.openai_realtime import OpenAIRealtimeProvider, ROW_BOT_REALTIME_INSTRUCTIONS
        from row_bot.voice.runtime import load_voice_runtime_settings

        voice_settings = load_voice_runtime_settings()
        return OpenAIRealtimeProvider(
            model=voice_settings.talk_model,
            voice=voice_settings.realtime_voice,
        ).create_client_secret(
            instructions=ROW_BOT_REALTIME_INSTRUCTIONS,
        )
    except Exception as exc:
        logger.warning("OpenAI Realtime client secret creation failed", exc_info=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── Patch NiceGUI JSON serializer for surrogate safety ───────────────────────
# PDF text extraction and web scraping can inject lone UTF-16 surrogates
# (U+D800–U+DFFF) into tool results stored in LangGraph checkpoints.
# orjson (used by NiceGUI's socketio transport) rejects these with
# "surrogates not allowed".  This patch catches the error and strips
# surrogates on retry — zero cost for clean data.
def _patch_json_serializer() -> None:
    import nicegui.json as _nj
    import nicegui.json.orjson_wrapper as _ow
    from row_bot.utils.text import _SURROGATE_RE as _SURR

    _orig = _ow.dumps

    def _strip(obj):
        # Only recurse into EXACT built-in container types.  Subclasses
        # (e.g. ``nicegui.classes.Classes``/``ObservableList``) require
        # extra constructor kwargs and must not be reinstantiated here —
        # doing so raises ``TypeError`` and crashes the outbox emit loop.
        t = type(obj)
        if t is str:
            return _SURR.sub('', obj) if _SURR.search(obj) else obj
        if t is dict:
            # Build a new dict (orjson already failed on this one, safe).
            return {_strip(k): _strip(v) for k, v in obj.items()}
        if t is list:
            # Mutate in place — cheaper and keeps identity stable.
            for i, v in enumerate(obj):
                obj[i] = _strip(v)
            return obj
        if t is tuple:
            return tuple(_strip(v) for v in obj)
        # Unknown type (Classes, Props, custom Element, datetime, etc.):
        # leave untouched.  If it still breaks orjson the original
        # TypeError propagates — matches pre-patch behaviour.
        return obj

    def _safe_dumps(obj, *args, **kwargs):
        try:
            return _orig(obj, *args, **kwargs)
        except (TypeError, UnicodeEncodeError):
            return _orig(_strip(obj), *args, **kwargs)

    _ow.dumps = _safe_dumps
    _nj.dumps = _safe_dumps

_patch_json_serializer()


# ── UI package ───────────────────────────────────────────────────────────────
from row_bot.ui.state import (
    AppState, GenerationState, P,
    _active_generations,
    startup_ready, startup_status, startup_warnings,
)
from row_bot.ui.constants import EXAMPLE_PROMPTS, welcome_message
from row_bot.ui.helpers import (
    is_first_run, is_setup_complete, mark_onboarding_seen,
    load_thread_messages, browse_file,
)
from row_bot.ui.head_html import inject_head_html
from row_bot.ui.setup_wizard import show_setup_wizard
from row_bot.ui.render import render_text_with_embeds, add_chat_message
from row_bot.ui.export import open_export
from row_bot.ui.graph_panel import build_graph_panel
from row_bot.ui.task_dialog import show_task_dialog
from row_bot.ui.sidebar import build_sidebar
from row_bot.ui.command_center import build_command_center
from row_bot.ui.settings import open_settings
from row_bot.ui.streaming import Callbacks, send_message, build_interrupt_dialog
from row_bot.ui.home import build_home
from row_bot.ui.chat import build_chat
from row_bot.ui.transcript import (
    TRANSCRIPT_CHUNK_TARGET_MS,
    TRANSCRIPT_MAX_CHUNK_MESSAGES,
    choose_transcript_window,
    message_key,
    message_keys,
    rendered_window_matches,
)

# ── Backend imports ──────────────────────────────────────────────────────────
from row_bot.threads import rename_thread, should_auto_rename_thread
from row_bot.models import (
    get_current_model, is_cloud_model, is_cloud_available,
    is_model_local, refresh_cloud_models,
)
from row_bot.api_keys import apply_keys
from row_bot.agent import get_token_usage, clear_summary_cache
from row_bot.memory_extraction import (
    mark_user_activity, schedule_idle_extraction, start_periodic_extraction, set_active_thread,
)
from row_bot.dream_cycle import start_dream_loop
from row_bot.tasks import (
    ensure_task_schema,
    seed_default_tasks,
    start_task_scheduler,
    get_running_tasks,
    stop_task,
)
from row_bot.notifications import drain_toasts

# ── Channels ─────────────────────────────────────────────────────────────────
from row_bot.channels import config as _ch_config
from row_bot.channels import registry as _ch_registry


# ═════════════════════════════════════════════════════════════════════════════
# SINGLETON STATE
# ═════════════════════════════════════════════════════════════════════════════

state = AppState()
state.show_onboarding = is_first_run()


# ═════════════════════════════════════════════════════════════════════════════
# OAUTH TOKEN HEALTH
# ═════════════════════════════════════════════════════════════════════════════

def _check_oauth_tokens(_st=None) -> list[str]:
    """Check Gmail & Calendar OAuth tokens if those tools are enabled.

    Attempts silent refresh when possible.  Returns a list of warning
    strings (empty if everything is healthy).  If *_st* is provided,
    warnings are also appended to ``_st.startup_warnings``.
    """
    from row_bot.tools import registry as _reg
    warnings: list[str] = []

    for tool_name, display in [("gmail", "Gmail"), ("calendar", "Calendar"), ("x", "X (Twitter)")]:
        if not _reg.is_enabled(tool_name):
            continue
        tool = _reg.get_tool(tool_name)
        if tool is None or not tool.is_authenticated():
            continue
        try:
            status, detail = tool.check_token_health()
            if status in ("valid", "refreshed"):
                label = "token healthy" if status == "valid" else "token refreshed"
                _safe_console_print(f"[oauth] ✅ {display} {label}")
            elif status == "expired":
                msg = f"⚠️ {display} token expired — re-authenticate in Settings → Accounts"
                warnings.append(msg)
                _safe_console_print(f"[oauth] {msg}")
            elif status == "error":
                msg = f"⚠️ {display} token error: {detail}"
                warnings.append(msg)
                _safe_console_print(f"[oauth] {msg}")
        except Exception as exc:
            logger.warning("OAuth check failed for %s: %s", display, exc)

    if _st is not None:
        _st.startup_warnings.extend(warnings)
    return warnings


def _check_github_account_health(_st=None) -> list[str]:
    """Check configured GitHub credentials without warning for anonymous use."""
    warnings: list[str] = []
    try:
        import row_bot.github_account as github_account

        status = github_account.get_verified_github_account_status(use_cache=True)
        if status.source and status.state in {
            github_account.GITHUB_STATE_INVALID_TOKEN,
            github_account.GITHUB_STATE_RATE_LIMITED,
            github_account.GITHUB_STATE_SECONDARY_LIMITED,
            github_account.GITHUB_STATE_OFFLINE,
        }:
            msg = f"GitHub account needs attention: {status.settings_message or status.message}"
            warnings.append(msg)
            _safe_console_print(f"[github] {msg}")
        elif status.connected:
            user = f" as {status.user}" if status.user else ""
            _safe_console_print(f"[github] GitHub API healthy{user}")
    except Exception as exc:
        logger.warning("GitHub account health check failed: %s", exc)

    if _st is not None:
        _st.startup_warnings.extend(warnings)
    return warnings


def _periodic_oauth_check():
    """Background OAuth health check — runs every 6 hours."""
    warnings = _check_oauth_tokens()
    warnings.extend(_check_github_account_health())
    if warnings:
        from row_bot.notifications import notify as _oauth_notify
        for msg in warnings:
            _oauth_notify("Account Issue", msg, sound="default",
                          icon="⚠️", toast_type="warning")


# ═════════════════════════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ═════════════════════════════════════════════════════════════════════════════

@app.on_startup
async def on_startup():
    install_asyncio_exception_handler()
    start_performance_monitor()
    # Attach persistent file logging (daily JSONL to the Row-Bot data dir).
    from row_bot.logging_config import setup_file_logging
    setup_file_logging()

    try:
        from row_bot.startup_diagnostics import preflight_required_runtime_packages
        preflight_required_runtime_packages(logger)
    except Exception:
        logger.debug("Required runtime diagnostics failed", exc_info=True)

    # Kill orphaned ngrok processes from previous runs
    from row_bot.tunnel import kill_stale_ngrok
    kill_stale_ngrok()

    # One-shot: clear project_id on thread_meta rows whose designer
    # project JSON is missing. Prevents the "All Conversations" view
    # from showing threads that claim to belong to a deleted project.
    try:
        from row_bot.threads import sweep_orphan_project_ids
        sweep_orphan_project_ids()
    except Exception:
        logger.exception("Orphan project_id sweep failed")

    import row_bot.ui.state as _st

    logger.info("%s startup initiated", APP_DISPLAY_NAME)
    try:
        from row_bot.data_paths import describe_data_paths
        logger.info("%s data paths: %s", APP_DISPLAY_NAME, describe_data_paths())
    except Exception:
        logger.debug("Could not describe %s data paths", APP_DISPLAY_NAME, exc_info=True)

    def _set(msg: str):
        _st.startup_status = msg
        _safe_console_print(f"[startup] {msg}")

    _set("🔑 Applying API keys…")
    await asyncio.to_thread(apply_keys)

    from row_bot.models import fetch_context_catalog
    _set("📊 Fetching context catalog…")
    await asyncio.to_thread(fetch_context_catalog)

    if is_cloud_available():
        _set("☁️ Loading cached model catalog...")
        state.current_model = get_current_model()

    _set("🔄 Scheduling memory extraction…")
    await asyncio.to_thread(start_periodic_extraction)
    await asyncio.to_thread(schedule_idle_extraction)

    _set("🌙 Starting dream cycle daemon…")
    await asyncio.to_thread(start_dream_loop)

    _set("⬆ Starting auto-update scheduler…")
    try:
        from row_bot.updater import start_update_scheduler
        await asyncio.to_thread(start_update_scheduler)
    except Exception as exc:
        logger.warning("Updater scheduler failed to start (non-fatal): %s", exc)

    _set("⚡ Loading workflows…")
    try:
        await asyncio.to_thread(ensure_task_schema)
        await asyncio.to_thread(lambda: (seed_default_tasks(), start_task_scheduler()))
    except Exception as exc:
        logger.warning("Workflow startup skipped after task DB repair failure: %s", exc)
        _st.startup_warnings.append(
            "Workflow data is temporarily unavailable. "
            "Run launcher.py --reset-tasks-db if it does not recover after restart."
        )

    try:
        from row_bot.providers.model_catalog_cache import schedule_model_catalog_refresh_jobs
        await asyncio.to_thread(schedule_model_catalog_refresh_jobs)
    except Exception as exc:
        logger.warning("Model catalog refresh scheduler failed to start (non-fatal): %s", exc)

    _set("🔌 Starting MCP servers…")
    try:
        from row_bot.mcp_client.runtime import discover_enabled_servers
        await asyncio.to_thread(discover_enabled_servers)
    except Exception as exc:
        logger.warning("MCP startup skipped (non-fatal): %s", exc)

    # Pre-warm agent graph so first thread switch is fast
    _set("🧠 Building agent graph…")
    try:
        from row_bot.agent import get_agent_graph
        await asyncio.to_thread(get_agent_graph)
    except Exception as exc:
        logger.warning("Agent graph pre-warm failed (non-fatal): %s", exc)

    # ── Load Plugins ────────────────────────────────────────────────────────
    _set("🔌 Loading plugins…")
    try:
        from row_bot.plugins import load_plugins
        results = await asyncio.to_thread(load_plugins)
        loaded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        if loaded or failed:
            _safe_console_print(f"[startup] 🔌 Plugins: {loaded} loaded, {failed} failed")
        for r in results:
            if not r.success and r.error:
                _st.startup_warnings.append(f"⚠️ Plugin '{r.plugin_id}' failed: {r.error}")
    except Exception as exc:
        logger.warning("Plugin loading failed (non-fatal): %s", exc)

    # Auto-start channels via registry
    _set("📡 Starting channels…")
    # Ensure channel modules are imported so they self-register
    import row_bot.channels.telegram # noqa: F401
    from row_bot import channels
    import row_bot.channels.slack # noqa: F401
    from row_bot import channels
    import row_bot.channels.sms # noqa: F401
    from row_bot import channels
    import row_bot.channels.discord_channel # noqa: F401
    from row_bot import channels
    import row_bot.channels.whatsapp # noqa: F401
    from row_bot import channels
    try:
        from row_bot.channels.auth_store import migrate_legacy_channel_secrets
        migrated = await asyncio.to_thread(
            migrate_legacy_channel_secrets,
            _ch_registry.all_channels(),
        )
        if migrated.get("migrated"):
            _safe_console_print(
                f"[startup] 🔐 Migrated {migrated['migrated']} channel credential(s) "
                "to channel keyring"
            )
        if migrated.get("failed"):
            logger.warning(
                "Channel credential migration skipped %s field(s); legacy fallback remains active",
                migrated["failed"],
            )
    except Exception as exc:
        logger.warning(
            "Channel credential migration skipped; legacy fallback remains active: %s",
            exc,
        )
    for _ch in _ch_registry.all_channels():
        if _ch_config.get(_ch.name, "auto_start", False):
            try:
                ok = await _ch.start()
                if ok:
                    _safe_console_print(f"[startup] ✅ {_ch.display_name} auto-started")
                else:
                    _st.startup_warnings.append(
                        f"⚠️ {_ch.display_name} failed to auto-start — check Settings → Channels"
                    )
            except Exception as exc:
                _st.startup_warnings.append(
                    f"⚠️ {_ch.display_name} failed to auto-start: {exc}"
                )

    # Auto-start tunnel if it was enabled before restart
    _main_app_tunnel = _ch_config.get("tunnel", "tunnel_main_app", False)
    if isinstance(_main_app_tunnel, list) and _main_app_tunnel:
        _main_app_tunnel = _main_app_tunnel[0] is True
        _ch_config.set("tunnel", "tunnel_main_app", _main_app_tunnel)
    if _main_app_tunnel is True:
        try:
            from row_bot.tunnel import tunnel_manager
            if tunnel_manager.is_available():
                tunnel_manager.start_tunnel(_APP_PORT, label="main_app")
                _safe_console_print(f"[startup] ✅ Main-app tunnel auto-started on port {_APP_PORT}")
        except Exception as exc:
            _st.startup_warnings.append(f"⚠️ Tunnel failed to auto-start: {exc}")

    # ── Proactive OAuth token health check ───────────────────────────
    await asyncio.to_thread(_check_oauth_tokens, _st)
    await asyncio.to_thread(_check_github_account_health, _st)

    # Schedule periodic re-check every 6 hours
    try:
        from row_bot.tasks import _get_scheduler
        _sched = _get_scheduler()
        _sched.add_job(
            _periodic_oauth_check,
            trigger="interval",
            hours=6,
            id="oauth_token_health",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        _safe_console_print("[startup] ⏱️ OAuth periodic check scheduled (every 6 h)")
    except Exception as exc:
        logger.warning("Could not schedule periodic OAuth check: %s", exc)

    try:
        from datetime import datetime, timedelta
        from row_bot.tasks import _get_scheduler
        from row_bot.threads import cleanup_old_checkpoints

        def _run_checkpoint_cleanup() -> None:
            try:
                from row_bot.memory_extraction import is_app_idle
                if not is_app_idle():
                    logger.info("Checkpoint cleanup deferred; app is active")
                    return
                cleanup_old_checkpoints()
            except Exception:
                logger.debug("Checkpoint cleanup failed", exc_info=True)

        _sched = _get_scheduler()
        _sched.add_job(
            _run_checkpoint_cleanup,
            trigger="interval",
            hours=6,
            id="checkpoint_cleanup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            next_run_time=datetime.now() + timedelta(minutes=10),
        )
        _safe_console_print("[startup] 🧹 Checkpoint cleanup scheduled (idle, every 6 h)")
    except Exception as exc:
        logger.warning("Could not schedule checkpoint cleanup: %s", exc)

    # PTY bridge is started lazily when the user first opens the
    # terminal panel (ui/terminal_widget._wire_pty).  This ensures the
    # initial shell prompt flows through the registered xterm.js
    # callback instead of being consumed before the UI connects.
    _safe_console_print("[startup] 💻 Terminal bridge deferred to first panel open")

    # ── Idle browser-tab eviction ────────────────────────────────────
    try:
        from row_bot.tasks import _get_scheduler
        from row_bot.tools.browser_tool import get_session_manager as _get_bs_mgr

        def _evict_idle_browser_tabs() -> None:
            try:
                closed = _get_bs_mgr().evict_idle(ttl_seconds=600.0)
                if closed:
                    logger.info("browser: evicted %d idle tab(s)", closed)
            except Exception:
                logger.debug("browser idle eviction failed", exc_info=True)

        _sched = _get_scheduler()
        _sched.add_job(
            _evict_idle_browser_tabs,
            trigger="interval",
            minutes=5,
            id="browser_idle_eviction",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        _safe_console_print("[startup] ⏱️ Browser idle-tab eviction scheduled (every 5 min, 10 min TTL)")
    except Exception as exc:
        logger.warning("Could not schedule browser idle eviction: %s", exc)

    _set("✅ Ready")
    _st.startup_ready = True
    logger.info("%s startup complete", APP_DISPLAY_NAME)


# ── Webhook API Route ────────────────────────────────────────────────────────

from starlette.requests import Request
from starlette.responses import JSONResponse


async def _launcher_ping_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Identify this process to the desktop launcher."""
    return JSONResponse({"app": APP_PING_ID, "version": _app_version, "port": _APP_PORT})


async def _startup_state_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Expose startup state for the browser-side splash handoff."""
    import row_bot.ui.state as _st

    return JSONResponse({
        "ready": bool(_st.startup_ready),
        "status": str(_st.startup_status or ""),
        "warnings": len(_st.startup_warnings),
    })


async def _webhook_handler(request: Request) -> JSONResponse:
    """Handle POST /api/webhook/{task_id} for webhook-triggered tasks."""
    task_id = request.path_params.get("task_id", "")
    secret = request.query_params.get("secret")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    from row_bot.tasks import handle_webhook
    result = handle_webhook(task_id, secret=secret, payload=payload)
    status_code = 200 if result.get("status") == "ok" else 400
    return JSONResponse(result, status_code=status_code)


async def _client_error_handler(request: Request) -> JSONResponse:
    """Capture browser-side JavaScript errors for local diagnostics."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("kind") == "activity":
        mark_user_activity(str(payload.get("event") or "client activity"))
    record_client_error(payload if isinstance(payload, dict) else {"payload": payload})
    return JSONResponse({"ok": True})


_shutdown_cleanup_started = False


async def _cleanup_runtime(reason: str = "shutdown") -> None:
    """Stop long-lived helpers before the process exits."""
    global _shutdown_cleanup_started
    if _shutdown_cleanup_started:
        return
    _shutdown_cleanup_started = True

    cleanup_started = time.perf_counter()
    stop_performance_monitor()
    mark_shutdown(reason)
    _safe_console_print(f"[shutdown] Cleaning up sessions ({reason})...")
    try:
        # Stop channels before tunnels so webhook/socket clients can close cleanly.
        for _ch in _ch_registry.all_channels():
            try:
                if _ch.is_running():
                    await asyncio.wait_for(_ch.stop(), timeout=10)
                    _safe_console_print(f"[shutdown] {_ch.display_name} channel stopped")
            except asyncio.TimeoutError:
                _safe_console_print(f"[shutdown] {_ch.display_name} channel cleanup timed out")
            except Exception as exc:
                _safe_console_print(f"[shutdown] {_ch.display_name} channel cleanup error: {exc}")
    except Exception as exc:
        _safe_console_print(f"[shutdown] Channel registry cleanup error: {exc}")
    try:
        from row_bot.tools.browser_tool import get_session_manager as _get_bsm
        _get_bsm().kill_all()
        _safe_console_print("[shutdown] Browser session closed")
    except Exception as exc:
        _safe_console_print(f"[shutdown] Browser cleanup error: {exc}")
    try:
        from row_bot.tools.shell_tool import get_session_manager as _get_ssm
        _get_ssm().kill_all()
        _safe_console_print("[shutdown] Shell sessions closed")
    except Exception as exc:
        _safe_console_print(f"[shutdown] Shell cleanup error: {exc}")
    try:
        from row_bot.terminal_bridge import TerminalBridge
        if TerminalBridge.has_instance():
            TerminalBridge.destroy()
            _safe_console_print("[shutdown] Terminal bridge destroyed")
    except Exception as exc:
        _safe_console_print(f"[shutdown] Terminal bridge cleanup error: {exc}")
    try:
        from row_bot.tunnel import tunnel_manager
        tunnel_manager.stop_all()
        _safe_console_print("[shutdown] Tunnels closed")
    except Exception as exc:
        _safe_console_print(f"[shutdown] Tunnel cleanup error: {exc}")
    try:
        from row_bot.mcp_client.runtime import shutdown as _mcp_shutdown
        _mcp_shutdown()
        _safe_console_print("[shutdown] MCP sessions closed")
    except Exception as exc:
        _safe_console_print(f"[shutdown] MCP cleanup error: {exc}")
    _safe_console_print(f"[shutdown] Done in {(time.perf_counter() - cleanup_started):.1f}s")


async def _launcher_shutdown_handler(request: Request) -> JSONResponse:
    """Local launcher hook used for tray quit and updater handoff."""
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in {"127.0.0.1", "::1", "localhost"} and not client_host.endswith("127.0.0.1"):
        return JSONResponse({"ok": False, "error": "localhost only"}, status_code=403)

    async def _shutdown_soon() -> None:
        await asyncio.sleep(0.1)
        await _cleanup_runtime("launcher")
        logger.info("Launcher shutdown complete; exiting process")
        os._exit(0)

    asyncio.create_task(_shutdown_soon())
    return JSONResponse({"ok": True})


app.add_route("/api/launcher-ping", _launcher_ping_handler, methods=["GET"])
app.add_route("/api/startup-state", _startup_state_handler, methods=["GET"])
app.add_route("/api/launcher-shutdown", _launcher_shutdown_handler, methods=["POST"])
app.add_route("/api/webhook/{task_id}", _webhook_handler, methods=["POST"])
app.add_route("/api/client-error", _client_error_handler, methods=["POST"])


@app.on_shutdown
async def on_shutdown():
    await _cleanup_runtime()


# MAIN PAGE
# ═════════════════════════════════════════════════════════════════════════════

@ui.page("/")
async def index():
    import row_bot.ui.state as _st

    ui.dark_mode(True)

    # ── Global panel card style ──────────────────────────────────────────
    ui.add_head_html("""
    <style>
    .row-bot-panel-card {
        border: 1px solid rgba(255,255,255,0.07) !important;
        box-shadow: 4px 0 16px rgba(0,0,0,0.45),
                    -4px 0 16px rgba(0,0,0,0.45),
                    0 4px 12px rgba(0,0,0,0.35) !important;
    }
    .row-bot-inner-panel {
        background: linear-gradient(
            180deg,
            rgba(255,255,255,0.05) 0%,
            rgba(255,255,255,0.015) 100%
        );
        border: 1px solid rgba(255,255,255,0.09);
        border-top-color: rgba(255,255,255,0.14);
        border-bottom-color: rgba(0,0,0,0.15);
        border-radius: 10px;
        padding: 8px 10px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.07),
                    inset 0 -1px 0 rgba(0,0,0,0.12),
                    0 3px 10px rgba(0,0,0,0.35),
                    0 1px 3px rgba(0,0,0,0.2);
    }
    </style>
    """)

    # ── Startup splash (poll until backend is ready) ─────────────────────
    if not _st.startup_ready:
        with ui.column().classes("absolute-center items-center gap-4"):
            ui.image("/static/row_bot_glyph_256.png").style("width: 144px; height: 144px; object-fit: contain;")
            ui.label(APP_DISPLAY_NAME).style(
                f"font-size: 1.6rem; font-weight: 700; letter-spacing: 0.1em; color: {APP_BRAND_ACCENT};"
            )
            status_label = ui.label(_st.startup_status).classes("text-grey-5 text-sm")
            ui.spinner("dots", size="1.5rem", color="grey-6")

        def _poll_ready():
            status_label.text = _st.startup_status
            status_label.update()
            if _st.startup_ready:
                _poll_timer.deactivate()
                ui.run_javascript("window.location.reload()")

        ui.run_javascript("""
        (() => {
          if (window.__rowBotStartupPollInstalled) return;
          window.__rowBotStartupPollInstalled = true;
          const poll = async () => {
            try {
              const response = await fetch('/api/startup-state', {cache: 'no-store'});
              if (!response.ok) return;
              const state = await response.json();
              if (state && state.ready) {
                window.location.reload();
              }
            } catch (_) {}
          };
          const interval = setInterval(poll, 750);
          window.addEventListener('beforeunload', () => clearInterval(interval), {once: true});
          poll();
        })();
        """)

        _poll_timer = safe_timer(0.3, _poll_ready)
        return

    # ── Startup warnings ─────────────────────────────────────────────────
    if _st.startup_warnings:
        for msg in _st.startup_warnings:
            ui.notify(msg, type="warning", timeout=8000, close_button=True)
        _st.startup_warnings.clear()

    # ── Head HTML (styles, highlight.js, vis-network) ────────────────────
    inject_head_html()

    # ── Per-client element holder ────────────────────────────────────────
    p = P()
    p.pending_files = []

    # Pre-create dialogs (modules call .clear() + .open() on these)
    p.settings_dlg = ui.dialog().props("maximized transition-show=fade transition-hide=fade")
    p.export_dlg = ui.dialog()
    p.task_dlg = ui.dialog().props("persistent")

    # ── Health check ─────────────────────────────────────────────────────
    def _run_health_check() -> tuple[bool, str]:
        current = get_current_model()
        if is_cloud_model(current):
            if not is_cloud_available():
                return False, "Provider model selected but no credential configured. Open Settings → Providers."
            return True, ""
        from row_bot.models import _ollama_reachable
        if not _ollama_reachable():
            return False, "Cannot connect to Ollama. Make sure it is running (`ollama serve`)."
        if not is_model_local(current):
            return False, f"Model {current} is not exposed by the Ollama daemon. Manage local models in Ollama, then refresh."
        return True, ""

    try:
        health_result = await run.io_bound(_run_health_check)
    except Exception as exc:
        logger.warning("Startup health check failed", exc_info=True)
        health_result = (False, str(exc) or "Startup health check failed.")
    if not isinstance(health_result, tuple) or len(health_result) != 2:
        logger.warning("Startup health check returned invalid result: %r", health_result)
        health_result = (True, "")
    ok, err = health_result
    if not ok and is_setup_complete():
        ui.notify(err, type="negative", timeout=0, close_button=True)

    # ── Setup wizard gate ────────────────────────────────────────────────
    if not is_setup_complete():
        async def _on_wizard_finish():
            state.current_model = get_current_model()
            if getattr(state, "open_setup_center_on_next_load", False):
                ui.navigate.reload()
            else:
                ui.navigate.to("/")

        await show_setup_wizard(state, on_finish=_on_wizard_finish)
        return

    # ── Build Callbacks bundle ───────────────────────────────────────────
    cb = Callbacks()
    # Slots wired after layout is built (forward declarations)

    # ── Wrappers that close over (state, p, cb) ─────────────────────────
    def _open_settings(initial_tab: str = "Providers"):
        open_settings(state, p, initial_tab)

    def _open_export():
        open_export(state, p)

    def _send_message(text: str, voice_mode: bool = False):
        mark_user_activity("send message")
        return send_message(text, state=state, p=p, cb=cb, voice_mode=voice_mode)

    async def _send_active_voice_message(text: str, *, voice_mode: bool = False):
        binding = getattr(p, "active_voice_binding", None)
        if binding is not None and binding.is_current(state.thread_id):
            return await binding.send_talk(text)
        logger.info(
            "voice.realtime.pipeline %s",
            {
                "stage": "active_voice_surface_missing",
                "thread_id": state.thread_id,
                "text_chars": len(str(text or "")),
                "voice_mode": voice_mode,
            },
        )
        return None

    def _active_voice_surface() -> str:
        binding = getattr(p, "active_voice_binding", None)
        if binding is not None and binding.is_current(state.thread_id):
            return str(binding.surface or "")
        if getattr(state, "active_developer_workspace_id", None):
            return "developer"
        if getattr(state, "active_designer_project", None):
            return "designer"
        return "normal_chat"

    from row_bot.voice.agent_bridge import VoiceAgentBridge
    _voice_bridge = VoiceAgentBridge(
        send_message=_send_active_voice_message,
        active_generation=lambda: _active_generations.get(state.thread_id),
        surface=_active_voice_surface,
        thread_id=lambda: state.thread_id or "",
    )

    def _show_task_dialog(task, on_done):
        show_task_dialog(task, on_done, state=state, p=p)

    # ══════════════════════════════════════════════════════════════════════
    # LAYOUT
    # ══════════════════════════════════════════════════════════════════════

    # ── Sidebar (left drawer) ────────────────────────────────────────────
    rebuild_thread_list = build_sidebar(
        state, p,
        rebuild_main=lambda **kw: _rebuild_main(**kw),
        open_settings=_open_settings,
        load_thread_messages=load_thread_messages,
    )

    # ── Main content column ──────────────────────────────────────────────
    from row_bot.ui.terminal_widget import build_terminal_panel
    from row_bot.tools import registry as _tool_registry

    _outer = ui.column().classes("w-full max-w-7xl mx-auto px-4 no-wrap row-bot-panel-card").style(
        "height: calc(100vh - 16px); overflow: hidden; padding-bottom: 12px;"
        " border-radius: 12px; margin-top: 8px;"
    )
    with _outer:
        p.main_col = ui.column().classes("w-full no-wrap flex-grow").style(
            "overflow: hidden;"
        )
        # Terminal panel — inline, pushes chat content up when expanded
        build_terminal_panel(p, state, _tool_registry)

    # ── Command Center (right drawer) ───────────────────────────────
    build_command_center(
        state, p,
        rebuild_main=lambda **kw: _rebuild_main(**kw),
        rebuild_thread_list=rebuild_thread_list,
        show_task_dialog=_show_task_dialog,
        load_thread_messages=load_thread_messages,
    )
    from row_bot.ui.buddy import build_in_app_buddy
    build_in_app_buddy()
    # Generation counter — every ``_rebuild_main`` bumps this. A
    # deferred hydration compares its captured id; if another rebuild
    # started in the meantime, the stale hydration aborts.
    _rebuild_gen = [0]

    def _rebuild_main(immediate: bool = False, reason: str = "unspecified") -> None:
        """Rebuild the main content column.

        By default paints a lightweight skeleton first and defers the
        real view build to the next tick so the browser can paint the
        skeleton frame — makes thread switches / home / gallery feel
        instant on slow views.

        Pass ``immediate=True`` when the caller needs ``p.chat_container``
        (or similar) to exist synchronously after the call returns (e.g.
        when creating a thread on first message send).
        """
        if p.main_col is None:
            return
        _started = time.perf_counter()
        # Designer needs full width; other views use centered max-w-7xl
        if state.active_designer_project is not None or state.active_developer_workspace_id is not None:
            _outer.classes(remove="max-w-7xl mx-auto px-4", add="px-2")
        else:
            _outer.classes(remove="px-2", add="max-w-7xl mx-auto px-4")

        def _view_name() -> str:
            if state.active_designer_project is not None:
                return "designer"
            if state.active_developer_workspace_id is not None:
                return "developer"
            if state.thread_id is None:
                return "home"
            return "chat"

        def _build_real() -> None:
            if p.main_col is None:
                return
            _real_started = time.perf_counter()
            with p.main_col:
                if state.active_designer_project is not None:
                    from row_bot.designer.editor import build_designer_editor

                    def _exit_designer():
                        from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                        stop_voice_for_thread_change(state, p, reason="exit_designer")
                        state.active_designer_project = None
                        state.thread_id = None
                        state.thread_name = None
                        state.messages = []
                        state.preferred_home_tab = "Designer"
                        _rebuild_main()

                    build_designer_editor(
                        state.active_designer_project,
                        on_back=_exit_designer,
                        send_message=_send_message,
                        p=p,
                        state=state,
                        add_chat_message=lambda msg: add_chat_message(msg, p, state.thread_id),
                        browse_file=browse_file,
                        open_settings=_open_settings,
                    )
                elif state.active_developer_workspace_id is not None:
                    from row_bot.developer.ui import build_developer_workspace

                    def _exit_developer():
                        from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                        stop_voice_for_thread_change(state, p, reason="exit_developer")
                        prev = state.thread_id
                        state.active_developer_workspace_id = None
                        state.thread_id = None
                        state.thread_name = None
                        state.messages = []
                        set_active_thread(None, previous_id=prev)
                        state.preferred_home_tab = "Developer"
                        _rebuild_main()

                    build_developer_workspace(
                        state.active_developer_workspace_id,
                        state=state,
                        p=p,
                        send_message=_send_message,
                        add_chat_message=lambda msg: add_chat_message(msg, p, state.thread_id),
                        browse_file=browse_file,
                        open_settings=_open_settings,
                        show_interrupt=cb.show_interrupt,
                        on_back=_exit_developer,
                        rebuild_main=lambda **kw: _rebuild_main(**kw),
                        rebuild_thread_list=rebuild_thread_list,
                    )
                elif state.thread_id is None:
                    build_home(
                        state, p,
                        rebuild_main=_rebuild_main,
                        rebuild_thread_list=rebuild_thread_list,
                        send_message=_send_message,
                        show_task_dialog=_show_task_dialog,
                        build_graph_panel=build_graph_panel,
                        is_first_run=is_first_run,
                        mark_onboarding_seen=mark_onboarding_seen,
                        load_thread_messages=load_thread_messages,
                        open_settings=_open_settings,
                    )
                else:
                    build_chat(
                        state, p,
                        rebuild_main=_rebuild_main,
                        rebuild_thread_list=rebuild_thread_list,
                        send_message=_send_message,
                        open_settings=_open_settings,
                        open_export=_open_export,
                        show_interrupt=cb.show_interrupt,
                        add_chat_message=lambda msg: add_chat_message(msg, p, state.thread_id),
                        browse_file=browse_file,
                    )
            log_ui_perf(
                "app.main.rebuild.build",
                (time.perf_counter() - _real_started) * 1000.0,
                threshold_ms=500.0,
                reason=reason,
                view=_view_name(),
                immediate=immediate,
            )

        # Immediate path — build real view synchronously, no skeleton.
        if immediate:
            _rebuild_gen[0] += 1
            p.main_col.clear()
            _build_real()
            log_ui_perf(
                "app.main.rebuild.immediate",
                (time.perf_counter() - _started) * 1000.0,
                threshold_ms=500.0,
                reason=reason,
                view=_view_name(),
                generation=_rebuild_gen[0],
            )
            return

        # ── Phase 1: paint a skeleton IMMEDIATELY (same tick) ────────
        # Gives instant visual feedback. The real view is built in a
        # deferred timer so the browser can render this paint first.
        _rebuild_gen[0] += 1
        _my_gen = _rebuild_gen[0]
        from row_bot.ui.skeleton import (
            show_gallery_skeleton,
            show_chat_skeleton,
            show_home_skeleton,
            show_generic_skeleton,
        )
        p.main_col.clear()
        with p.main_col:
            if state.active_designer_project is not None:
                show_generic_skeleton()
            elif state.active_developer_workspace_id is not None:
                show_generic_skeleton()
            elif state.thread_id is None:
                # Home view — pick based on preferred tab
                if getattr(state, "preferred_home_tab", None) == "Designer":
                    show_gallery_skeleton()
                else:
                    show_home_skeleton()
            else:
                show_chat_skeleton()
        log_ui_perf(
            "app.main.rebuild.skeleton",
            (time.perf_counter() - _started) * 1000.0,
            threshold_ms=200.0,
            reason=reason,
            view=_view_name(),
            generation=_my_gen,
        )

        # ── Phase 2: hydrate real view on next tick ──────────────────
        def _hydrate() -> None:
            _hydrate_started = time.perf_counter()
            # Stale — another _rebuild_main happened after us.
            if _my_gen != _rebuild_gen[0]:
                log_ui_perf(
                    "app.main.rebuild.hydrate.stale",
                    (time.perf_counter() - _hydrate_started) * 1000.0,
                    threshold_ms=200.0,
                    reason=reason,
                    view=_view_name(),
                    generation=_my_gen,
                )
                return
            if p.main_col is None:
                return
            try:
                p.main_col.clear()
                _build_real()
                log_ui_perf(
                    "app.main.rebuild.hydrate",
                    (time.perf_counter() - _hydrate_started) * 1000.0,
                    threshold_ms=500.0,
                    reason=reason,
                    view=_view_name(),
                    generation=_my_gen,
                )
            except Exception:
                logger.exception("_rebuild_main hydration failed")

        # 0.01 s is short enough to feel instant but long enough to let
        # the browser paint the skeleton frame. Use a deferred task
        # instead of a one-shot NiceGUI timer so a stale rebuild cannot
        # leave a timer bound to a deleted parent slot.
        defer_ui(_hydrate)

    # ── Interrupt dialog ─────────────────────────────────────────────────
    show_interrupt = build_interrupt_dialog(state, p, cb)

    # ── Wire callback bundle ─────────────────────────────────────────────
    cb.rebuild_main = _rebuild_main
    cb.rebuild_thread_list = rebuild_thread_list
    cb.show_interrupt = show_interrupt
    cb.update_token_counter = lambda: _update_token_counter()
    def _mark_chat_message_rendered(msg: dict) -> None:
        if p.transcript_thread_id != state.thread_id:
            return
        try:
            idx = len(state.messages) - 1
            if idx < 0:
                return
            key = message_key(idx, msg)
            if key not in p.transcript_rendered_keys:
                p.transcript_rendered_keys.append(key)
            p.transcript_total = len(state.messages)
            p.transcript_window_size = len(p.transcript_rendered_keys)
        except Exception:
            logger.debug("Transcript render-state mark failed", exc_info=True)

    def _add_chat_message_and_track(msg: dict) -> None:
        add_chat_message(msg, p, state.thread_id)
        _mark_chat_message_rendered(msg)

    cb.add_chat_message = _add_chat_message_and_track
    cb.mark_chat_message_rendered = _mark_chat_message_rendered
    cb.render_text_with_embeds = render_text_with_embeds

    def _refresh_chat_messages() -> None:
        """Synchronize the active transcript without a full visible rebuild."""
        if p.chat_container is None:
            return
        started = time.perf_counter()
        all_keys = message_keys(state.messages)
        rendered_keys = list(p.transcript_rendered_keys or [])
        same_thread = p.transcript_thread_id == state.thread_id
        window_start = int(p.transcript_window_start or 0)

        def _scroll_bottom() -> None:
            if p.chat_scroll:
                p.chat_scroll.scroll_to(percent=1.0)

        def _log_sync(mode: str, rows: int) -> None:
            try:
                from row_bot.ui.performance import log_ui_perf

                log_ui_perf(
                    "chat.transcript.sync",
                    (time.perf_counter() - started) * 1000.0,
                    mode=mode,
                    rows=rows,
                    total_rows=len(state.messages),
                    thread_id=state.thread_id,
                )
            except Exception:
                logger.debug("Transcript sync perf logging failed", exc_info=True)

        if (
            same_thread
            and rendered_keys
            and rendered_window_matches(rendered_keys, all_keys, start=window_start)
        ):
            rendered_end = window_start + len(rendered_keys)
            missing = list(enumerate(state.messages[rendered_end:], start=rendered_end))
            if not missing:
                _log_sync("noop", 0)
                return

            p.transcript_generation += 1
            sync_generation = p.transcript_generation
            chunk_state = {"idx": 0}

            def _append_chunk() -> None:
                if (
                    p.transcript_generation != sync_generation
                    or p.transcript_thread_id != state.thread_id
                ):
                    return
                start_idx = chunk_state["idx"]
                end_idx = start_idx
                chunk_started = time.perf_counter()
                try:
                    with p.chat_container:
                        while end_idx < len(missing):
                            msg_index, msg = missing[end_idx]
                            add_chat_message(msg, p, state.thread_id)
                            p.transcript_rendered_keys.append(message_key(msg_index, msg))
                            end_idx += 1
                            if end_idx - start_idx >= TRANSCRIPT_MAX_CHUNK_MESSAGES:
                                break
                            elapsed_ms = (time.perf_counter() - chunk_started) * 1000.0
                            if elapsed_ms >= TRANSCRIPT_CHUNK_TARGET_MS:
                                break
                except Exception:
                    logger.debug("Transcript tail append failed", exc_info=True)
                    return
                chunk_state["idx"] = end_idx
                p.transcript_total = len(state.messages)
                p.transcript_window_size = len(p.transcript_rendered_keys)
                if end_idx < len(missing):
                    defer_ui(_append_chunk)
                else:
                    _scroll_bottom()
                    _log_sync("append_tail", len(missing))

            defer_ui(_append_chunk)
            return

        # Rare fallback: the rendered transcript no longer matches state
        # (for example after a stale client handle or a cross-thread race).
        # Keep this scoped to the transcript and bounded to the latest window.
        p.transcript_generation += 1
        sync_generation = p.transcript_generation
        window = choose_transcript_window(len(state.messages))
        display_msgs = state.messages[window.start:window.end]
        display_keys = all_keys[window.start:window.end]
        p.transcript_thread_id = state.thread_id
        p.transcript_window_start = window.start
        p.transcript_window_size = window.visible_count
        p.transcript_total = window.total
        p.transcript_rendered_keys = []
        p.chat_container.clear()
        if not display_msgs:
            with p.chat_container:
                ui.label("Ask anything...").classes("text-grey-5 text-sm q-pa-md")
            _log_sync("empty", 0)
            return

        chunk_state = {"idx": 0}

        def _reconcile_chunk() -> None:
            if (
                p.transcript_generation != sync_generation
                or p.transcript_thread_id != state.thread_id
            ):
                return
            start_idx = chunk_state["idx"]
            end_idx = start_idx
            chunk_started = time.perf_counter()
            try:
                with p.chat_container:
                    while end_idx < len(display_msgs):
                        add_chat_message(display_msgs[end_idx], p, state.thread_id)
                        p.transcript_rendered_keys.append(display_keys[end_idx])
                        end_idx += 1
                        if end_idx - start_idx >= TRANSCRIPT_MAX_CHUNK_MESSAGES:
                            break
                        elapsed_ms = (time.perf_counter() - chunk_started) * 1000.0
                        if elapsed_ms >= TRANSCRIPT_CHUNK_TARGET_MS:
                            break
            except Exception:
                logger.debug("Transcript reconcile failed", exc_info=True)
                return
            chunk_state["idx"] = end_idx
            if end_idx < len(display_msgs):
                defer_ui(_reconcile_chunk)
            else:
                _scroll_bottom()
                _log_sync("reconcile_window", len(display_msgs))

        defer_ui(_reconcile_chunk)

    cb.refresh_chat_messages = _refresh_chat_messages

    # ── Timers ───────────────────────────────────────────────────────────

    def _poll_notifications() -> None:
        for t in drain_toasts():
            _tkw = {"type": t.get("type", "info"), "close_button": True}
            if t.get("persistent"):
                _tkw["timeout"] = 0
            else:
                _tkw["timeout"] = 5000
            ui.notify(t["message"], **_tkw)
            rebuild_thread_list()

    _last_buddy_voice_state = [""]

    def _poll_voice() -> None:
        if not state.voice_enabled:
            if p.voice_status_label:
                p.voice_status_label.text = ""
            _last_buddy_voice_state[0] = ""
            return

        svc = state.voice_coordinator
        new_status = svc.get_status()
        st = svc.state
        if svc.transport == "realtime":
            if p.voice_status_label:
                if st == "connecting":
                    p.voice_status_label.text = "Connecting realtime Talk..."
                elif st in {"connected", "listening"}:
                    p.voice_status_label.text = "Realtime Talk - listening"
                elif st == "thinking":
                    p.voice_status_label.text = "Thinking..."
                elif st == "waiting_for_approval":
                    p.voice_status_label.text = "Waiting for approval..."
                elif st == "speaking":
                    p.voice_status_label.text = "Speaking..."
                elif st == "error":
                    p.voice_status_label.text = f"Realtime error: {new_status or 'connection failed'}"
                else:
                    p.voice_status_label.text = str(new_status or st)
            return
        if st != _last_buddy_voice_state[0]:
            _last_buddy_voice_state[0] = st
            try:
                from row_bot.buddy.events import BuddyEventType, emit_buddy_event
                if st in {"listening", "transcribing", "muted"}:
                    emit_buddy_event(
                        BuddyEventType.VOICE_LISTENING,
                        source="app.voice",
                        payload={"label": "Listening" if st == "listening" else "Voice active", "state": st},
                    )
            except Exception:
                logger.debug("Buddy voice event failed", exc_info=True)
        if p.voice_status_label:
            if st == "listening":
                p.voice_status_label.text = "🔴 Listening — speak now…"
            elif st == "transcribing":
                p.voice_status_label.text = "⏳ Processing…"
            elif st == "muted":
                tts = state.tts_service
                if tts and not tts.is_speaking:
                    svc.unmute()
                    p.voice_status_label.text = "🔴 Listening — speak now…"
                else:
                    p.voice_status_label.text = "🔇 Speaking…"
            elif st == "stopped":
                p.voice_status_label.text = f"⚫ {new_status or 'Stopped'}"

        if p.voice_status_label and state.voice_input_mode == "dictate":
            if st == "listening":
                p.voice_status_label.text = "Dictating - speak now..."
            elif st == "transcribing":
                p.voice_status_label.text = "Transcribing dictation..."
            elif st == "muted":
                p.voice_status_label.text = "Adding dictation..."
        elif p.voice_status_label and state.voice_input_mode == "talk":
            gen = _active_generations.get(state.thread_id)
            if gen:
                if gen.interrupt_data or state.pending_interrupt:
                    p.voice_status_label.text = "Waiting for approval..."
                elif gen.pending_tools:
                    tool_names = {str(tool.get("name") or "") for tool in gen.pending_tools.values() if isinstance(tool, dict)}
                    if any("browser" in name.lower() for name in tool_names):
                        p.voice_status_label.text = "Using Browser..."
                    else:
                        p.voice_status_label.text = "Using Tool..."
                elif state.tts_service and state.tts_service.is_speaking:
                    p.voice_status_label.text = "Speaking..."
                else:
                    p.voice_status_label.text = "Thinking..."

        text = svc.get_transcription()
        if text and state.voice_input_mode == "dictate":
            binding = getattr(p, "active_voice_binding", None)
            if binding is not None and binding.is_current(state.thread_id):
                binding.append_dictation(text)
            elif p.chat_input:
                from row_bot.voice.actions import append_dictation_text

                p.chat_input.value = append_dictation_text(str(p.chat_input.value or ""), text)
                p.chat_input.update()
            else:
                logger.info(
                    "voice.realtime.pipeline %s",
                    {
                        "stage": "dictation_dropped_stale_binding",
                        "thread_id": state.thread_id,
                        "text_chars": len(str(text or "")),
                    },
                )
                svc.unmute()
                return
            svc.unmute()
            if p.voice_status_label:
                p.voice_status_label.text = "Dictation added to composer"
        elif text:
            if state.tts_service and state.tts_service.enabled:
                state.tts_service.stop()
            if state.thread_id and should_auto_rename_thread(state.thread_id, state.thread_name):
                state.thread_name = rename_thread(state.thread_id, text[:50], source="auto")
                rebuild_thread_list()
            asyncio.create_task(_voice_bridge.submit_user_transcript(text))

    _token_counter_state = {
        "in_flight": False,
        "key": None,
        "last": None,
        "scheduled_key": None,
        "generation": 0,
    }

    def _render_token_counter(used: int, max_tokens: int) -> None:
        pct = min(used / max_tokens, 1.0) if max_tokens else 0.0

        def _fmt(val):
            if val >= 1_000_000:
                return f"{val / 1_000_000:.1f}M"
            if val >= 1_000:
                return f"{val / 1_000:.1f}K"
            return str(val)

        if p.token_label:
            p.token_label.text = f"Context: {_fmt(used)} / {_fmt(max_tokens)} ({pct:.0%})"
        if p.token_bar:
            p.token_bar.value = pct

    async def _refresh_token_counter_async(key, config, model_override) -> None:
        started = time.perf_counter()
        try:
            used, max_tokens = await run.io_bound(
                lambda: get_token_usage(config, model_override=model_override)
            )
        except Exception:
            logger.debug("Token counter refresh failed", exc_info=True)
            return
        finally:
            _token_counter_state["in_flight"] = False

        elapsed = time.perf_counter() - started
        if elapsed >= 1.0:
            logger.info("perf: token counter refresh took %.3fs", elapsed)
        current_key = (
            state.thread_id,
            state.thread_model_override or "",
            len(state.messages),
        )
        if key != current_key:
            return
        _token_counter_state["last"] = (used, max_tokens)
        _render_token_counter(used, max_tokens)

    async def _schedule_token_counter_async(key, config, model_override, generation: int) -> None:
        await asyncio.sleep(0.75)
        current_key = (
            state.thread_id,
            state.thread_model_override or "",
            len(state.messages),
        )
        if generation != _token_counter_state["generation"] or key != current_key:
            if _token_counter_state.get("scheduled_key") == key:
                _token_counter_state["scheduled_key"] = None
            return
        if _token_counter_state["in_flight"]:
            return
        _token_counter_state["scheduled_key"] = None
        _token_counter_state["in_flight"] = True
        await _refresh_token_counter_async(key, config, model_override)

    def _update_token_counter() -> None:
        if state.is_generating or (state.thread_id and state.thread_id in _active_generations):
            return
        config = {"configurable": {"thread_id": state.thread_id}} if state.thread_id else None
        model_override = state.thread_model_override or None
        key = (
            state.thread_id,
            state.thread_model_override or "",
            len(state.messages),
        )
        if _token_counter_state["in_flight"]:
            return
        if _token_counter_state["key"] == key and _token_counter_state.get("last"):
            last = _token_counter_state.get("last")
            _render_token_counter(*last)
            return
        if _token_counter_state.get("scheduled_key") == key:
            return
        _token_counter_state["key"] = key
        _token_counter_state["scheduled_key"] = key
        _token_counter_state["generation"] += 1
        generation = _token_counter_state["generation"]
        safe_ui_task(
            lambda: _schedule_token_counter_async(key, config, model_override, generation),
            context="token counter refresh",
        )

    _notification_timer = safe_timer(1.0, _poll_notifications)
    _voice_timer = safe_timer(0.3, _poll_voice)
    _token_timer = safe_timer(5.0, _update_token_counter)
    deactivate_on_disconnect(_notification_timer, _voice_timer, _token_timer)

    # ── Build initial view ───────────────────────────────────────────────
    _rebuild_main()
    try:
        from row_bot.ui.onboarding_state import consume_setup_center_on_next_load

        should_open_setup_center = consume_setup_center_on_next_load()
    except Exception:
        logger.exception("Failed to consume setup center launch request")
        should_open_setup_center = False
    should_open_setup_center = bool(getattr(state, "open_setup_center_on_next_load", False)) or should_open_setup_center
    if should_open_setup_center:
        state.open_setup_center_on_next_load = False
        client = ui.context.client

        def _open_setup_center_after_first_run() -> None:
            from row_bot.ui.onboarding_center import show_setup_center

            with client:
                show_setup_center(
                    open_settings=_open_settings,
                    rebuild_main=_rebuild_main,
                    state=state,
                )

        defer_ui(_open_setup_center_after_first_run, delay=0.15)
    try:
        from row_bot.ui.post_migration import maybe_show_post_migration_report

        maybe_show_post_migration_report(open_settings=_open_settings)
    except Exception:
        logger.exception("Failed to render post-migration report")
    _update_token_counter()


@ui.page("/buddy-overlay")
async def buddy_overlay():
    ui.dark_mode(True)
    from row_bot.ui.buddy import build_buddy_overlay_page
    build_buddy_overlay_page()


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ in {"__main__", "__mp_main__"}:
    _static_dir = str(static_dir())
    if os.path.isdir(_static_dir):
        app.add_static_files("/static", _static_dir)

    from row_bot.buddy.assets import buddy_static_dir

    _buddy_static_dir = buddy_static_dir()
    _buddy_static_dir.mkdir(parents=True, exist_ok=True)
    app.add_static_files("/_buddy", str(_buddy_static_dir))

    from row_bot.designer.publish import ensure_published_dir

    _published_dir = str(ensure_published_dir())
    app.add_static_files("/published", _published_dir)

    # Serve per-thread media files (generated videos, etc.)
    from row_bot.threads import _MEDIA_DIR
    app.add_static_files("/_media", str(_MEDIA_DIR))

    # Serve user-downloaded font cache
    _font_cache = str(get_row_bot_data_dir() / "font_cache")
    if os.path.isdir(_font_cache):
        app.add_static_files("/_fonts/cache", _font_cache)

    _native = "--native" in sys.argv
    _show = "--show" in sys.argv and not _native

    _run_kwargs = {
        "title": APP_DISPLAY_NAME,
        "port": _APP_PORT,
        "dark": True,
        "favicon": Path(_static_dir) / "favicon.ico",
        "reload": False,
        "show": _show,
        "native": _native,
        "window_size": (1280, 900) if _native else None,
    }
    if _APP_HOST:
        _run_kwargs["host"] = _APP_HOST

    ui.run(**_run_kwargs)
