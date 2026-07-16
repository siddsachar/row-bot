"""NiceGUI dialog for browsing and installing public skills."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from typing import Callable

from nicegui import run, ui

from .catalog import inspect_entry, search_skills, source_metadata
from .installer import ConflictPolicy, install_bundle
from .models import InstallResult, SkillBundle, SkillHubEntry
from .scanner import scan_bundle

SOURCE_OPTIONS = {
    "all": "All",
    "github": "GitHub",
    "skills_sh": "skills.sh",
    "browse_sh": "browse.sh",
    "clawhub": "ClawHub",
    "lobehub": "LobeHub",
}


def install_preview_bundle(
    bundle: SkillBundle,
    *,
    make_available: bool = False,
    conflict_policy: ConflictPolicy = "keep_existing",
) -> InstallResult:
    """Pure helper used by UI actions and tests."""
    return install_bundle(
        bundle,
        enabled=bool(make_available),
        conflict_policy=conflict_policy,
    )


def open_skills_hub_dialog(
    *,
    on_change: Callable[[], None] | None = None,
    initial_query: str = "",
) -> None:
    """Open the public skills browser dialog."""
    filter_state = {"value": "all"}

    with ui.dialog().props("maximized data-docs-id=skills-hub").classes("overflow-hidden") as dialog:
        with ui.card().classes("w-full h-full no-shadow flex flex-col").style(
            "max-width: 76rem; margin: 0 auto; height: 100vh; max-height: 100vh; overflow:hidden;"
        ):
            with ui.row().classes("w-full items-start justify-between px-4 pt-3 pb-1 shrink-0"):
                with ui.column().classes("gap-0"):
                    ui.label("Browse Public Skills").classes("text-h5")
                    ui.label(
                        "Public skills import into your local Skill Library and are off by default."
                    ).classes("text-caption text-grey-6")
                ui.button(icon="close", on_click=dialog.close).props("flat round size=sm")

            ui.separator().classes("shrink-0")

            with ui.column().classes("w-full gap-2 q-pa-md shrink-0"):
                with ui.row().classes("w-full items-end gap-2 no-wrap"):
                    query = ui.input(
                        "Search",
                        value=initial_query,
                        placeholder="Search public skills or paste a skill source",
                    ).classes("col")
                    ui.button("Search", icon="search", on_click=lambda: _search(False)).props("unelevated")
                    ui.button("Refresh", icon="refresh", on_click=lambda: _search(True)).props("flat")
                    ui.button(
                        "Import from Source",
                        icon="input",
                        on_click=lambda: _open_import_dialog(),
                    ).props("flat color=primary")
                chips_row = ui.row().classes("w-full items-center gap-1")
                status_label = ui.label("").classes("text-caption text-grey-6")
                status_row = ui.row().classes("w-full items-center gap-1")

            with ui.splitter(value=42).classes("w-full flex-grow").style(
                "flex: 1 1 0; min-height:0; height:100%; max-height:100%; overflow:hidden;"
            ) as splitter:
                with splitter.before:
                    results_col = ui.column().classes("w-full h-full gap-2 q-pa-md overflow-auto").style(
                        "height:100%; max-height:100%; min-height:0; overflow-y:auto; overflow-x:hidden;"
                    )
                with splitter.after:
                    preview_col = ui.column().classes("w-full h-full gap-3 q-pa-md overflow-auto").style(
                        "height:100%; max-height:100%; min-height:0; overflow-y:auto; overflow-x:hidden;"
                    )

            def _render_chips() -> None:
                chips_row.clear()
                chips = _available_filter_chips()
                with chips_row:
                    for chip_id, label in chips:
                        active = filter_state["value"] == chip_id
                        ui.button(
                            label,
                            on_click=lambda _=None, value=chip_id: _set_filter(value),
                        ).props(("unelevated " if active else "flat ") + "dense no-caps").classes("text-xs")

            async def _set_filter(value: str) -> None:
                filter_state["value"] = value
                _render_chips()
                await _search(False)

            async def _search(force_refresh: bool = False) -> None:
                results_col.clear()
                preview_col.clear()
                status_row.clear()
                with results_col:
                    ui.spinner(size="lg")
                try:
                    result = await run.io_bound(
                        search_skills,
                        query.value or "",
                        source=filter_state["value"],
                        force_refresh=force_refresh,
                    )
                    entries = result.entries
                    status_label.text = _format_status(result.mode, len(entries), result.error, detected_kind=(result.detected_input.kind if result.detected_input else ""))
                except Exception as exc:
                    entries = []
                    result = None
                    status_label.text = "Public skill search unavailable."
                    ui.notify(f"Skill search failed: {exc}", type="warning")
                _render_statuses(status_row, result.source_statuses if result else [])
                results_col.clear()
                with results_col:
                    if not entries:
                        ui.label("No public skills found for this search.").classes("text-grey-6 text-sm")
                        ui.button(
                            "Import from Source",
                            icon="input",
                            on_click=lambda: _open_import_dialog(),
                        ).props("flat color=primary dense")
                    for entry in entries:
                        _render_entry(entry, preview_col, on_change)
                if len(entries) == 1 and result and result.detected_input and result.detected_input.is_import_like:
                    await _preview_entry(entries[0], preview_col, on_change)

            def _open_import_dialog() -> None:
                with ui.dialog() as import_dialog:
                    with ui.card().classes("w-full").style("max-width: 48rem;"):
                        ui.label("Import from Source").classes("text-h6")
                        source_input = ui.textarea(
                            "Source",
                            placeholder=(
                                "GitHub repo/path, marketplace URL, website URL, direct SKILL.md URL, "
                                "well-known index URL, or pasted full markdown"
                            ),
                        ).props("autogrow").classes("w-full")
                        message = ui.label("").classes("text-caption text-grey-6")

                        async def _preview_import() -> None:
                            value = source_input.value or ""
                            if not value.strip():
                                message.text = "Paste a skill source first."
                                return
                            message.text = "Detecting source..."
                            try:
                                result = await run.io_bound(search_skills, value, force_refresh=True)
                            except Exception as exc:
                                message.text = f"Preview failed: {exc}"
                                return
                            if not result.entries:
                                message.text = result.error or "No skills were detected from that source."
                                return
                            query.value = ""
                            status_label.text = _format_status(result.mode, len(result.entries), result.error, detected_kind=(result.detected_input.kind if result.detected_input else ""))
                            _render_statuses(status_row, result.source_statuses)
                            results_col.clear()
                            preview_col.clear()
                            with results_col:
                                for entry in result.entries:
                                    _render_entry(entry, preview_col, on_change)
                            import_dialog.close()
                            if len(result.entries) == 1:
                                await _preview_entry(result.entries[0], preview_col, on_change)

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Cancel", on_click=import_dialog.close).props("flat")
                            ui.button("Preview", icon="visibility", on_click=_preview_import).props("unelevated color=primary")
                import_dialog.open()

            async def _on_show(_=None) -> None:
                _render_chips()
                await _search(False)

            query.on("keydown.enter.exact.prevent", lambda _: _search(False))
            dialog.on("show", _on_show)

    dialog.open()


def _render_entry(entry: SkillHubEntry, preview_col, on_change: Callable[[], None] | None) -> None:
    with ui.card().classes("w-full q-pa-sm"):
        with ui.row().classes("items-start justify-between w-full no-wrap"):
            with ui.column().classes("gap-1").style("min-width: 0;"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(entry.name).classes("text-body1 text-weight-medium")
                    _render_entry_badges(entry)
                    _installed_badge(entry)
                ui.label(entry.description or "No description provided.").classes("text-caption text-grey-6")
                if entry.author:
                    ui.label(entry.author).classes("text-caption text-grey-7")
                if entry.tags:
                    with ui.row().classes("gap-1"):
                        for tag in entry.tags[:6]:
                            ui.badge(str(tag), color="grey").props("outline")
            ui.button(
                "Preview",
                icon="visibility",
                on_click=lambda e=entry: _preview_entry(e, preview_col, on_change),
            ).props("flat dense")


async def _preview_entry(entry: SkillHubEntry, preview_col, on_change: Callable[[], None] | None) -> None:
    preview_col.clear()
    with preview_col:
        ui.spinner(size="lg")
    try:
        bundle = await run.io_bound(inspect_entry, entry)
        scan = await run.io_bound(scan_bundle, bundle)
    except Exception as exc:
        preview_col.clear()
        with preview_col:
            ui.label("Preview unavailable").classes("text-negative text-subtitle2")
            with ui.row().classes("items-center gap-2"):
                _render_entry_badges(entry)
            ui.label(str(exc)).classes("text-caption text-grey-6")
            if entry.url:
                ui.link("Open Source", entry.url, new_tab=True).classes("text-caption")
        return

    preview_col.clear()
    with preview_col:
        with ui.row().classes("items-center gap-2"):
            ui.label(bundle.frontmatter.get("display_name") or entry.name).classes("text-h6")
            _render_entry_badges(entry)
        ui.label(bundle.frontmatter.get("description") or entry.description or "").classes("text-caption text-grey-6")
        if bundle.metadata.get("source_warning") or entry.metadata.get("source_warning"):
            ui.label(bundle.metadata.get("source_warning") or entry.metadata.get("source_warning")).classes(
                "w-full q-pa-sm rounded-borders bg-orange-1 text-orange-10 text-caption"
            )
        _render_metadata(bundle, entry)
        _render_findings(scan)
        ui.label("SKILL.md Preview").classes("text-subtitle2 q-mt-sm")
        primary = bundle.primary_file()
        _render_text_preview((primary.text if primary else "")[:6000])
        ui.label("Files").classes("text-subtitle2")
        ui.markdown("```text\n" + "\n".join(bundle.file_tree()) + "\n```", extras=["fenced-code-blocks"])

        async def _install(make_available: bool) -> None:
            note = ui.notification("Installing public skill...", type="ongoing", spinner=True, timeout=None)
            try:
                result = await run.io_bound(
                    install_preview_bundle,
                    bundle,
                    make_available=make_available,
                )
                note.dismiss()
                if result.success:
                    ui.notify(result.message, type="positive")
                    if on_change:
                        on_change()
                else:
                    ui.notify(result.message, type="negative")
            except Exception as exc:
                note.dismiss()
                ui.notify(f"Install failed: {exc}", type="negative")

        with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
            if entry.url:
                ui.link("Open Source", entry.url, new_tab=True).classes("q-mr-auto text-caption")
            ui.button(
                "Install",
                icon="download",
                on_click=lambda: _install(False),
            ).props(("unelevated color=primary" + (" disable" if scan.blocked else ""))).tooltip("Import this skill and leave it Off.")
            ui.button(
                "Install & Make Available",
                icon="toggle_on",
                on_click=lambda: _install(True),
            ).props(("flat color=primary" + (" disable" if scan.blocked else ""))).tooltip("Import this skill and immediately make it available.")


def _render_metadata(bundle: SkillBundle, entry: SkillHubEntry) -> None:
    meta = {
        "name": bundle.frontmatter.get("name"),
        "author": bundle.frontmatter.get("author") or entry.author,
        "version": bundle.frontmatter.get("version"),
        "source": _source_label(entry.source),
        "publisher": entry.metadata.get("publisher") or entry.author,
        "manifest": entry.metadata.get("manifest_badge"),
        "source_url": entry.url or bundle.metadata.get("url"),
        "install_ref": bundle.install_ref,
        "install_path": f"~/.row-bot/skills/{bundle.frontmatter.get('name') or bundle.root_name}",
    }
    clean = {key: value for key, value in meta.items() if value}
    if clean:
        ui.markdown("```json\n" + json.dumps(clean, indent=2) + "\n```", extras=["fenced-code-blocks"])


def _render_text_preview(text: str) -> None:
    escaped = html.escape(text or "")
    ui.html(
        "<pre style=\"max-width:100%; overflow:auto; white-space:pre; "
        "background:#1f2937; color:#d1d5db; border-radius:6px; padding:16px; "
        "font-size:13px; line-height:1.45;\"><code>"
        + escaped
        + "</code></pre>"
    ).classes("w-full")


def _render_findings(scan) -> None:
    grouped = defaultdict(list)
    for finding in scan.findings:
        grouped[finding.severity].append(finding)
    for severity, color in (("block", "red"), ("warn", "orange"), ("info", "grey")):
        findings = grouped.get(severity) or []
        if not findings:
            continue
        ui.label(severity.title()).classes("text-subtitle2")
        for finding in findings:
            with ui.row().classes("items-start gap-2 w-full no-wrap"):
                ui.badge(finding.code, color=color).props("outline")
                ui.label(finding.message).classes("text-caption text-grey-7")
                if finding.path:
                    ui.label(finding.path).classes("text-caption text-grey-5")


def _render_statuses(container, statuses) -> None:
    container.clear()
    with container:
        for status in statuses:
            label = _source_label(status.source_id)
            suffix = status.status
            color = {
                "live": "green",
                "cached": "blue-grey",
                "stale": "orange",
                "partial": "orange",
                "error": "red",
                "empty": "grey",
            }.get(status.status, "grey")
            badge = ui.badge(f"{label}: {suffix}", color=color).props("outline")
            if status.message:
                badge.tooltip(status.message)


def _available_filter_chips() -> list[tuple[str, str]]:
    chips = [("all", "All")]
    for meta in source_metadata():
        if not (meta.get("supports_browse") or meta.get("supports_search")):
            continue
        source_id = str(meta.get("id") or "")
        if source_id in {"", "direct_url", "well_known", "pasted_markdown", "claude_marketplace"}:
            continue
        chip_id = str(meta.get("source_group") or source_id)
        chips.append((chip_id, str(meta.get("display_name") or _source_label(chip_id))))
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for chip in chips:
        if chip[0] in seen:
            continue
        seen.add(chip[0])
        result.append(chip)
    return result


def _format_status(mode: str, count: int, error: str = "", *, detected_kind: str = "") -> str:
    if detected_kind and detected_kind not in {"empty", "keyword"}:
        return f"Detected {detected_kind.replace('_', ' ')}. Showing {count} preview result{'s' if count != 1 else ''}."
    if mode == "live":
        return f"Showing {count} live public result{'s' if count != 1 else ''}."
    if mode == "partial":
        return f"Showing {count} public result{'s' if count != 1 else ''}; some sources need attention."
    if mode == "cache":
        return f"Showing {count} cached public result{'s' if count != 1 else ''}."
    if mode == "error":
        return error or "Public sources are unavailable."
    return "No public skills found for this search."


def _source_label(source: str) -> str:
    return SOURCE_OPTIONS.get(source, source.replace("_", " ").title())


def _trust_label(trust: str) -> str:
    lower = (trust or "community").lower()
    if "high" in lower:
        return "High-risk"
    if lower in {"trusted", "trusted_publisher", "verified", "official"}:
        return "Publisher"
    return "Community"


def _trust_color(trust: str) -> str:
    lower = (trust or "community").lower()
    if "high" in lower:
        return "red"
    if lower in {"trusted", "trusted_publisher", "verified", "official"}:
        return "green"
    return "orange"


def _render_entry_badges(entry: SkillHubEntry) -> None:
    ui.badge(_source_label(entry.source), color="blue").props("outline")
    publisher = str(entry.metadata.get("publisher") or "").strip()
    if publisher and publisher.lower() != _source_label(entry.source).lower():
        ui.badge(publisher, color="teal").props("outline")
    manifest = str(entry.metadata.get("manifest_badge") or "").strip()
    if manifest:
        ui.badge(manifest, color="purple").props("outline")
    ui.badge(_trust_label(entry.trust_level), color=_trust_color(entry.trust_level)).props("outline")


def _installed_badge(entry: SkillHubEntry) -> None:
    if not entry.metadata.get("installed"):
        return
    state = str(entry.metadata.get("installed_state") or "off")
    if state == "available":
        ui.badge("Available", color="green").props("outline")
    else:
        ui.badge("Installed Off", color="blue-grey").props("outline")
