"""Thoth UI — first-launch setup wizard.

Builds the full-screen ``ui.dialog`` that walks the user through
model selection and API key configuration on first launch.

The wizard is self-contained except for two callbacks:

* **on_finish** — called (no args) when the user clicks *Get Started*.
  The caller should use this to e.g. call ``_rebuild_main()`` to
  transition to the main UI.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

from nicegui import run, ui

from ui.state import AppState

logger = logging.getLogger(__name__)


async def show_setup_wizard(
    state: AppState,
    *,
    on_finish: Callable[[], None],
) -> None:
    """Build and open the first-launch setup wizard dialog.

    Must be called inside a NiceGUI page context (``@ui.page``).
    """
    # Lazy imports — only needed when the wizard actually runs
    from models import (
        POPULAR_MODELS,
        DEFAULT_MODEL,
        validate_openrouter_key,
        validate_anthropic_key,
        validate_google_key,
        validate_xai_key,
        refresh_cloud_models,
        list_cloud_models,
        get_provider_emoji,
        list_cloud_vision_models,
        list_all_models,
        is_model_local,
        pull_model,
        set_model,
        list_local_models,
        _ollama_reachable,
    )
    from vision import DEFAULT_VISION_MODEL, POPULAR_VISION_MODELS
    from api_keys import set_key
    from agent import clear_agent_cache
    from ui.helpers import mark_setup_complete
    from providers.selection import add_quick_choice_for_model

    def _open_first_run_migration_wizard() -> None:
        with ui.dialog().props("maximized") as migration_dlg:
            with ui.card().classes("w-full h-full no-shadow").style(
                "max-width: 64rem; margin: 0 auto;"
            ):
                with ui.row().classes("w-full items-center justify-between px-4 pt-3 pb-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("move_up", size="sm")
                        ui.label("Import from Hermes/OpenClaw").classes("text-h5")
                    ui.button(icon="close", on_click=migration_dlg.close).props("flat round size=sm")
                ui.separator()
                with ui.scroll_area().classes("w-full").style("height: calc(100vh - 76px);"):
                    with ui.column().classes("w-full px-6 py-4"):
                        __import__(
                            "ui.migration_wizard",
                            fromlist=["build_migration_wizard_tab"],
                        ).build_migration_wizard_tab()
        migration_dlg.open()

    setup_dlg = ui.dialog().props(
        "persistent maximized transition-show=fade transition-hide=fade"
    )

    with setup_dlg:
        with ui.card().classes("w-full max-w-4xl mx-auto q-pa-lg"):
            # ── Header ───────────────────────────────────────────────
            ui.html(
                '<div style="text-align:center;">'
                '<h1 style="color: gold; margin-bottom: 0;">𓁟 Welcome to Thoth</h1>'
                '</div>',
                sanitize=False,
            )
            ui.label(
                "Let's get your default mode, providers, and Quick Choices ready."
            ).classes("text-center text-grey-6")

            ui.separator()

            ui.label("Import from an existing setup").classes("text-h6")
            ui.label(
                "Migration runs before provider setup so imported defaults and provider settings can shape the recommendations."
            ).classes("text-grey-6 text-sm")
            with ui.row().classes("w-full gap-2 q-my-sm"):
                ui.button(
                    "Open Migration Wizard",
                    icon="move_up",
                    on_click=_open_first_run_migration_wizard,
                ).props("outline color=primary")
                ui.button("Skip migration", icon="skip_next").props("flat color=grey")

            ui.separator()

            # ── Setup Path Toggle ────────────────────────────────────
            ui.label("Choose an AI account or local model").classes("text-h6")
            ui.label(
                "Choose Local if you have a GPU and want full privacy. "
                "Choose Provider if you prefer using OpenAI, Claude, Gemini, xAI, or OpenRouter API keys."
            ).classes("text-grey-6 text-sm")

            setup_path: dict[str, str | None] = {"mode": None}

            with ui.row().classes("w-full gap-4 q-my-sm"):
                def _pick_local():
                    setup_path["mode"] = "local"
                    _local_section.visible = True
                    _cloud_section.visible = False
                    _update_finish()

                def _pick_cloud():
                    setup_path["mode"] = "cloud"
                    _local_section.visible = False
                    _cloud_section.visible = True
                    _update_finish()

                ui.button("🖥️ Local (Ollama)", on_click=_pick_local).props(
                    "color=primary outline"
                ).classes("flex-grow")
                ui.button("Providers (API key)", on_click=_pick_cloud).props(
                    "color=cyan outline"
                ).classes("flex-grow")

            _cloud_section = ui.column().classes("w-full")
            _cloud_section.visible = False
            _local_section = ui.column().classes("w-full")
            _local_section.visible = False

            # ── Provider Setup Path ──────────────────────────────────
            cloud_done: dict[str, bool] = {"value": False}
            with _cloud_section:
                ui.label(
                    "Enter at least one provider API key. OpenAI gives direct access to GPT models. "
                    "Anthropic gives direct access to Claude. Google AI gives direct access to Gemini. "
                    "xAI gives direct access to Grok. "
                    "OpenRouter gives access to 100+ models from all providers."
                ).classes("text-grey-6 text-sm")

                setup_openai_key = ui.input(
                    "OpenAI API Key (optional)",
                    password=True, password_toggle_button=True,
                ).classes("w-full")
                setup_anth_key = ui.input(
                    "Anthropic API Key (optional)",
                    password=True, password_toggle_button=True,
                ).classes("w-full")
                setup_goog_key = ui.input(
                    "Google AI API Key (optional)",
                    password=True, password_toggle_button=True,
                ).classes("w-full")
                setup_xai_key = ui.input(
                    "xAI API Key (optional)",
                    password=True, password_toggle_button=True,
                ).classes("w-full")
                setup_or_key = ui.input(
                    "OpenRouter API Key (optional)",
                    password=True, password_toggle_button=True,
                ).classes("w-full")

                cloud_status = ui.label("").classes("text-sm")
                cloud_status.visible = False
                cloud_model_select = ui.select(
                    label="Default provider model",
                    options=[],
                ).classes("w-full").props("use-input input-debounce=300")
                cloud_model_select.visible = False

                cloud_vision_select = ui.select(
                    label="Vision model (optional — for camera/screenshot analysis)",
                    options=[],
                ).classes("w-full").props("use-input input-debounce=300")
                cloud_vision_select.visible = False

                async def _validate_cloud_keys():
                    oai_val = setup_openai_key.value.strip()
                    anth_val = setup_anth_key.value.strip()
                    goog_val = setup_goog_key.value.strip()
                    xai_val = setup_xai_key.value.strip()
                    or_val = setup_or_key.value.strip()
                    if not oai_val and not anth_val and not goog_val and not xai_val and not or_val:
                        ui.notify("Enter at least one API key", type="warning")
                        return
                    cloud_status.text = "⏳ Validating key(s)…"
                    cloud_status.visible = True
                    if or_val:
                        or_valid = await run.io_bound(validate_openrouter_key, or_val)
                        if not or_valid:
                            cloud_status.text = "❌ Invalid OpenRouter API key."
                            cloud_done["value"] = False
                            _update_finish()
                            return
                        set_key("OPENROUTER_API_KEY", or_val)
                    if anth_val:
                        anth_valid = await run.io_bound(validate_anthropic_key, anth_val)
                        if not anth_valid:
                            cloud_status.text = "❌ Invalid Anthropic API key."
                            cloud_done["value"] = False
                            _update_finish()
                            return
                        set_key("ANTHROPIC_API_KEY", anth_val)
                    if goog_val:
                        goog_valid = await run.io_bound(validate_google_key, goog_val)
                        if not goog_valid:
                            cloud_status.text = "❌ Invalid Google AI API key."
                            cloud_done["value"] = False
                            _update_finish()
                            return
                        set_key("GOOGLE_API_KEY", goog_val)
                    if xai_val:
                        xai_valid = await run.io_bound(validate_xai_key, xai_val)
                        if not xai_valid:
                            cloud_status.text = "❌ Invalid xAI API key."
                            cloud_done["value"] = False
                            _update_finish()
                            return
                        set_key("XAI_API_KEY", xai_val)
                    if oai_val:
                        set_key("OPENAI_API_KEY", oai_val)
                    cloud_status.text = "⏳ Fetching available models…"
                    count = await run.io_bound(refresh_cloud_models)
                    if count == 0:
                        cloud_status.text = "❌ No models found. Check your API key(s)."
                        cloud_done["value"] = False
                        _update_finish()
                        return
                    models = list_cloud_models()
                    opts = {m: f"{get_provider_emoji(m)} {m}" for m in models}
                    cloud_model_select.options = opts
                    cloud_model_select.visible = True
                    first = "gpt-5" if "gpt-5" in models else models[0]
                    cloud_model_select.set_value(first)
                    vision_models = list_cloud_vision_models()
                    if vision_models:
                        v_opts = {m: f"{get_provider_emoji(m)} {m}" for m in vision_models}
                        cloud_vision_select.options = v_opts
                        v_first = "gpt-5" if "gpt-5" in vision_models else vision_models[0]
                        cloud_vision_select.set_value(v_first)
                        cloud_vision_select.visible = True
                    cloud_status.text = f"✅ Found {count} models"
                    add_quick_choice_for_model(first, source="setup_default")
                    cloud_done["value"] = True
                    _update_finish()

                ui.button("Validate & Fetch Models", icon="key", on_click=_validate_cloud_keys).props(
                    "color=cyan"
                )

                def _on_cloud_model_change(e):
                    if e.value:
                        cloud_done["value"] = True
                        _update_finish()

                cloud_model_select.on_value_change(_on_cloud_model_change)

            # ── Brain Model (Local path) ─────────────────────────────
            with _local_section:
                def _wiz_probe():
                    up = _ollama_reachable()
                    local = list_local_models() if up else []
                    return up, local

                _wiz_ollama_up, local_now = await run.io_bound(_wiz_probe)

                if sys.platform == "win32":
                    _wiz_install = (
                        "1. Download Ollama from ollama.com/download\n"
                        "2. Run the installer — Ollama starts automatically\n"
                        "3. Come back here and click 🖥️ Local again"
                    )
                elif sys.platform == "darwin":
                    _wiz_install = (
                        "1. Download from ollama.com/download (or: brew install ollama)\n"
                        "2. Run: ollama serve\n"
                        "3. Come back here and click 🖥️ Local again"
                    )
                else:
                    _wiz_install = (
                        "1. Install: curl -fsSL https://ollama.com/install.sh | sh\n"
                        "2. Run: ollama serve\n"
                        "3. Come back here and click 🖥️ Local again"
                    )

                with ui.card().classes("w-full q-pa-md bg-amber-1") as wiz_ollama_notice:
                    ui.label("⚠️ Ollama is not running").classes(
                        "text-weight-bold text-body1 text-brown-9"
                    )
                    ui.label(
                        "Local models need Ollama installed and running on your machine. "
                        "Install it, then come back to this wizard."
                    ).classes("text-grey-8 text-sm q-mb-xs")
                    ui.label(_wiz_install).classes("text-grey-8 text-xs").style(
                        "white-space: pre-line"
                    )
                    ui.link(
                        "Download Ollama →",
                        "https://ollama.com/download",
                        new_tab=True,
                    ).classes("text-sm text-weight-bold")
                wiz_ollama_notice.visible = not _wiz_ollama_up

                ui.label("🧠 Brain Model").classes("text-h6")
                ui.label(
                    "The main reasoning model that powers conversations and tool use. "
                    "14B+ recommended for best accuracy."
                ).classes("text-grey-6 text-sm")

                local_now = local_now or []
                setup_all_models = list_all_models()
                brain_default = state.current_model
                if brain_default not in setup_all_models:
                    brain_default = DEFAULT_MODEL

                setup_brain_opts = {
                    m: f"{'✅' if m in local_now else '⬇️'}  {m}"
                    for m in setup_all_models
                }
                setup_brain_select = ui.select(
                    label="Brain model",
                    options=setup_brain_opts,
                    value=brain_default,
                ).classes("w-full").props("use-input input-debounce=300")

                brain_status = ui.label("").classes("text-sm")
                brain_status.visible = False
                brain_done: dict[str, bool] = {"value": brain_default in local_now}

                setup_brain_dl = ui.button(f"⬇️ Download {brain_default}").props(
                    "color=primary"
                )
                setup_brain_dl.visible = brain_default not in local_now
                if brain_default in local_now:
                    brain_status.text = f"✅ {brain_default} is ready"
                    brain_status.visible = True

                async def _setup_dl_brain():
                    sel = setup_brain_select.value
                    if is_model_local(sel):
                        brain_status.text = f"✅ {sel} is already downloaded"
                        brain_status.visible = True
                        brain_done["value"] = True
                        setup_brain_dl.visible = False
                        _update_finish()
                        return
                    if not _ollama_reachable():
                        brain_status.text = (
                            "❌ Ollama is not running. Install and start Ollama first."
                        )
                        brain_status.visible = True
                        return
                    setup_brain_dl.disable()
                    brain_status.text = f"⏳ Downloading {sel}… this may take a few minutes"
                    brain_status.visible = True
                    n = ui.notification(
                        f"Downloading {sel}…",
                        type="ongoing",
                        spinner=True,
                        timeout=None,
                    )
                    await run.io_bound(lambda: list(pull_model(sel)))
                    n.dismiss()
                    brain_status.text = f"✅ {sel} downloaded successfully!"
                    setup_brain_dl.visible = False
                    setup_brain_dl.enable()
                    brain_done["value"] = True
                    set_model(sel)
                    state.current_model = sel
                    clear_agent_cache()
                    _update_finish()

                setup_brain_dl.on_click(_setup_dl_brain)

                def _on_setup_brain_change(e):
                    sel = e.value
                    setup_brain_dl.text = f"⬇️ Download {sel}"
                    already = is_model_local(sel)
                    setup_brain_dl.visible = not already
                    brain_done["value"] = already
                    if already:
                        brain_status.text = f"✅ {sel} is ready"
                        brain_status.visible = True
                        set_model(sel)
                        state.current_model = sel
                        clear_agent_cache()
                    else:
                        brain_status.visible = False
                    _update_finish()

                setup_brain_select.on_value_change(_on_setup_brain_change)

                ui.separator()

                # ── Vision Model ─────────────────────────────────────
                ui.label("👁️ Vision Model").classes("text-h6")
                ui.label(
                    "Used for camera and screen capture analysis. "
                    "Optional — you can skip this and download it later."
                ).classes("text-grey-6 text-sm")

                vsvc = state.vision_service
                setup_vision_opts = {
                    m: f"{'✅' if m in local_now else '⬇️'}  {m}"
                    for m in sorted(
                        set(
                            POPULAR_VISION_MODELS
                            + (
                                [vsvc.model]
                                if vsvc.model not in POPULAR_VISION_MODELS
                                else []
                            )
                        )
                    )
                }
                setup_vision_select = ui.select(
                    label="Vision model",
                    options=setup_vision_opts,
                    value=vsvc.model,
                ).classes("w-full").props("use-input input-debounce=300")

                vision_status = ui.label("").classes("text-sm")
                vision_status.visible = False

                setup_vision_dl = ui.button(f"⬇️ Download {vsvc.model}").props(
                    "color=primary outline"
                )
                setup_vision_dl.visible = vsvc.model not in local_now
                if vsvc.model in local_now:
                    vision_status.text = f"✅ {vsvc.model} is ready"
                    vision_status.visible = True

                async def _setup_dl_vision():
                    sel = setup_vision_select.value
                    if is_model_local(sel):
                        vision_status.text = f"✅ {sel} is already downloaded"
                        vision_status.visible = True
                        setup_vision_dl.visible = False
                        return
                    if not _ollama_reachable():
                        vision_status.text = (
                            "❌ Ollama is not running. Install and start Ollama first."
                        )
                        vision_status.visible = True
                        return
                    setup_vision_dl.disable()
                    vision_status.text = f"⏳ Downloading {sel}… this may take a few minutes"
                    vision_status.visible = True
                    n = ui.notification(
                        f"Downloading {sel}…",
                        type="ongoing",
                        spinner=True,
                        timeout=None,
                    )
                    await run.io_bound(lambda: list(pull_model(sel)))
                    n.dismiss()
                    vision_status.text = f"✅ {sel} downloaded successfully!"
                    setup_vision_dl.visible = False
                    setup_vision_dl.enable()
                    vsvc.model = sel

                setup_vision_dl.on_click(_setup_dl_vision)

                def _on_setup_vision_change(e):
                    sel = e.value
                    setup_vision_dl.text = f"⬇️ Download {sel}"
                    already = is_model_local(sel)
                    setup_vision_dl.visible = not already
                    if already:
                        vision_status.text = f"✅ {sel} is ready"
                        vision_status.visible = True
                        vsvc.model = sel
                    else:
                        vision_status.visible = False

                setup_vision_select.on_value_change(_on_setup_vision_change)

            ui.separator()

            # ── Recommended Setup ────────────────────────────────────
            ui.label("📋 Recommended Setup").classes("text-h6")
            ui.label(
                "After completing this wizard, head to Settings to get the most out of Thoth:"
            ).classes("text-grey-6 text-sm")

            tips = [
                (
                    "🧠",
                    "Memory & Knowledge Graph",
                    "I build a personal knowledge graph from our conversations automatically — "
                    "people, places, preferences, and their connections. "
                    "You can also tell me things explicitly — 'Remember that my standup is at 9 AM.'",
                ),
                (
                    "🎤",
                    "Voice & Text-to-Speech",
                    "Toggle the mic to talk to me — I'll transcribe with Whisper and respond conversationally. "
                    "Enable TTS in Settings → Voice for spoken replies with 10 neural voices via Kokoro.",
                ),
                (
                    "🧩",
                    "Skills",
                    "9 bundled instruction packs — Deep Research, Daily Briefing, Humanizer, and more. "
                    "Enable them in Settings → Skills to shape how I think and respond.",
                ),
                (
                    "⚡",
                    "Tasks & Scheduling",
                    "Create scheduled automations — daily briefings, email digests, research summaries. "
                    "7 trigger types including cron expressions. Open the Tasks tab or just ask me.",
                ),
                (
                    "🌐",
                    "Browser Automation",
                    "I can browse the web in a visible Chromium window — navigate, click, fill forms, "
                    "and extract data. Logins persist across sessions.",
                ),
                (
                    "📧",
                    "Gmail & Calendar",
                    "Settings → Tools → Gmail to connect your Google account. "
                    "Once connected, I can read, draft and send emails, and manage calendar events.",
                ),
                (
                    "📄",
                    "Documents",
                    "Settings → Documents to upload PDFs and text files as a persistent knowledge base. "
                    "You can also attach files directly in chat with the 📎 button or drag-and-drop.",
                ),
                (
                    "📋",
                    "Habit & Health Tracker",
                    "Log medications, symptoms, exercise, or any recurring activity through conversation. "
                    "Streak analysis, trend charts, and CSV export — all stored locally.",
                ),
                (
                    "🖥️",
                    "Shell Access",
                    "I can run shell commands on your machine — install packages, manage git repos, "
                    "run scripts. Dangerous commands require your approval first.",
                ),
                (
                    "📁",
                    "Workspace Folder",
                    "File operations are sandboxed to ~/Documents/Thoth (auto-created). "
                    "I can read, write, and organize files there — including PDF, CSV, and Excel.",
                ),
                (
                    "📡",
                    "Channels",
                    "Settings → Channels to connect Telegram or Email so I can respond even when the app is closed.",
                ),
            ]
            # If provider path was selected, add a tips entry for Quick Choices
            _is_cloud = setup_path["mode"] == "cloud"
            if _is_cloud:
                tips.insert(
                    1,
                    (
                        "📌",
                        "Quick Choices",
                        "Head to Settings → Models to pin exact models. "
                        "Quick Choices appear in chat, workflow, channel, and Designer pickers.",
                    ),
                )
            for icon, title, desc in tips:
                with ui.row().classes("items-start gap-2 q-py-xs"):
                    ui.label(icon).classes("text-lg")
                    with ui.column().classes("gap-0"):
                        ui.label(title).classes("font-bold text-sm")
                        ui.label(desc).classes("text-grey-6 text-xs")

            ui.separator()

            # ── Finish ───────────────────────────────────────────────
            finish_btn = ui.button("Get Started →").props(
                "color=primary size=lg"
            ).classes("w-full")

            def _update_finish():
                if setup_path["mode"] is None:
                    finish_btn.set_enabled(False)
                elif setup_path["mode"] == "cloud":
                    finish_btn.set_enabled(cloud_done["value"])
                else:
                    finish_btn.set_enabled(brain_done["value"])

            _update_finish()

            async def _finish_setup():
                if setup_path["mode"] == "cloud":
                    sel = cloud_model_select.value
                    if sel:
                        set_model(sel)
                        state.current_model = sel
                        add_quick_choice_for_model(sel, source="setup_default")
                        clear_agent_cache()
                    vsel = cloud_vision_select.value
                    if vsel:
                        state.vision_service.model = vsel
                mark_setup_complete()
                setup_dlg.close()
                await on_finish()

            finish_btn.on_click(_finish_setup)

    setup_dlg.open()
