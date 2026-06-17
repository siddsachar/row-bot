"""Row-Bot UI - sidebar (left drawer) with thread list.

Builds the sidebar drawer, home/new buttons, thread listing, and
settings/help buttons.  All navigation is handled via callbacks so
the module stays decoupled from the main page layout.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from row_bot.brand import APP_BRAND_ACCENT, APP_DISPLAY_NAME
from nicegui import run, ui
from row_bot.ui.state import AppState, P, _active_generations
from row_bot.ui.constants import SIDEBAR_MAX_THREADS
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# Module-level so filter choice survives rebuild_main() re-renders.
_SIDEBAR_FILTER: str = "all"  # one of: "all", "chat", "designer", "code", "workflow"
_MODAL_FILTER: str = "all"
_SIDEBAR_DEV_EXPANDED: set[str] = set()
_SIDEBAR_DEV_EXPANDED_LOADED: bool = False
_SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE: bool = False
_SIDEBAR_DEV_DEFAULT_APPLIED: bool = False
THREAD_FILTER_DESCRIPTORS: tuple[dict[str, str], ...] = (
    {"key": "all", "label": "All", "icon": "forum"},
    {"key": "chat", "label": "Chats", "icon": "chat_bubble_outline"},
    {"key": "designer", "label": "Designs", "icon": "brush"},
    {"key": "code", "label": "Code", "icon": "code"},
    {"key": "workflow", "label": "Workflows", "icon": "task_alt"},
)
_SIDEBAR_AVATAR_CSS = """
.sb-avatar { position: relative; }
.sb-idle { color: #64748b; }
.sb-streaming { color: #2563eb; }
.sb-error { color: #dc2626; }
.sb-voice { color: #0f766e; }
.sb-task { color: #7c3aed; }
.sb-approval { color: #d97706; }
.sb-done { color: #16a34a; }
.sb-tts { color: #0891b2; }
.sb-state-label { font-size: 11px; }
.sb-ring-spin { animation: sb-ring-spin 1.1s linear infinite; }
@keyframes sb-ring-spin { to { transform: rotate(360deg); } }
"""


def _render_filter_button(
    *,
    key: str,
    label: str,
    icon: str,
    count: int,
    active: bool,
    on_click: Callable[[], None],
):
    """Render a compact sidebar/modal thread filter control."""

    aria = f"{label} conversations, {count}"
    btn = ui.button(
        str(count) if count else "",
        icon=icon,
        on_click=on_click,
    ).props(
        f'dense no-caps no-wrap size=sm rounded aria-label="{aria}" '
        + ("color=primary" if active else "flat color=grey-5")
    ).classes("row-bot-thread-filter-icon").style(
        "width: 46px; min-width: 0; max-width: 46px; height: 30px; "
        "min-height: 30px; padding: 2px 3px; font-size: 0.68rem; "
        "line-height: 1; white-space: nowrap; flex: 0 0 46px;"
    ).tooltip(f"{label}: {count}")
    if active:
        btn.classes("row-bot-pill-active")
    return btn


def _render_modal_filter_button(
    *,
    key: str,
    label: str,
    icon: str,
    count: int,
    active: bool,
    on_click: Callable[[], None],
):
    """Render a wider filter control for the All Conversations dialog."""

    aria = f"{label} conversations, {count}"
    btn = ui.button(
        f"{label} {count}" if count else label,
        icon=icon,
        on_click=on_click,
    ).props(
        f'dense no-caps no-wrap size=sm rounded aria-label="{aria}" '
        + ("color=primary" if active else "flat color=grey-5")
    ).classes("row-bot-thread-filter-modal").style(
        "min-height: 30px; padding: 2px 8px; font-size: 0.72rem; "
        "line-height: 1; white-space: nowrap; flex: 0 0 auto;"
    ).tooltip(f"{label}: {count}")
    if active:
        btn.classes("row-bot-pill-active")
    return btn


def _sidebar_state_path():
    return get_row_bot_data_dir() / "sidebar_state.json"


def _ensure_sidebar_dev_state_loaded() -> None:
    global _SIDEBAR_DEV_EXPANDED
    global _SIDEBAR_DEV_EXPANDED_LOADED
    global _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE
    if _SIDEBAR_DEV_EXPANDED_LOADED:
        return
    path = _sidebar_state_path()
    if not path.exists():
        _SIDEBAR_DEV_EXPANDED = set()
        _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE = False
        _SIDEBAR_DEV_EXPANDED_LOADED = True
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        has_dev_state = isinstance(data, dict) and "developer_workspace_expanded" in data
        expanded = data.get("developer_workspace_expanded", []) if has_dev_state else []
        _SIDEBAR_DEV_EXPANDED = {str(item) for item in expanded if str(item or "").strip()}
        _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE = has_dev_state
    except Exception:
        logger.debug("Failed to load sidebar state", exc_info=True)
        _SIDEBAR_DEV_EXPANDED = set()
        _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE = False
    _SIDEBAR_DEV_EXPANDED_LOADED = True


def _persist_sidebar_dev_expanded() -> None:
    global _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE
    try:
        path = _sidebar_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload.update(existing)
            except Exception:
                logger.debug("Failed to merge existing sidebar state", exc_info=True)
        payload["developer_workspace_expanded"] = sorted(_SIDEBAR_DEV_EXPANDED)
        payload.pop("agent_parent_expanded", None)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE = True
    except Exception:
        logger.debug("Failed to persist sidebar state", exc_info=True)


def build_sidebar(
    state: AppState,
    p: P,
    *,
    rebuild_main: Callable[[], None],
    open_settings: Callable[..., None],
    load_thread_messages: Callable[[str], list[dict]],
) -> Callable[[], None]:
    """Build the left drawer and return ``rebuild_thread_list`` so the
    caller can invoke it when needed.

    Parameters
    ----------
    rebuild_main:
        Called to refresh the main content area after a navigation event.
    open_settings:
        Called when the user clicks the Settings button.
    load_thread_messages:
        ``load_thread_messages(thread_id) -> list[dict]`` used to hydrate
        a thread when the user clicks it.
    """
    from row_bot.threads import _list_threads, _save_thread_meta, _delete_thread, _get_thread_project_id, _get_thread_approval_mode
    from row_bot.tasks import get_running_tasks, stop_task
    from row_bot.models import is_cloud_model, get_current_model
    from row_bot.memory_extraction import set_active_thread
    from row_bot.agent import clear_summary_cache
    from row_bot.ui.thread_actions import show_rename_thread_dialog

    # Keep a reference the caller can use
    _rebuild_thread_list_ref: list[Callable[[], None]] = [lambda: None]

    with ui.left_drawer(value=True, fixed=True).style(
        "width: var(--row-bot-left-drawer-width, 280px);"
    ).classes("row-bot-panel-card"):
        # Logo - always app branding, independent of identity settings
        ui.html(
            f'<div style="display:flex; align-items:center; gap:8px; margin:0 0 8px 0;">'
            f'<img src="/static/row_bot_glyph_256.png" alt="" '
            f'style="width:72px; height:auto; display:block; flex:0 0 auto;">'
            f'<div style="display:flex; flex-direction:column; gap:3px; min-width:0;">'
            f'<span style="font-size:1.15rem; font-weight:600; color:{APP_BRAND_ACCENT};'
            f' letter-spacing:0.5px; line-height:1.05;">{APP_DISPLAY_NAME}</span>'
            f'<span style="font-size:11px; color:#9ca3af; line-height:1.2;">'
            f'Personal AI Sovereignty</span></div></div>',
            sanitize=False,
        )
        ui.separator()

        # Home + New buttons
        with ui.row().classes("w-full gap-2"):
            def _go_home():
                from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                stop_voice_for_thread_change(state, p, reason="home")
                prev = state.thread_id
                prev_gen = _active_generations.get(prev) if prev else None
                if prev_gen and prev_gen.status == "streaming":
                    prev_gen.detached = True
                    if prev_gen.tts_active:
                        state.tts_service.stop()
                        prev_gen.tts_active = False
                state.active_designer_project = None
                state.active_developer_workspace_id = None
                state.thread_id = None
                state.thread_name = None
                from row_bot.approval_policy import DEFAULT_APPROVAL_MODE
                state.thread_approval_mode = DEFAULT_APPROVAL_MODE
                state.messages = []
                p.pending_files.clear()
                set_active_thread(None, previous_id=prev)
                rebuild_main(reason="home")
                _rebuild_thread_list_ref[0]()

            _home_btn = ui.button("🏠 Home", on_click=_go_home).classes("flex-grow").props("flat")

            async def _new_thread():
                from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                tid = uuid.uuid4().hex[:12]
                name = f"💻 Thread {datetime.now().strftime('%b %d, %H:%M')}"
                await run.io_bound(
                    lambda: _save_thread_meta(
                        tid,
                        name,
                        seed_default_skills=True,
                    )
                )
                stop_voice_for_thread_change(state, p, reason="new_thread")
                prev = state.thread_id
                prev_gen = _active_generations.get(prev) if prev else None
                if prev_gen and prev_gen.status == "streaming":
                    prev_gen.detached = True
                    if prev_gen.tts_active:
                        state.tts_service.stop()
                        prev_gen.tts_active = False
                state.active_designer_project = None
                state.active_developer_workspace_id = None
                state.thread_id = tid
                state.thread_name = name
                state.messages = []
                state.thread_model_override = ""
                from row_bot.approval_policy import DEFAULT_APPROVAL_MODE
                state.thread_approval_mode = DEFAULT_APPROVAL_MODE
                p.pending_files.clear()
                set_active_thread(tid, previous_id=prev)
                rebuild_main(immediate=True, reason="new_thread")
                _rebuild_thread_list_ref[0]()

            ui.button("＋ New", on_click=_new_thread).classes("flex-grow").props("color=primary")

        with ui.column().classes("w-full gap-1 q-mt-sm row-bot-inner-panel"):
            ui.label("Conversations").classes("text-subtitle2")
            # Filter pill row - rebuilt by _rebuild_thread_list so counts stay current
            p.thread_filter_container = ui.row().classes(
                "w-full gap-1 items-center no-wrap q-mb-xs"
            ).style(
                "display: flex; flex-wrap: nowrap; overflow: hidden; "
                "column-gap: 4px; row-gap: 0;"
            )
            p.thread_container = ui.column().classes("w-full gap-0")

        # Agent Profile library
        with ui.column().classes("w-full gap-1 q-mt-sm row-bot-inner-panel"):
            from row_bot.ui.profile_library import build_profile_library

            build_profile_library(
                state,
                p,
                rebuild_main=rebuild_main,
                rebuild_thread_list=lambda: _rebuild_thread_list_ref[0](),
            )

        # Spacer pushes bottom section down
        ui.space()

        # ── Buddy companion ─────────────────────────────────────────
        from row_bot.ui.buddy import build_sidebar_buddy
        build_sidebar_buddy(state, p, open_settings=open_settings)

        # Help & Settings buttons
        with ui.row().classes("w-full justify-center items-center gap-1"):
            def _show_help():
                from row_bot.ui.onboarding_center import show_setup_center

                show_setup_center(
                    open_settings=open_settings,
                    rebuild_main=rebuild_main,
                    state=state,
                )

            ui.button("👋", on_click=_show_help).props("flat dense").style("font-size: 1.1rem;")
            ui.button(icon="settings", on_click=lambda: open_settings()).props(
                "flat dense round size=sm"
            ).classes("text-grey-5").style("font-size: 1.25rem;")
        from row_bot.version import __version__ as _v
        ui.label(f"v{_v}").classes("text-xs text-grey-7 w-full text-center").style(
            "margin-top: -4px; letter-spacing: 0.3px; opacity: 0.5;"
        )

    # ── Thread list builder ──────────────────────────────────────────

    def _rebuild_thread_list() -> None:
        if p.thread_container is None:
            return
        p.thread_container.clear()
        if p.thread_filter_container is not None:
            p.thread_filter_container.clear()
        threads = _list_threads(include_details=True)
        running_tids = get_running_tasks()

        # Classify every thread once so pills + list share the same data.
        from row_bot.threads import get_workflow_thread_ids
        workflow_tids = get_workflow_thread_ids()
        try:
            from row_bot.agent_runs import list_agent_runs

            _agent_run_rows = list_agent_runs(limit=500)
        except Exception:
            _agent_run_rows = []
        child_thread_ids = {
            str(agent_run.get("thread_id") or "")
            for agent_run in _agent_run_rows
            if str(agent_run.get("thread_id") or "")
            and str(agent_run.get("thread_id") or "") != str(agent_run.get("parent_thread_id") or "")
        }
        def _cat_of(pid: str, tid: str, thread_type: str = "", dev_ws: str = "") -> str:
            if thread_type == "agent_child" or tid in child_thread_ids:
                return "agents"
            if pid:
                return "designer"
            if thread_type == "code" or dev_ws:
                return "code"
            if tid in workflow_tids:
                return "workflow"
            return "chat"

        classified: list[tuple] = []
        counts = {"all": 0, "chat": 0, "designer": 0, "code": 0, "workflow": 0, "agents": 0}
        for row in threads:
            tid = row[0]
            _pid = row[5] if len(row) > 5 else ""
            _thread_type = row[6] if len(row) > 6 else ""
            _dev_ws = row[7] if len(row) > 7 else ""
            cat = _cat_of(_pid, tid, _thread_type, _dev_ws)
            counts[cat] += 1
            if cat != "agents":
                counts["all"] += 1
            classified.append((row, cat))

        # ── Filter pill row ─────────────────────────────────────────
        global _SIDEBAR_FILTER
        valid_filter_keys = {descriptor["key"] for descriptor in THREAD_FILTER_DESCRIPTORS}
        if _SIDEBAR_FILTER not in valid_filter_keys:
            _SIDEBAR_FILTER = "all"
        if p.thread_filter_container is not None and counts["all"] > 0:
            with p.thread_filter_container:
                for descriptor in THREAD_FILTER_DESCRIPTORS:
                    key = descriptor["key"]
                    label = descriptor["label"]
                    n = counts[key]
                    # Hide empty buckets other than "All".
                    if key != "all" and n == 0:
                        continue
                    is_on = _SIDEBAR_FILTER == key

                    def _set_filter(k=key):
                        global _SIDEBAR_FILTER
                        _SIDEBAR_FILTER = k
                        _rebuild_thread_list()

                    _render_filter_button(
                        key=key,
                        label=label,
                        icon=descriptor["icon"],
                        count=n,
                        active=is_on,
                        on_click=_set_filter,
                    )

        # Apply filter
        if _SIDEBAR_FILTER == "all":
            classified = [c for c in classified if c[1] != "agents"]
        else:
            classified = [c for c in classified if c[1] == _SIDEBAR_FILTER]

        def _workspace_display(dev_ws: str) -> tuple[str, str]:
            try:
                from row_bot.developer.storage import get_workspace

                workspace = get_workspace(dev_ws)
                if workspace is not None:
                    return workspace.name or "Developer workspace", workspace.path or ""
            except Exception:
                logger.debug("Failed to load Developer workspace %s for sidebar", dev_ws, exc_info=True)
            return "Missing workspace", dev_ws

        def _sidebar_display_items(rows: list[tuple], filter_key: str) -> list[tuple[str, object, str, bool]]:
            global _SIDEBAR_DEV_DEFAULT_APPLIED
            _ensure_sidebar_dev_state_loaded()
            if filter_key not in {"all", "code"}:
                return [("thread", row, cat, False) for row, cat in rows]
            groups: dict[str, dict] = {}
            top_workspace_id = ""
            for row, cat in rows:
                dev_ws = row[7] if len(row) > 7 else ""
                if cat != "code" or not dev_ws:
                    continue
                if not top_workspace_id:
                    top_workspace_id = dev_ws
                group = groups.get(dev_ws)
                if group is None:
                    workspace_name, workspace_path = _workspace_display(dev_ws)
                    group = {
                        "workspace_id": dev_ws,
                        "name": workspace_name,
                        "path": workspace_path,
                        "updated": row[3] if len(row) > 3 else "",
                        "rows": [],
                    }
                    groups[dev_ws] = group
                group["rows"].append((row, cat))

            if (
                top_workspace_id
                and not _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE
                and not _SIDEBAR_DEV_DEFAULT_APPLIED
            ):
                _SIDEBAR_DEV_EXPANDED.add(top_workspace_id)
                _SIDEBAR_DEV_DEFAULT_APPLIED = True
                _persist_sidebar_dev_expanded()

            items: list[tuple[str, object, str, bool]] = []
            seen_groups: set[str] = set()
            for row, cat in rows:
                dev_ws = row[7] if len(row) > 7 else ""
                if cat == "code" and dev_ws:
                    if dev_ws in seen_groups:
                        continue
                    seen_groups.add(dev_ws)
                    group = groups[dev_ws]
                    items.append(("developer_group", group, cat, False))
                    if dev_ws in _SIDEBAR_DEV_EXPANDED:
                        for child_row, child_cat in group["rows"]:
                            items.append(("thread", child_row, child_cat, True))
                    continue
                items.append(("thread", row, cat, False))
            return items

        def _fmt_ts(iso_str: str) -> str:
            try:
                dt = datetime.fromisoformat(iso_str)
                try:
                    return dt.strftime("%b %d, %#I:%M %p")
                except ValueError:
                    return dt.strftime("%b %d, %-I:%M %p")
            except Exception:
                return iso_str[:16] if iso_str else ""

        with p.thread_container:
            if not classified:
                ui.label("No conversations yet." if _SIDEBAR_FILTER == "all"
                         else "Nothing in this filter.").classes(
                    "text-grey-6 text-sm q-px-sm"
                )
                return

            display_items = _sidebar_display_items(classified, _SIDEBAR_FILTER)
            visible = display_items[:SIDEBAR_MAX_THREADS]
            for item_kind, payload, _cat, is_developer_child in visible:
                if item_kind == "developer_group":
                    group = payload
                    workspace_id = str(group.get("workspace_id", ""))
                    group_rows = list(group.get("rows", []))
                    is_expanded = workspace_id in _SIDEBAR_DEV_EXPANDED
                    is_active_workspace = (
                        state.active_developer_workspace_id == workspace_id
                        or any(row[0] == state.thread_id for row, _child_cat in group_rows)
                    )

                    def _toggle_workspace(wsid=workspace_id):
                        if wsid in _SIDEBAR_DEV_EXPANDED:
                            _SIDEBAR_DEV_EXPANDED.discard(wsid)
                        else:
                            _SIDEBAR_DEV_EXPANDED.add(wsid)
                        _persist_sidebar_dev_expanded()
                        _rebuild_thread_list()

                    caption_bits = [
                        f"{len(group_rows)} thread{'s' if len(group_rows) != 1 else ''}",
                    ]
                    latest = str(group.get("updated") or "")
                    if latest:
                        caption_bits.append(_fmt_ts(latest))
                    with ui.item(on_click=_toggle_workspace).classes("w-full rounded").props("clickable").style(
                        "min-height: 36px; padding: 4px 8px;"
                    ):
                        with ui.item_section().props("avatar").style("min-width: 28px;"):
                            ui.icon("folder_open" if is_expanded else "folder", size="xs").classes(
                                "text-primary" if is_active_workspace else "text-grey-6"
                            )
                        with ui.item_section():
                            ui.item_label(str(group.get("name") or "Developer workspace")).classes("ellipsis").style(
                                "font-size: 0.85rem;" + ("font-weight: 600;" if is_active_workspace else "")
                            )
                            ui.item_label(" - ".join(caption_bits)).props("caption").classes("text-grey-7").style(
                                "font-size: 0.7rem;"
                            )
                    continue

                row = payload
                tid, name, created, updated, *_rest = row
                _thread_model_ov = _rest[0] if _rest else ""
                _thread_project_id = _rest[1] if len(_rest) > 1 else ""
                _thread_type = _rest[2] if len(_rest) > 2 else ""
                _dev_workspace_id = _rest[3] if len(_rest) > 3 else ""
                _thread_approval_mode = _rest[4] if len(_rest) > 4 else ""
                name = name or ""
                is_active = tid == state.thread_id
                is_running = tid in running_tids
                is_generating_tid = tid in _active_generations
                is_cloud_thread = is_cloud_model(_thread_model_ov or get_current_model())
                is_designer_thread = bool(_thread_project_id)
                is_code_thread = _thread_type == "code" or bool(_dev_workspace_id)

                async def _select(t=tid, n=name, mo=_thread_model_ov, pid=_thread_project_id, dev_ws=_dev_workspace_id, app_mode=_thread_approval_mode):
                    from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                    stop_voice_for_thread_change(state, p, reason="thread_select")
                    prev = state.thread_id
                    prev_gen = _active_generations.get(prev) if prev else None
                    if prev_gen and prev_gen.status == "streaming":
                        prev_gen.detached = True
                        if prev_gen.tts_active:
                            state.tts_service.stop()
                            prev_gen.tts_active = False

                    async def _load_messages_cached(tid_: str) -> list[dict]:
                        cached = state.message_cache.get(tid_)
                        if cached is not None and tid_ not in state.message_cache_dirty:
                            return list(cached)
                        msgs = await run.io_bound(load_thread_messages, tid_)
                        state.message_cache[tid_] = list(msgs)
                        state.message_cache_dirty.discard(tid_)
                        return msgs

                    if pid:
                        # Designer thread - open the associated project
                        from row_bot.designer.storage import load_project
                        proj = load_project(pid)
                        if proj:
                            state.thread_id = t
                            state.thread_name = n
                            state.thread_model_override = mo or ""
                            state.thread_approval_mode = app_mode or await run.io_bound(_get_thread_approval_mode, t)
                            state.messages = await _load_messages_cached(t)
                            p.pending_files.clear()
                            set_active_thread(t, previous_id=prev)
                            state.active_designer_project = proj
                            state.active_developer_workspace_id = None
                            rebuild_main()
                            _rebuild_thread_list_ref[0]()
                            return
                        # Project missing - fall through to normal thread behavior

                    # Non-designer thread (or missing project) - close designer if needed
                    if dev_ws:
                        from row_bot.developer.storage import get_workspace
                        workspace = get_workspace(dev_ws)
                        if workspace:
                            state.active_designer_project = None
                            state.active_developer_workspace_id = dev_ws
                            state.thread_id = t
                            state.thread_name = n
                            state.thread_model_override = mo or ""
                            state.thread_approval_mode = app_mode or await run.io_bound(_get_thread_approval_mode, t)
                            state.messages = await _load_messages_cached(t)
                            p.pending_files.clear()
                            set_active_thread(t, previous_id=prev)
                            rebuild_main()
                            _rebuild_thread_list_ref[0]()
                            return

                    state.active_designer_project = None
                    state.active_developer_workspace_id = None
                    state.thread_id = t
                    state.thread_name = n
                    state.thread_model_override = mo or ""
                    state.thread_approval_mode = app_mode or await run.io_bound(_get_thread_approval_mode, t)
                    state.messages = await _load_messages_cached(t)
                    p.pending_files.clear()
                    set_active_thread(t, previous_id=prev)
                    rebuild_main()
                    _rebuild_thread_list_ref[0]()

                def _delete(t=tid):
                    _del_gen = _active_generations.get(t)
                    if _del_gen:
                        _del_gen.stop_event.set()
                    stop_task(t)
                    _delete_thread(t)
                    clear_summary_cache(t)
                    from row_bot.tools.shell_tool import get_session_manager, clear_shell_history
                    get_session_manager().kill_session(t)
                    clear_shell_history(t)
                    from row_bot.tools.browser_tool import (
                        get_session_manager as get_browser_session_manager,
                        clear_browser_history,
                    )
                    get_browser_session_manager().kill_session(t)
                    clear_browser_history(t)
                    set_active_thread(None, previous_id=t)
                    state.invalidate_thread_cache(t)
                    if state.thread_id == t:
                        from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                        stop_voice_for_thread_change(state, p, reason="delete_active_thread")
                        state.thread_id = None
                        state.thread_name = None
                        from row_bot.approval_policy import DEFAULT_APPROVAL_MODE
                        state.thread_approval_mode = DEFAULT_APPROVAL_MODE
                        state.messages = []
                        state.active_developer_workspace_id = None
                        rebuild_main()
                    _rebuild_thread_list_ref[0]()

                def _rename(t=tid, n=name):
                    show_rename_thread_dialog(
                        thread_id=t,
                        current_name=n,
                        state=state,
                        rebuild_thread_list=_rebuild_thread_list_ref[0],
                        rebuild_main=rebuild_main,
                    )

                item_classes = "w-full rounded"
                item_style = "min-height: 40px; padding: 4px 8px;"
                if is_developer_child:
                    item_classes += " developer-thread-child sidebar-thread-child"
                    item_style += " margin-left: 18px; width: calc(100% - 18px);"

                with ui.item(on_click=_select).classes(item_classes).props(
                    "clickable" + (" active" if is_active else "")
                ).style(item_style):
                    with ui.item_section().props("avatar").style("min-width: 28px;"):
                        if is_generating_tid:
                            _thr_icon = "autorenew"
                        elif is_running:
                            _thr_icon = "hourglass_top"
                        elif is_designer_thread:
                            _thr_icon = "brush"
                        elif is_code_thread:
                            _thr_icon = "code"
                        elif is_cloud_thread:
                            _thr_icon = "cloud"
                        elif name.startswith("\u2708\ufe0f"):
                            _thr_icon = "send"
                        elif name.startswith("\U0001f4e7"):
                            _thr_icon = "email"
                        elif name.startswith("\u26a1"):
                            _thr_icon = "electric_bolt"
                        elif name.startswith(chr(0xFFFD)) or "WhatsApp" in name:
                            _thr_icon = "forum"
                        elif name.startswith("\U0001f3ae") or "Discord" in name:
                            _thr_icon = "sports_esports"
                        elif name.startswith(chr(0xFFFD) + "\U0001f4f1"):
                            _thr_icon = "textsms"
                        elif name.startswith("\U0001f4ac"):
                            _thr_icon = "chat"
                        else:
                            _thr_icon = "computer"
                        _icon_el = ui.icon(_thr_icon, size="xs").classes(
                            "text-primary" if is_active else "text-grey-6"
                        )
                        if is_generating_tid:
                            _icon_el.classes(add="row-bot-spin")
                    with ui.item_section():
                        ui.item_label(name).classes("ellipsis").style(
                            "font-size: 0.85rem;" + ("font-weight: 600;" if is_active else "")
                        )
                        if updated:
                            ui.item_label(_fmt_ts(updated)).props("caption").classes("text-grey-7").style(
                                "font-size: 0.7rem;"
                            )
                    with ui.item_section().props("side"):
                        action_btn = ui.button(icon="more_vert").props("flat dense round size=xs color=grey-6")
                        action_btn.on("click", js_handler="(e) => e.stopPropagation()")
                        with action_btn:
                            with ui.menu():
                                ui.menu_item("Rename", on_click=lambda t=tid, n=name: _rename(t, n))
                                ui.separator()
                                ui.menu_item("Delete", on_click=lambda t=tid: _delete(t))

            if len(threads) > SIDEBAR_MAX_THREADS:
                def _show_all():
                    from row_bot.ui.bulk_select import BulkSelect, render_bulk_action_bar
                    from row_bot.ui.confirm import confirm_destructive
                    from row_bot.threads import delete_threads as _bulk_delete_threads

                    bulk = BulkSelect()

                    def _purge_external(t: str) -> None:
                        """Cleanup outside threads.py: session kills, history,
                        active generation stop, task stop. Safe on missing ids.
                        """
                        try:
                            gen = _active_generations.get(t)
                            if gen:
                                gen.stop_event.set()
                        except Exception:
                            pass
                        try:
                            stop_task(t)
                        except Exception:
                            pass
                        try:
                            clear_summary_cache(t)
                        except Exception:
                            pass
                        try:
                            from row_bot.tools.shell_tool import (
                                get_session_manager, clear_shell_history,
                            )
                            get_session_manager().kill_session(t)
                            clear_shell_history(t)
                        except Exception:
                            pass
                        try:
                            from row_bot.tools.browser_tool import (
                                get_session_manager as get_browser_session_manager,
                                clear_browser_history,
                            )
                            get_browser_session_manager().kill_session(t)
                            clear_browser_history(t)
                        except Exception:
                            pass

                    with ui.dialog() as dlg, ui.card().classes("w-96"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label("All Conversations").classes("text-h6")
                            select_btn = ui.button("Select").props(
                                "flat dense no-caps size=sm"
                            )

                        def _toggle_mode():
                            bulk.toggle_mode()
                            select_btn.text = "Done" if bulk.active else "Select"
                            _rebuild_dialog_list()

                        select_btn.on("click", _toggle_mode)

                        # Classify once for filter + pills
                        from row_bot.threads import get_workflow_thread_ids as _gwf
                        _wf_tids = _gwf()

                        def _cat_modal(pid: str, tid: str, thread_type: str = "", dev_ws: str = "") -> str:
                            if thread_type == "agent_child" or tid in child_thread_ids:
                                return "agents"
                            if pid:
                                return "designer"
                            if thread_type == "code" or dev_ws:
                                return "code"
                            if tid in _wf_tids:
                                return "workflow"
                            return "chat"

                        _modal_counts = {"all": 0, "chat": 0,
                                         "designer": 0, "code": 0, "workflow": 0,
                                         "agents": 0}
                        for _r in threads:
                            _pid = _r[5] if len(_r) > 5 else ""
                            _tt = _r[6] if len(_r) > 6 else ""
                            _dw = _r[7] if len(_r) > 7 else ""
                            _cat_key = _cat_modal(_pid, _r[0], _tt, _dw)
                            _modal_counts[_cat_key] += 1
                            if _cat_key != "agents":
                                _modal_counts["all"] += 1

                        filter_row = ui.row().classes(
                            "w-full gap-1 items-center q-mb-xs"
                        ).style(
                            "display: flex; flex-wrap: wrap; overflow: visible; "
                            "column-gap: 4px; row-gap: 4px; min-height: 34px;"
                        )

                        def _render_modal_pills():
                            filter_row.clear()
                            global _MODAL_FILTER
                            valid_modal_keys = {descriptor["key"] for descriptor in THREAD_FILTER_DESCRIPTORS}
                            if _MODAL_FILTER not in valid_modal_keys:
                                _MODAL_FILTER = "all"
                            with filter_row:
                                for descriptor in THREAD_FILTER_DESCRIPTORS:
                                    key = descriptor["key"]
                                    label = descriptor["label"]
                                    n = _modal_counts[key]
                                    if key != "all" and n == 0:
                                        continue
                                    is_on = _MODAL_FILTER == key

                                    def _set_mf(k=key):
                                        global _MODAL_FILTER
                                        _MODAL_FILTER = k
                                        _render_modal_pills()
                                        _rebuild_dialog_list()

                                    _render_modal_filter_button(
                                        key=key,
                                        label=label,
                                        icon=descriptor["icon"],
                                        count=n,
                                        active=is_on,
                                        on_click=_set_mf,
                                    )

                        _render_modal_pills()

                        list_container = ui.column().classes("w-full gap-0")

                        def _rebuild_dialog_list() -> None:
                            list_container.clear()
                            # Filtered view of threads
                            _filtered = []
                            for r in threads:
                                _cat = _cat_modal(
                                    r[5] if len(r) > 5 else "",
                                    r[0],
                                    r[6] if len(r) > 6 else "",
                                    r[7] if len(r) > 7 else "",
                                )
                                if (
                                    (_MODAL_FILTER == "all" and _cat != "agents")
                                    or _cat == _MODAL_FILTER
                                ):
                                    _filtered.append((r, _cat))
                            with list_container:
                                if not _filtered:
                                    ui.label("Nothing in this filter.").classes(
                                        "text-grey-6 q-pa-md"
                                    )
                                    return
                                display_items = _sidebar_display_items(_filtered, _MODAL_FILTER)
                                with ui.list().props("bordered separator").classes("w-full"):
                                    for item_kind, payload, _cat, is_developer_child in display_items:
                                        if item_kind == "developer_group":
                                            group = payload
                                            workspace_id = str(group.get("workspace_id", ""))
                                            group_rows = list(group.get("rows", []))
                                            is_expanded = workspace_id in _SIDEBAR_DEV_EXPANDED
                                            is_active_workspace = (
                                                state.active_developer_workspace_id == workspace_id
                                                or any(row[0] == state.thread_id for row, _child_cat in group_rows)
                                            )

                                            def _toggle_workspace(wsid=workspace_id):
                                                if wsid in _SIDEBAR_DEV_EXPANDED:
                                                    _SIDEBAR_DEV_EXPANDED.discard(wsid)
                                                else:
                                                    _SIDEBAR_DEV_EXPANDED.add(wsid)
                                                _persist_sidebar_dev_expanded()
                                                _rebuild_dialog_list()

                                            caption_bits = [
                                                f"{len(group_rows)} thread{'s' if len(group_rows) != 1 else ''}",
                                            ]
                                            latest = str(group.get("updated") or "")
                                            if latest:
                                                caption_bits.append(_fmt_ts(latest))
                                            with ui.item(on_click=_toggle_workspace).props("clickable"):
                                                with ui.item_section().props("avatar").style("min-width: 28px;"):
                                                    ui.icon("folder_open" if is_expanded else "folder", size="xs").classes(
                                                        "text-primary" if is_active_workspace else "text-grey-6"
                                                    )
                                                with ui.item_section():
                                                    ui.item_label(str(group.get("name") or "Developer workspace")).classes("ellipsis").style(
                                                        "font-weight: 600;" if is_active_workspace else ""
                                                    )
                                                    ui.item_label(" - ".join(caption_bits)).props("caption")
                                            continue

                                        row = payload
                                        tid, name, created, updated, *_rest2 = row
                                        _mo2 = _rest2[0] if _rest2 else ""
                                        _pid2 = _rest2[1] if len(_rest2) > 1 else ""
                                        _dev_ws2 = _rest2[3] if len(_rest2) > 3 else ""

                                        def _sel(t=tid, n=name, mo=_mo2, pid=_pid2, dev_ws=_dev_ws2):
                                            # In selection mode, clicking a row toggles selection
                                            if bulk.active:
                                                bulk.toggle_item(t)
                                                return
                                            from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                                            stop_voice_for_thread_change(state, p, reason="thread_modal_select")
                                            prev = state.thread_id
                                            prev_gen = _active_generations.get(prev) if prev else None
                                            if prev_gen and prev_gen.status == "streaming":
                                                prev_gen.detached = True
                                                if prev_gen.tts_active:
                                                    state.tts_service.stop()
                                                    prev_gen.tts_active = False
                                            state.thread_id = t
                                            state.thread_name = n
                                            state.thread_model_override = mo or ""
                                            state.messages = load_thread_messages(t)
                                            state.active_designer_project = None
                                            state.active_developer_workspace_id = None
                                            if dev_ws:
                                                from row_bot.developer.storage import get_workspace
                                                if get_workspace(dev_ws):
                                                    state.active_developer_workspace_id = dev_ws
                                            elif pid:
                                                from row_bot.designer.storage import load_project
                                                proj = load_project(pid)
                                                if proj:
                                                    state.active_designer_project = proj
                                            dlg.close()
                                            rebuild_main()
                                            _rebuild_thread_list_ref[0]()

                                        def _del(t=tid):
                                            _purge_external(t)
                                            _delete_thread(t)
                                            if state.thread_id == t:
                                                from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                                                stop_voice_for_thread_change(state, p, reason="delete_active_thread_modal")
                                                state.thread_id = None
                                                state.messages = []
                                            dlg.close()
                                            rebuild_main()
                                            _rebuild_thread_list_ref[0]()

                                        def _ren(t=tid, n=name):
                                            show_rename_thread_dialog(
                                                thread_id=t,
                                                current_name=n,
                                                state=state,
                                                rebuild_thread_list=_rebuild_thread_list_ref[0],
                                                rebuild_main=rebuild_main,
                                                on_renamed=lambda _saved: dlg.close(),
                                            )

                                        item_classes = ""
                                        item_style = ""
                                        if is_developer_child:
                                            item_classes = "developer-thread-child"
                                            item_style = "margin-left: 18px; width: calc(100% - 18px);"

                                        with ui.item(on_click=_sel).classes(item_classes).props("clickable").style(item_style):
                                            if bulk.active:
                                                with ui.item_section().props("avatar").style("min-width: 28px;"):
                                                    cb = ui.checkbox(value=bulk.is_selected(tid))
                                                    cb.on(
                                                        "update:model-value",
                                                        lambda e, t=tid: bulk.toggle_item(
                                                            t, bool(e.args),
                                                        ),
                                                    )
                                                    cb.on(
                                                        "click",
                                                        js_handler="(e) => e.stopPropagation()",
                                                    )
                                            else:
                                                with ui.item_section().props("avatar").style("min-width: 28px;"):
                                                    ui.icon("code" if is_developer_child else "chat_bubble_outline", size="xs")
                                            with ui.item_section():
                                                ui.item_label(name)
                                                if updated:
                                                    ui.item_label(_fmt_ts(updated)).props("caption")
                                            if not bulk.active:
                                                with ui.item_section().props("side"):
                                                    action_btn = ui.button(icon="more_vert").props(
                                                        "flat dense round size=xs color=grey-6"
                                                    )
                                                    action_btn.on("click", js_handler="(e) => e.stopPropagation()")
                                                    with action_btn:
                                                        with ui.menu():
                                                            ui.menu_item("Rename", on_click=lambda t=tid, n=name: _ren(t, n))
                                                            ui.separator()
                                                            ui.menu_item("Delete", on_click=lambda t=tid: _del(t))

                        action_slot = ui.column().classes("w-full")

                        def _do_bulk_delete(ids: list[str]) -> None:
                            def _commit():
                                for t in ids:
                                    _purge_external(t)
                                deleted, failures = _bulk_delete_threads(ids)
                                if state.thread_id in ids:
                                    from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                                    stop_voice_for_thread_change(state, p, reason="bulk_delete_active_thread")
                                    state.thread_id = None
                                    state.thread_name = None
                                    state.messages = []
                                msg = f"🗑️ Deleted {deleted} conversation{'s' if deleted != 1 else ''}."
                                if failures:
                                    msg += f" {len(failures)} failed."
                                ui.notify(msg, type="negative" if failures else "info")
                                dlg.close()
                                rebuild_main()
                                _rebuild_thread_list_ref[0]()

                            noun = "conversation" if len(ids) == 1 else "conversations"
                            confirm_destructive(
                                f"Delete {len(ids)} {noun}?",
                                body=(
                                    "This cannot be undone. Sessions, media, "
                                    "and history will be cleared."
                                ),
                                on_confirm=_commit,
                            )

                        with action_slot:
                            render_bulk_action_bar(
                                bulk,
                                on_delete=_do_bulk_delete,
                                label_singular="conversation",
                                label_plural="conversations",
                                on_clear=_rebuild_dialog_list,
                            )

                        ui.separator()
                        with ui.row().classes("w-full gap-2"):
                            def _delete_all():
                                # Respect current filter: only nuke what's visible.
                                all_ids = []
                                for r in threads:
                                    cat = _cat_modal(
                                        r[5] if len(r) > 5 else "",
                                        r[0],
                                        r[6] if len(r) > 6 else "",
                                        r[7] if len(r) > 7 else "",
                                    )
                                    if (_MODAL_FILTER == "all" and cat != "agents") or cat == _MODAL_FILTER:
                                        all_ids.append(r[0])
                                if not all_ids:
                                    ui.notify("Nothing to delete in this filter.",
                                              type="warning")
                                    return

                                def _commit():
                                    for t in all_ids:
                                        _purge_external(t)
                                    _bulk_delete_threads(all_ids)
                                    from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

                                    stop_voice_for_thread_change(state, p, reason="bulk_delete_all_threads")
                                    state.thread_id = None
                                    state.thread_name = None
                                    state.messages = []
                                    dlg.close()
                                    rebuild_main()
                                    _rebuild_thread_list_ref[0]()

                                scope = (
                                    "conversations"
                                    if _MODAL_FILTER == "all"
                                    else {
                                        "chat": "chats",
                                        "designer": "design conversations",
                                        "code": "code conversations",
                                        "workflow": "workflow conversations",
                                    }[_MODAL_FILTER]
                                )
                                confirm_destructive(
                                    f"Delete all {len(all_ids)} {scope}?",
                                    body="This cannot be undone.",
                                    on_confirm=_commit,
                                )

                            ui.button("Delete all", icon="delete_sweep", on_click=_delete_all).props(
                                "flat color=negative"
                            ).classes("flex-grow")
                            ui.button("Close", on_click=dlg.close).props("flat").classes("flex-grow")

                        _rebuild_dialog_list()
                    dlg.open()

                ui.button(
                    f"Show all ({len(threads)})", on_click=_show_all
                ).classes("w-full q-mt-xs").props("flat dense size=sm")

    _rebuild_thread_list_ref[0] = _rebuild_thread_list
    _rebuild_thread_list()

    return _rebuild_thread_list
