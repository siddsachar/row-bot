"""Row-Bot's server: the React client at /app-v2/ and its API on FastAPI/uvicorn.

Run:   python app.py              →   http://localhost:8080
    ROW_BOT_PORT=8081 python app.py → http://localhost:8081
"""

from __future__ import annotations

import asyncio
import atexit
import builtins
from contextlib import contextmanager
import hmac
import logging
import os
import sys
import time

_APP_BOOT_STARTED = time.perf_counter()
_LAUNCH_SESSION_ID = os.environ.get("ROW_BOT_LAUNCH_SESSION_ID", "")
_FIRST_LAUNCHER_PING_LOGGED = False
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
               "watchfiles", "uvicorn.error", "uvicorn.access",
               "sentence_transformers", "transformers", "huggingface_hub",
               "googleapiclient", "googleapiclient.discovery_cache",
               "primp", "ddgs", "ddgs.ddgs", "faster_whisper",
               "streamlit", "kaleido", "choreographer", "pyngrok",
               "pyngrok.process", "pyngrok.process.ngrok"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
for _discord_noisy in _DISCORD_BENIGN_VOICE_LOGGERS:
    logging.getLogger(_discord_noisy).setLevel(logging.ERROR)

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
# A confirmed restore from a backup applies here, before anything below opens
# the profile's databases; the current profile is kept aside (decision 21).
if __name__ == "__main__":
    from row_bot.profile_restore import apply_on_start

    apply_on_start()
from row_bot.brand import APP_DISPLAY_NAME, APP_PING_ID, APP_USER_AGENT
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.access.launcher_control import LAUNCH_SECRET_ENV
from row_bot.docs_capture import (
    docs_capture_disable_autostart,
    is_docs_read_only_real_data_capture,
    is_docs_real_data_capture,
)
from row_bot.runtime_paths import static_dir
from row_bot.version import __version__ as _app_version
os.environ.setdefault("USER_AGENT", APP_USER_AGENT)

logger = logging.getLogger(__name__)


def _app_boot_event(event: str, **fields) -> None:
    elapsed_ms = (time.perf_counter() - _APP_BOOT_STARTED) * 1000.0
    compact = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger.info(
        "app.boot.%s elapsed_ms=%.1f session=%s%s%s",
        event,
        elapsed_ms,
        _LAUNCH_SESSION_ID,
        " " if compact else "",
        compact,
    )


_app_boot_event("module_logger_ready", python=sys.executable, cwd=os.getcwd())


def _startup_warning(message: str, *, source: str = "startup") -> None:
    """A start-up problem: shown once in the React app and listed in Monitor
    (parity row 11)."""
    from row_bot.application import startup_state

    startup_state.warnings.append(message)
    try:
        from row_bot.application.app_notices import app_notices

        app_notices.startup_warning(message, source=source)
    except Exception:
        logger.debug("Start-up notice failed (non-fatal)", exc_info=True)


def _close_tunnels_at_interpreter_exit() -> None:
    from row_bot.tunnel import close_tunnels_on_exit

    close_tunnels_on_exit("interpreter exit")


atexit.register(_close_tunnels_at_interpreter_exit)


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
    setup_stability_monitoring,
    start_performance_monitor,
    stop_performance_monitor,
)

setup_stability_monitoring()

if not is_docs_real_data_capture():
    try:
        from row_bot.startup_diagnostics import preflight_optional_native_packages

        preflight_optional_native_packages(logger)
    except Exception:
        logger.debug("Startup diagnostics failed", exc_info=True)

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from row_bot.app_port import get_app_host, get_app_port
from row_bot.server import app, on_shutdown, on_startup

_APP_PORT = get_app_port()
_APP_HOST = get_app_host()

# ── Backend imports ──────────────────────────────────────────────────────────
from row_bot.application import startup_state
from row_bot.models import get_current_model, is_cloud_available
from row_bot.api_keys import apply_keys
from row_bot.memory_extraction import schedule_idle_extraction, start_periodic_extraction
from row_bot.dream_cycle import start_dream_loop
from row_bot.tasks import (
    ensure_task_schema,
    seed_default_tasks,
    start_task_scheduler,
)

# ── Channels ─────────────────────────────────────────────────────────────────
from row_bot.channels import config as _ch_config
from row_bot.channels import registry as _ch_registry

