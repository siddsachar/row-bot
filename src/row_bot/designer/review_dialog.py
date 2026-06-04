"""Unified Review & Repair dialog — replaces the separate Page-Review and
Brand-Lint dialogs. Lists critique + brand findings in one place with
per-finding auto-fix (deterministic) and AI-fix (agent) actions.
"""

from __future__ import annotations

from typing import Callable

from nicegui import ui

from row_bot.designer.review import (
    build_review_report,
    apply_fix,
    apply_fixes_bulk,
    request_ai_fix,
)


_SEVERITY_COLORS = {"high": "red-6", "medium": "orange-7", "low": "grey-6"}

_CATEGORY_GROUP = {
    "hierarchy": "layout",
    "overflow": "layout",
    "contrast": "layout",
    "readability": "layout",
    "spacing": "layout",
    "off_palette": "brand",
    "font": "brand",
    "missing_alt": "brand",
    "logo_safe_zone": "brand",
}

_GROUP_LABEL = {"layout": "Layout & readability", "brand": "Brand & accessibility"}


def open_review_dialog(
    project,
    *,
    refresh_editor: Callable[..., None],
    send_agent_message: Callable,
) -> None:
    """Open the unified Review dialog.

    ``refresh_editor`` — called after applied fixes to re-render preview.
    ``send_agent_message`` — sync/async callable that submits a chat turn;
    used by the "Fix with AI" action.
    """
    dismissed: set[str] = set()
    scope_state = ["page"]  # "page" | "project"

    def _refresh_preview() -> None:
        try:
            refresh_editor(force_preview=True)
        except TypeError:
            try:
                refresh_editor()
            except Exception:
                pass

    with ui.dialog() as dlg, ui.card().style(
        "min-width: 640px; max-width: 780px; padding: 14px 16px; "
        "max-height: 86vh; display: flex; flex-direction: column;"
    ):
        header = ui.row().classes("w-full items-center justify-between no-wrap")
        scope_row = ui.row().classes(
            "w-full items-center justify-between q-mt-xs no-wrap"
        )
        body = ui.column().classes("w-full gap-0").style(
            "flex: 1 1 auto; overflow-y: auto; margin-top: 10px; "
            "padding-right: 4px;"
        )
        footer = ui.row().classes("w-full items-center justify-between q-mt-sm")

        def _rescan() -> None:
            dismissed.clear()
            _render()

        def _set_scope(value: str) -> None:
            scope_state[0] = "project" if value == "Whole project" else "page"
            _render()

        def _apply_one(finding: dict) -> None:
            result = apply_fix(project, finding)
            if result.get("applied"):
                _refresh_preview()
                ui.notify(
                    f"Applied {len(result['changes'])} change(s).",
                    type="positive",
                )
            else:
                ui.notify(
                    result.get("reason") or "No changes applied.",
                    type="warning",
                )
            _render()

        def _apply_all_safe(findings: list[dict]) -> None:
            result = apply_fixes_bulk(project, findings)
            n = result.get("applied", 0)
            if n:
                _refresh_preview()
                ui.notify(
                    f"Applied {n} safe fix(es) across "
                    f"{result.get('pages_touched', 0)} page(s).",
                    type="positive",
                )
            else:
                ui.notify("No safe fixes available.", type="info")
            _render()

        def _fix_with_ai(finding: dict) -> None:
            request_ai_fix(finding, send_agent_message)
            ui.notify("Sent fix request to the agent.", type="info")
            dlg.close()

        def _dismiss(finding_id: str) -> None:
            dismissed.add(finding_id)
            _render()

        def _render_finding(f: dict) -> None:
            sev = f.get("severity", "low")
            sev_color = _SEVERITY_COLORS.get(sev, "grey-6")
            with ui.row().classes(
                "w-full items-start no-wrap q-pa-sm"
            ).style(
                "border-bottom: 1px solid rgba(255,255,255,0.06); gap: 10px;"
            ):
                ui.badge(sev).props(f"color={sev_color}").classes("q-mt-xs")
                with ui.column().classes("gap-0").style(
                    "flex: 1 1 auto; min-width: 0;"
                ):
                    ui.label(
                        f"Page {f['page_index'] + 1} · "
                        f"{f['category']} · {f['source'].replace('_', ' ')}"
                    ).classes("text-xs text-grey-6")
                    ui.label(f["message"]).classes("text-sm")
                    if f.get("excerpt"):
                        ui.label(f"\u201c{f['excerpt']}\u201d").classes(
                            "text-xs text-grey-5"
                        ).style("font-style: italic;")
                    if f.get("suggested_fix"):
                        ui.label(f["suggested_fix"]).classes(
                            "text-xs text-grey-7"
                        )
                    with ui.row().classes("gap-1 q-mt-xs"):
                        if f.get("auto_fixable"):
                            ui.button(
                                "Apply safe fix", icon="auto_fix_high",
                                on_click=lambda _e=None, _f=f: _apply_one(_f),
                            ).props("flat dense color=primary size=sm")
                        ui.button(
                            "Fix with AI", icon="smart_toy",
                            on_click=lambda _e=None, _f=f: _fix_with_ai(_f),
                        ).props("flat dense color=deep-purple size=sm")
                        ui.button(
                            "Dismiss", icon="close",
                            on_click=lambda _e=None, _id=f["id"]: _dismiss(_id),
                        ).props("flat dense color=grey size=sm")

        def _render() -> None:
            header.clear()
            scope_row.clear()
            body.clear()
            footer.clear()

            report = build_review_report(
                project, scope=scope_state[0], dismissed=dismissed,
            )
            findings: list[dict] = report["findings"]

            with header:
                with ui.column().classes("gap-0"):
                    ui.label("Review & Repair").classes(
                        "text-subtitle1 text-weight-medium"
                    )
                    ui.label(report["summary"]).classes("text-xs text-grey-6")
                with ui.row().classes("gap-1 items-center"):
                    sc = report["severity_counts"]
                    if sc.get("high"):
                        ui.badge(f"{sc['high']} high").props("color=red-6")
                    if sc.get("medium"):
                        ui.badge(f"{sc['medium']} medium").props("color=orange-7")
                    if sc.get("low"):
                        ui.badge(f"{sc['low']} low").props("color=grey-6")
                    if scope_state[0] == "page" and findings:
                        ui.badge(f"Score {report['score']}/100").props(
                            "color=blue-grey-6"
                        )

            with scope_row:
                ui.toggle(
                    ["This page", "Whole project"],
                    value=("Whole project" if scope_state[0] == "project"
                           else "This page"),
                    on_change=lambda e: _set_scope(e.value),
                ).props("dense unelevated")
                with ui.row().classes("gap-1 items-center"):
                    if dismissed:
                        ui.label(f"{len(dismissed)} dismissed").classes(
                            "text-xs text-grey-5"
                        )
                    ui.button(icon="refresh", on_click=_rescan).props(
                        "flat dense round color=grey-6"
                    ).tooltip("Re-scan (clears dismissed)")

            with body:
                if not findings:
                    with ui.column().classes(
                        "w-full items-center q-pa-lg gap-1"
                    ):
                        ui.icon("check_circle").classes(
                            "text-positive"
                        ).style("font-size: 36px;")
                        ui.label("Everything looks good.").classes(
                            "text-sm text-grey-5"
                        )
                else:
                    groups: dict[str, list[dict]] = {"layout": [], "brand": []}
                    for f in findings:
                        g = _CATEGORY_GROUP.get(f["category"], "layout")
                        groups[g].append(f)
                    for group_key in ("layout", "brand"):
                        items = groups[group_key]
                        if not items:
                            continue
                        label = _GROUP_LABEL[group_key]
                        with ui.expansion(
                            f"{label} ({len(items)})", value=True,
                        ).classes("w-full").props("dense"):
                            for f in items:
                                _render_finding(f)

            safe_count = sum(1 for f in findings if f.get("auto_fixable"))
            with footer:
                if findings:
                    ui.label(
                        f"{safe_count} safe fix(es) available"
                    ).classes("text-xs text-grey-6")
                else:
                    ui.label("").classes("text-xs")
                with ui.row().classes("gap-1"):
                    if safe_count:
                        ui.button(
                            "Apply all safe fixes", icon="auto_fix_high",
                            on_click=lambda _e=None, _fs=findings: _apply_all_safe(_fs),
                        ).props("unelevated color=primary dense")
                    ui.button("Close", on_click=dlg.close).props("flat dense")

        _render()
        dlg.open()