_CHANNEL_MODULES = (
    "row_bot.channels.telegram",
    "row_bot.channels.slack",
    "row_bot.channels.sms",
    "row_bot.channels.discord_channel",
    "row_bot.channels.whatsapp",
)


def _load_channel_modules() -> list[str]:
    """Import channel adapters so installed extras self-register."""
    import importlib

    skipped: list[str] = []
    for module_name in _CHANNEL_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            skipped.append(f"{module_name}: {exc}")
            logger.info("Optional channel module skipped: %s (%s)", module_name, exc)
    return skipped


def _startup_fields(**fields) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ")[:200]
        parts.append(f"{key}={text}")
    return " ".join(parts)


@contextmanager
def _startup_phase(name: str, **fields):
    started = time.perf_counter()
    try:
        yield
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "startup.phase name=%s duration_ms=%.1f success=false %s",
            name,
            duration_ms,
            _startup_fields(**fields),
        )
        raise
    else:
        duration_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "startup.phase name=%s duration_ms=%.1f success=true %s",
            name,
            duration_ms,
            _startup_fields(**fields),
        )


def _schedule_background_task(coro, *, name: str):
    task = asyncio.create_task(coro, name=name)

    def _log_failure(done_task: asyncio.Task) -> None:
        if done_task.cancelled():
            return
        try:
            exc = done_task.exception()
        except Exception as callback_exc:
            logger.debug("Could not inspect background task %s: %s", name, callback_exc)
            return
        if exc is not None:
            logger.warning("Background startup task %s failed: %s", name, exc, exc_info=exc)

    task.add_done_callback(_log_failure)
    return task


async def _repair_orchestration_recovery_batch(after_id: str = "") -> None:
    """Run one post-ready local repair batch and continue only if needed."""

    from row_bot.agent_orchestrator import repair_interrupted_orchestrations_batch

    result = await asyncio.to_thread(
        repair_interrupted_orchestrations_batch,
        limit=20,
        after_id=after_id,
    )
    if int(result.get("processed") or 0):
        logger.info("Deferred orchestration recovery: %s", result)
    if result.get("has_more"):
        await asyncio.sleep(0)
        _schedule_background_task(
            _repair_orchestration_recovery_batch(
                str(result.get("next_cursor") or after_id)
            ),
            name="row-bot-orchestration-recovery-next",
        )


async def _auto_start_channel_background(channel) -> None:
    channel_name = str(getattr(channel, "name", "") or "")
    display_name = str(getattr(channel, "display_name", channel_name) or channel_name)
    started = time.perf_counter()
    ok = False
    try:
        ok = bool(await channel.start())
        if ok:
            _safe_console_print(f"[startup] ✅ {display_name} auto-started")
            from row_bot.channels.registry import clear_agent_cache_if_loaded

            clear_agent_cache_if_loaded()
            try:
                from row_bot.channels.thread_notifications import reconcile_pending_channel_notifications

                delivered = await asyncio.to_thread(reconcile_pending_channel_notifications, 25)
                from row_bot.agent_orchestrator import retry_pending_deliveries

                await asyncio.to_thread(retry_pending_deliveries, 25)
                if delivered:
                    logger.info(
                        "startup.channel.pending_notifications_delivered channel=%s count=%d",
                        channel_name,
                        delivered,
                    )
            except Exception:
                logger.debug("Channel notification reconciliation failed", exc_info=True)
        else:
            _startup_warning(
                f"{display_name} didn't start. Check it in Settings › Channels.",
                source="channels",
            )
    except Exception as exc:
        _startup_warning(
            f"{display_name} didn't start. Check it in Settings › Channels.",
            source="channels",
        )
        logger.warning("Channel auto-start failed for %s: %s", channel_name, exc)
    finally:
        logger.info(
            "startup.channel.auto_start channel=%s duration_ms=%.1f ok=%s",
            channel_name,
            (time.perf_counter() - started) * 1000.0,
            ok,
        )


async def _auto_start_channels_background(channels: list) -> None:
    logger.info("startup.channels.auto_start_begin count=%d", len(channels))
    for channel in channels:
        await _auto_start_channel_background(channel)
    try:
        from row_bot.channels.thread_notifications import reconcile_pending_channel_notifications

        delivered = await asyncio.to_thread(reconcile_pending_channel_notifications, 50)
        from row_bot.agent_orchestrator import retry_pending_deliveries

        await asyncio.to_thread(retry_pending_deliveries, 50)
        if delivered:
            logger.info("startup.channels.pending_notifications_delivered count=%d", delivered)
    except Exception:
        logger.debug("Channel notification reconciliation after auto-start failed", exc_info=True)
    logger.info("startup.channels.auto_start_complete count=%d", len(channels))


def _schedule_auto_start_channels(channels: list):
    if not channels:
        logger.info("startup.channels.auto_start_none")
        return None
    for channel in channels:
        logger.info(
            "startup.channel.auto_start_scheduled channel=%s",
            getattr(channel, "name", ""),
        )
    return _schedule_background_task(
        _auto_start_channels_background(channels),
        name="row-bot-channel-autostart",
    )


async def _prewarm_agent_graph_background() -> None:
    with _startup_phase("agent_graph_prewarm", background=True):
        from row_bot.agent import get_agent_graph
        from row_bot.models import NoModelChosenError
        from row_bot.providers.readiness import AgentCompatibilityError

        try:
            await asyncio.to_thread(get_agent_graph)
        except AgentCompatibilityError as exc:
            logger.info("startup.agent_graph_prewarm skipped=agent_not_ready reason=%s", exc)
        except NoModelChosenError:
            # A fresh profile has no model until the person chooses one (decision 9).
            logger.info("startup.agent_graph_prewarm skipped=no_model_chosen")


def _schedule_agent_graph_prewarm():
    if os.environ.get("ROW_BOT_DEPLOYMENT_MODE", "desktop").strip().lower() == "server":
        logger.info("startup.agent_graph_prewarm skipped=server_mode")
        return None
    return _schedule_background_task(
        _prewarm_agent_graph_background(),
        name="row-bot-agent-graph-prewarm",
    )


async def _prewarm_local_embeddings_background() -> None:
    """Warm cached local embeddings only when semantic memory can use them."""
    with _startup_phase("local_embedding_prewarm", background=True):
        from row_bot.embedding_config import get_embedding_config

        config = get_embedding_config()
        if config.get("provider") != "local":
            logger.info("startup.local_embedding_prewarm skipped=cloud_provider")
            return
        from row_bot import knowledge_graph

        vector_status = await asyncio.to_thread(knowledge_graph.memory_vector_status)
        entity_count = await asyncio.to_thread(knowledge_graph.count_entities)
        if not vector_status.get("ready") or entity_count <= 0:
            logger.info(
                "startup.local_embedding_prewarm skipped=memory_index_%s entities=%d",
                vector_status.get("state"),
                entity_count,
            )
            return
        from row_bot.embedding_providers import start_local_embedding_load

        load_status = start_local_embedding_load()
        logger.info(
            "startup.local_embedding_prewarm state=%s model=%s",
            load_status.get("state"),
            load_status.get("model_key"),
        )


def _schedule_local_embedding_prewarm():
    return _schedule_background_task(
        _prewarm_local_embeddings_background(),
        name="row-bot-local-embedding-prewarm",
    )


# ═════════════════════════════════════════════════════════════════════════════
# VOICE OWNER
# ═════════════════════════════════════════════════════════════════════════════

# The process's one voice owner: React Talk and Dictation share the
# microphone through it (bound to the client platform below).
from row_bot.voice import get_voice_service
from row_bot.voice.coordinator import VoiceSessionCoordinator

_voice_coordinator = VoiceSessionCoordinator(get_voice_service())


# ═════════════════════════════════════════════════════════════════════════════
# OAUTH TOKEN HEALTH
# ═════════════════════════════════════════════════════════════════════════════

def _check_oauth_tokens(at_startup: bool = False) -> list[str]:
    """Check Gmail & Calendar OAuth tokens if those tools are enabled.

    Attempts silent refresh when possible.  Returns a list of warning
    strings (empty if everything is healthy).  At start-up the warnings are
    also start-up warnings.
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

    if at_startup:
        for warning in warnings:
            _startup_warning(warning, source="accounts")
    return warnings


def _check_github_account_health(at_startup: bool = False) -> list[str]:
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

    if at_startup:
        for warning in warnings:
            _startup_warning(warning, source="accounts")
    return warnings


def _periodic_oauth_check():
    """Background OAuth health check — runs every 6 hours."""
    warnings = _check_oauth_tokens()
    warnings.extend(_check_github_account_health())
    if warnings:
        from row_bot.notifications import notify as _oauth_notify
        for msg in warnings:
            _oauth_notify("Account Issue", msg, sound="default",
                          toast_type="warning", source="accounts")


# ═════════════════════════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ═════════════════════════════════════════════════════════════════════════════

@on_startup
async def _start():
    startup_state.ready = False
    startup_state.status = "Starting Row-Bot..."
    _app_boot_event("startup_shell_ready", host=_APP_HOST, port=_APP_PORT)
    logger.info(
        "%s startup shell ready; scheduling background startup (session=%s)",
        APP_DISPLAY_NAME,
        os.environ.get("ROW_BOT_LAUNCH_SESSION_ID", ""),
    )
    asyncio.create_task(_run_startup_sequence_guarded(), name="row-bot-startup-sequence")


async def _run_startup_sequence_guarded():
    startup_total_started = time.perf_counter()
    try:
        await _run_startup_sequence()
    except Exception as exc:
        startup_state.status = f"Startup error: {exc}"
        _startup_warning(
            "Row-Bot didn't finish starting. Some features may be unavailable; Monitor shows which.",
        )
        logger.info(
            "startup.phase name=startup_sequence_total duration_ms=%.1f success=false",
            (time.perf_counter() - startup_total_started) * 1000.0,
        )
        logger.exception("%s background startup failed", APP_DISPLAY_NAME)
    else:
        logger.info(
            "startup.phase name=startup_sequence_total duration_ms=%.1f success=true",
            (time.perf_counter() - startup_total_started) * 1000.0,
        )


async def _run_startup_sequence():
    _app_boot_event("startup_sequence_start")
    if is_docs_read_only_real_data_capture():
        startup_state.status = "Read-only Settings capture ready"
        startup_state.ready = True
        _safe_console_print("[startup] Authorized real-data capture - startup writes suppressed")
        _app_boot_event("startup_real_data_capture_ready")
        return
    install_asyncio_exception_handler()
    start_performance_monitor()
    # Attach persistent file logging (daily JSONL to the Row-Bot data dir).
    from row_bot.logging_config import setup_file_logging
    with _startup_phase("file_logging"):
        setup_file_logging()

    from row_bot.application.lifecycle import application_lifecycle
    with _startup_phase("client_platform_recovery"):
        await application_lifecycle.startup()

    if docs_capture_disable_autostart():
        startup_state.status = "Docs capture ready"
        startup_state.ready = True
        _safe_console_print("[startup] Docs capture enabled - background autostart skipped")
        _app_boot_event("startup_docs_capture_ready")
        return

    try:
        from row_bot.startup_diagnostics import preflight_required_runtime_packages
        with _startup_phase("required_runtime_diagnostics"):
            preflight_required_runtime_packages(logger)
    except Exception:
        logger.debug("Required runtime diagnostics failed", exc_info=True)

    # One-shot: clear project_id on thread_meta rows whose designer
    # project JSON is missing. Prevents the "All Conversations" view
    # from showing threads that claim to belong to a deleted project.
    try:
        from row_bot.threads import sweep_orphan_project_ids
        with _startup_phase("orphan_project_sweep"):
            sweep_orphan_project_ids()
    except Exception:
        logger.exception("Orphan project_id sweep failed")

    # One-shot: temp and splash files Row-Bot left in the data folder, by
    # exact name pattern only; this launch's splash stays (B126).
    try:
        from row_bot.data_paths import get_row_bot_data_dir
        from row_bot.thread_cleanup import sweep_data_dir_leftovers
        with _startup_phase("data_dir_leftovers"):
            sweep_data_dir_leftovers(get_row_bot_data_dir(), keep_launch=_LAUNCH_SESSION_ID)
    except Exception:
        logger.exception("Data folder leftover sweep failed")

    logger.info("%s startup initiated", APP_DISPLAY_NAME)
    try:
        from row_bot.data_paths import describe_data_paths
        logger.info("%s data paths: %s", APP_DISPLAY_NAME, describe_data_paths())
    except Exception:
        logger.debug("Could not describe %s data paths", APP_DISPLAY_NAME, exc_info=True)

    def _set(msg: str):
        startup_state.status = msg
        _app_boot_event("startup_phase", status=msg)
        _safe_console_print(f"[startup] {msg}")

    _set("🔑 Applying API keys…")
    with _startup_phase("apply_keys"):
        await asyncio.to_thread(apply_keys)

    # Profiles that finished setup while presets applied keep the models they
    # were running on, written once as their own choices (decision 9).
    try:
        from row_bot.application.model_choice_migration import migrate_legacy_presets
        with _startup_phase("legacy_model_choices"):
            await asyncio.to_thread(migrate_legacy_presets)
    except Exception:
        logger.warning("Could not keep the models this profile was using", exc_info=True)

    if is_cloud_available():
        _set("☁️ Loading cached model catalog...")
        with _startup_phase("load_cached_model_catalog"):
            get_current_model()

    _set("🔄 Scheduling memory extraction…")
    with _startup_phase("memory_extraction_scheduler"):
        await asyncio.to_thread(start_periodic_extraction)
        await asyncio.to_thread(schedule_idle_extraction)

    _set("Recovering document ingestion…")
    try:
        from row_bot.document_jobs import ensure_document_supervisor

        with _startup_phase("document_ingestion_supervisor"):
            await asyncio.to_thread(ensure_document_supervisor)
    except Exception as exc:
        logger.warning(
            "Document ingestion startup recovery skipped (non-fatal): %s",
            exc,
        )
        _startup_warning(
            "Document ingestion could not start. Existing document search remains available; "
            "restart Row-Bot or review System Diagnosis before uploading more files.",
            source="documents",
        )

    _set("🌙 Starting dream cycle daemon…")
    with _startup_phase("dream_cycle_daemon"):
        await asyncio.to_thread(start_dream_loop)

    _set("⬆ Starting auto-update scheduler…")
    try:
        from row_bot.updater import start_update_scheduler
        with _startup_phase("update_scheduler"):
            await asyncio.to_thread(start_update_scheduler)
    except Exception as exc:
        logger.warning("Updater scheduler failed to start (non-fatal): %s", exc)

    _set("⚡ Loading workflows…")
    try:
        with _startup_phase("workflow_scheduler"):
            await asyncio.to_thread(ensure_task_schema)
            await asyncio.to_thread(lambda: (seed_default_tasks(), start_task_scheduler()))
    except Exception as exc:
        logger.warning("Workflow startup skipped after task DB repair failure: %s", exc)
        _startup_warning(
            "Workflow data is temporarily unavailable. "
            "Run launcher.py --reset-tasks-db if it does not recover after restart.",
            source="workflow",
        )

    _set("Recovering Agent runs...")
    try:
        from row_bot.agent_runs import recover_stale_agent_runs

        with _startup_phase("agent_run_recovery"):
            recovery = await asyncio.to_thread(recover_stale_agent_runs)
        if any(int(value or 0) for value in recovery.values()):
            logger.info("Agent Run startup recovery: %s", recovery)
    except Exception as exc:
        logger.warning("Agent Run startup recovery skipped (non-fatal): %s", exc)

    # ── Load Plugins ────────────────────────────────────────────────────────
    _set("🔌 Loading plugins…")
    try:
        from row_bot.plugins.loader import refresh_plugin_runtime
        with _startup_phase("plugin_runtime_refresh"):
            results = await asyncio.to_thread(
                refresh_plugin_runtime,
                "startup",
                discover_mcp=False,
                clear_agent=False,
            )
        loaded = sum(1 for r in results if r.success and not getattr(r, "stale", False))
        failed = sum(1 for r in results if not r.success)
        stale = sum(1 for r in results if getattr(r, "stale", False))
        if loaded or failed or stale:
            _safe_console_print(
                f"[startup] 🔌 Plugins: {loaded} loaded, {failed} failed, {stale} stale"
            )
        for r in results:
            if not r.success and r.error:
                _startup_warning(f"The plugin '{r.plugin_id}' didn't load: {r.error}", source="plugins")
            elif getattr(r, "stale", False):
                _startup_warning(
                    f"The legacy plugin '{r.plugin_id}' moved to stale plugins.", source="plugins"
                )
    except Exception as exc:
        logger.warning("Plugin loading failed (non-fatal): %s", exc)

    _set("🔌 Starting MCP servers…")
    try:
        from row_bot.mcp_client.runtime import discover_enabled_servers
        with _startup_phase("mcp_discovery"):
            await asyncio.to_thread(discover_enabled_servers)
    except Exception as exc:
        logger.warning("MCP startup skipped (non-fatal): %s", exc)

    # Prepare channels via registry. Actual live channel handshakes run after
    # core UI readiness so slow providers do not block the local app shell.
    _set("📡 Preparing channels…")
    # Ensure channel modules are imported so they self-register.
    with _startup_phase("channel_module_import"):
        skipped_channels = _load_channel_modules()
    for skipped_channel in skipped_channels:
        _startup_warning(
            f"Channel adapter unavailable: {skipped_channel}. "
            "Install the channels extra to enable it.",
            source="channels",
        )
    try:
        from row_bot.channels.auth_store import migrate_legacy_channel_secrets
        with _startup_phase("channel_secret_migration"):
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
    auto_start_channels = []
    with _startup_phase("channel_auto_start_plan"):
        for _ch in _ch_registry.all_channels():
            auto_start = bool(_ch_config.get(_ch.name, "auto_start", False))
            logger.info(
                "startup.channel.auto_start_config channel=%s enabled=%s",
                _ch.name,
                auto_start,
            )
            if auto_start:
                auto_start_channels.append(_ch)

    # An earlier run that crashed or was stopped by force may have left its
    # ngrok agent (and public address) running. Stop only agents Row-Bot
    # recorded as its own, before the tunnel or a channel could open a new
    # one. (After the read-only capture modes have returned.)
    try:
        from row_bot.tunnel import cleanup_owned_agents
        with _startup_phase("owned_tunnel_cleanup"):
            await asyncio.to_thread(cleanup_owned_agents)
    except Exception as exc:
        logger.warning("Owned tunnel cleanup skipped (non-fatal): %s", exc)

    # Auto-start tunnel if it was enabled before restart
    _main_app_tunnel = _ch_config.get("tunnel", "tunnel_main_app", False)
    if isinstance(_main_app_tunnel, list) and _main_app_tunnel:
        _main_app_tunnel = _main_app_tunnel[0] is True
        _ch_config.set("tunnel", "tunnel_main_app", _main_app_tunnel)
    if _main_app_tunnel is True:
        _set("🌐 Starting remote access tunnel…")
        try:
            from row_bot.tunnel import tunnel_manager
            with _startup_phase("main_app_tunnel_autostart"):
                if tunnel_manager.is_available():
                    await asyncio.to_thread(tunnel_manager.start_tunnel, _APP_PORT, label="main_app")
                    _safe_console_print(f"[startup] ✅ Main-app tunnel auto-started on port {_APP_PORT}")
                else:
                    _status_code, status_detail = tunnel_manager.status()
                    logger.warning("Tunnel auto-start skipped: %s", status_detail)
                    _startup_warning(f"The public tunnel didn't start: {status_detail}", source="tunnel")
        except Exception as exc:
            from row_bot.tunnel import describe_tunnel_error
            _startup_warning(f"The public tunnel didn't start. {describe_tunnel_error(exc)}", source="tunnel")

    # ── Proactive OAuth token health check ───────────────────────────
    with _startup_phase("oauth_token_health_check"):
        await asyncio.to_thread(_check_oauth_tokens, True)
    with _startup_phase("github_account_health_check"):
        await asyncio.to_thread(_check_github_account_health, True)

    # Schedule periodic re-check every 6 hours
    try:
        from row_bot.tasks import _get_scheduler
        with _startup_phase("oauth_periodic_scheduler"):
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
        from row_bot.thread_cleanup import run_idle_maintenance

        def _run_checkpoint_cleanup() -> None:
            try:
                from row_bot.memory_extraction import is_app_idle
                if not is_app_idle():
                    logger.info("Checkpoint cleanup deferred; app is active")
                    return
                # Includes cleanup_old_checkpoints, safe orphan repair, and
                # thresholded SQLite reclamation in one serialized idle pass.
                run_idle_maintenance()
            except Exception:
                logger.debug("Checkpoint cleanup failed", exc_info=True)

        with _startup_phase("checkpoint_cleanup_scheduler"):
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
        def _evict_idle_browser_tabs() -> None:
            try:
                from row_bot.tools.browser_tool import get_session_manager as _get_bs_mgr

                closed = _get_bs_mgr().evict_idle(ttl_seconds=600.0)
                if closed:
                    logger.info("browser: evicted %d idle tab(s)", closed)
            except Exception:
                logger.debug("browser idle eviction failed", exc_info=True)

        with _startup_phase("browser_idle_eviction_scheduler"):
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
    startup_state.ready = True
    _schedule_background_task(
        _repair_orchestration_recovery_batch(),
        name="row-bot-orchestration-recovery",
    )
    _schedule_agent_graph_prewarm()
    _schedule_local_embedding_prewarm()
    _schedule_auto_start_channels(auto_start_channels)
    _app_boot_event("startup_sequence_complete")
    logger.info("%s startup complete", APP_DISPLAY_NAME)


# ── Webhook API Route ────────────────────────────────────────────────────────


def _launcher_request_authorized(request: Request) -> bool:
    """Validate the ephemeral secret shared only with the owning launcher."""
    expected = os.environ.get(LAUNCH_SECRET_ENV, "")
    authorization = str(request.headers.get("authorization") or "")
    return bool(
        len(expected) >= 32
        and hmac.compare_digest(authorization, f"Bearer {expected}")
    )


async def _launcher_ping_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Identify this process to the desktop launcher."""
    if not _launcher_request_authorized(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    global _FIRST_LAUNCHER_PING_LOGGED
    if not _FIRST_LAUNCHER_PING_LOGGED:
        _FIRST_LAUNCHER_PING_LOGGED = True
        _app_boot_event("first_launcher_ping", port=_APP_PORT)
    return JSONResponse({"app": APP_PING_ID, "version": _app_version, "port": _APP_PORT})


async def _startup_state_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Expose startup state for the browser-side splash handoff."""
    if not _launcher_request_authorized(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return JSONResponse({
        "ready": bool(startup_state.ready),
        "status": str(startup_state.status or ""),
        "warnings": len(startup_state.warnings),
    })


async def _health_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Expose process liveness without provider, route, or user details."""
    return JSONResponse(
        {"ok": True, "status": "alive"},
        headers={"Cache-Control": "no-store"},
    )


async def _ready_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Expose only whether application startup reached its ready state."""
    ready = bool(startup_state.ready)
    return JSONResponse(
        {"ok": ready, "status": "ready" if ready else "starting"},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )


async def _webhook_handler(request: Request) -> JSONResponse:
    """Handle POST /api/webhook/{task_id} for webhook-triggered tasks."""
    task_id = request.path_params.get("task_id", "")
    from row_bot.tasks import webhook_request_secret
    # The secret travels in a header; older addresses carry ?secret= (B132).
    secret = webhook_request_secret(request.headers, request.query_params)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    from row_bot.tasks import handle_webhook
    result = handle_webhook(task_id, secret=secret, payload=payload)
    status_code = 200 if result.get("status") == "ok" else 400
    return JSONResponse(result, status_code=status_code)


async def _root_handler(request: Request) -> RedirectResponse:
    """The React client is the only UI; old bookmarks and deep links land on Home."""
    query = request.url.query
    return RedirectResponse(
        f"/app-v2/?{query}" if query else "/app-v2/",
        status_code=307,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


async def _favicon_handler(request: Request) -> FileResponse:  # noqa: ARG001
    return FileResponse(static_dir() / "favicon.ico")


_shutdown_cleanup_started = False


async def _cleanup_runtime(reason: str = "shutdown") -> bool:
    """Stop long-lived helpers before the process exits."""
    global _shutdown_cleanup_started
    if _shutdown_cleanup_started:
        return False
    _shutdown_cleanup_started = True

    cleanup_started = time.perf_counter()
    # Tunnels are public exposure: they close on every exit, including one
    # where running work has not stopped yet (B104).
    from row_bot.tunnel import close_tunnels_on_exit
    await asyncio.to_thread(close_tunnels_on_exit, reason)
    from row_bot.application.lifecycle import application_lifecycle
    runtime_shutdown = await application_lifecycle.shutdown()
    if runtime_shutdown["status"] != "quiesced":
        logger.warning("Execution cancellation is pending; owned tool resources remain available")
        _shutdown_cleanup_started = False
        return False
    stop_performance_monitor()
    mark_shutdown(reason)
    _safe_console_print(f"[shutdown] Cleaning up sessions ({reason})...")
    try:
        # Stop channels so webhook/socket clients can close cleanly.
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
        from row_bot.mcp_client.runtime import shutdown as _mcp_shutdown
        _mcp_shutdown()
        _safe_console_print("[shutdown] MCP sessions closed")
    except Exception as exc:
        _safe_console_print(f"[shutdown] MCP cleanup error: {exc}")
    _safe_console_print(f"[shutdown] Done in {(time.perf_counter() - cleanup_started):.1f}s")
    return True


async def _launcher_shutdown_handler(request: Request) -> JSONResponse:
    """Local launcher hook used for tray quit and updater handoff."""
    if not _launcher_request_authorized(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

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
app.add_route("/", _root_handler, methods=["GET"])
app.add_route("/favicon.ico", _favicon_handler, methods=["GET"])
app.add_route("/healthz", _health_handler, methods=["GET"])
app.add_route("/readyz", _ready_handler, methods=["GET"])

from starlette.middleware.gzip import GZipMiddleware
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.config import AccessConfig, canonical_host
from row_bot.access.access_routes import (
    AccessRouteConfigStore,
    discover_private_lan_addresses,
)
from row_bot.access.routes import register_access_routes
from row_bot.access.tailscale import (
    TailscaleOwnershipStore,
    augment_access_config_for_owned_tailscale,
)
from row_bot.access.runtime_policy import RuntimeAccessPolicy
from row_bot.mobile.routes import register_mobile_routes

_access_route_store = AccessRouteConfigStore()
_access_route_config = _access_route_store.load_or_default()
_host_admission_managed_externally = "ROW_BOT_ALLOWED_HOSTS" in os.environ
_access_config = AccessConfig.from_env()
_access_config = augment_access_config_for_owned_tailscale(
    _access_config,
    ownership=TailscaleOwnershipStore().load(),
    app_port=get_app_port(),
)
if (
    not _host_admission_managed_externally
    and _access_route_config.lan_enabled
):
    from dataclasses import replace as _dataclass_replace

    _lan_hosts = tuple(
        canonical_host(address, allow_port=False)
        for address in discover_private_lan_addresses()
    )
    _access_config = _dataclass_replace(
        _access_config,
        allowed_hosts=tuple(dict.fromkeys((*_access_config.allowed_hosts, *_lan_hosts))),
    )
_access_registration = register_access_routes(app, config=_access_config)
_runtime_access_policy = RuntimeAccessPolicy(
    _access_registration.config,
    configured_origins=(
        ()
        if _host_admission_managed_externally
        else _access_route_config.configured_origins
    ),
)
app.state.row_bot_access_runtime_policy = _runtime_access_policy
from row_bot.tunnel import tunnel_manager as _managed_tunnel_manager

_managed_tunnel_manager.set_managed_origin_registrar(_runtime_access_policy)
register_mobile_routes(app)
from row_bot.api.v1.routes import install_client_platform
from row_bot.application.client_platform import client_platform_service
from row_bot.application.folder_selections import FolderSelections
from row_bot.native_client import select_existing_workspace_folder
from row_bot.voice.browser_local import get_browser_local_voice_service

client_platform_service.bind_voice(_voice_coordinator,
                                   browser_service=get_browser_local_voice_service)

install_client_platform(app, client_platform_service, instance_id=client_platform_service.instance_id,
                        folder_selections=FolderSelections(picker=select_existing_workspace_folder))

from row_bot.client_assets import install_client_assets

install_client_assets(app)
app.add_middleware(
    AccessMiddleware,
    runtime_policy=_runtime_access_policy,
    session_authenticator=_access_registration.authenticator,
)
# Outermost, so responses leave compressed; Starlette never compresses
# text/event-stream, so the event stream is neither gzipped nor buffered.
app.add_middleware(GZipMiddleware)


@on_shutdown
async def _stop():
    if not await _cleanup_runtime():
        return
    try:
        from row_bot.computer_use.service import shutdown_computer_use

        shutdown_computer_use()
    except Exception:
        logger.debug("Computer Use shutdown failed", exc_info=True)


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ in {"__main__", "__mp_main__"}:
    from starlette.staticfiles import StaticFiles

    _static_dir = static_dir()
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    from row_bot.buddy.assets import buddy_static_dir

    _buddy_static_dir = buddy_static_dir()
    _buddy_static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/_buddy", StaticFiles(directory=_buddy_static_dir), name="buddy")

    from row_bot.designer.publish import ensure_published_dir

    app.mount("/published", StaticFiles(directory=ensure_published_dir()), name="published")

    # Fonts a design downloaded (designer.fonts emits /_fonts/cache URLs).
    _font_cache = get_row_bot_data_dir() / "font_cache"
    if _font_cache.is_dir():
        app.mount("/_fonts/cache", StaticFiles(directory=_font_cache), name="font-cache")

    import uvicorn

    uvicorn.run(
        app,
        host=_APP_HOST,
        port=_APP_PORT,
        log_level="warning",
        proxy_headers=False,
        # The React client uses HTTP and server-sent events only.
        ws="none",
        # Open event streams never end on their own; stop waiting for them so
        # the shutdown sequence (tunnels, channels, sessions) always runs.
        timeout_graceful_shutdown=5,
    )
