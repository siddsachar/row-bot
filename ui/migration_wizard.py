"""Preferences-launched wizard for previewing and applying Hermes/OpenClaw migrations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from nicegui import run, ui

from migration import (
    MigrationApplyOptions,
    MigrationApplyResult,
    MigrationCategory,
    MigrationItem,
    MigrationPlan,
    MigrationProvider,
    MigrationStatus,
    apply_migration_plan,
    build_migration_plan,
)


PROVIDER_OPTIONS = {
    MigrationProvider.HERMES.value: "Hermes Agent",
    MigrationProvider.OPENCLAW.value: "OpenClaw",
}

PROVIDER_DEFAULT_DIRS = {
    MigrationProvider.HERMES.value: ".hermes",
    MigrationProvider.OPENCLAW.value: ".openclaw",
}

CATEGORY_ORDER = (
    MigrationCategory.MODEL.value,
    MigrationCategory.SETTINGS.value,
    MigrationCategory.MCP.value,
    MigrationCategory.IDENTITY.value,
    MigrationCategory.MEMORIES.value,
    MigrationCategory.SKILLS.value,
    MigrationCategory.API_KEYS.value,
    MigrationCategory.ARCHIVE.value,
    MigrationCategory.CHANNELS.value,
    MigrationCategory.TASKS.value,
    MigrationCategory.DOCUMENTS.value,
)

DEFAULT_EXPANDED_CATEGORIES = (
    MigrationCategory.MODEL.value,
    MigrationCategory.IDENTITY.value,
    MigrationCategory.MEMORIES.value,
    MigrationCategory.SKILLS.value,
)

WIZARD_FLOW_STEPS = (
    "Choose the agent to migrate from, then pick the old agent folder and the Thoth target folder.",
    "Run a read-only scan. This only builds a preview; it does not write to either folder.",
    "Review what will migrate, what is skipped, what is archive-only, and which files conflict.",
    "Confirm and apply selected items. Thoth writes backups and a redacted migration report.",
)

WIZARD_STEP_TITLES = (
    "1. Choose folders",
    "2. Review scan",
    "3. Apply migration",
)

FIELD_HELP = {
    "provider": "Choose the app you are migrating from. Thoth uses this to look for the right files and folders.",
    "source": "The old Hermes/OpenClaw home folder. Thoth reads from this folder during scan and apply.",
    "target": "The Thoth data folder to write into. For tests, use a disposable target folder instead of your real Thoth profile.",
    "secrets": "Off by default. Turn on only when you intentionally want to copy API keys or tokens into the target.",
    "overwrite": "Off by default. Conflicting files stay unselected unless this is enabled; selected conflicts are backed up before replacement.",
    "confirm": "Required before apply so preview review stays separate from writing files.",
}


def friendly_warning_text(warning: str) -> str:
    text = str(warning or "").strip()
    normalized = text.lower()
    if "secret" in normalized:
        return "API keys or tokens were found, but they are not selected by default. Turn on Include API keys and tokens in step 1 only if you want to copy them."
    if "archive-only" in normalized:
        return "Some source files are kept for reference only. Thoth will copy them into the migration report, not activate them in your live setup."
    if "conflicting target" in normalized or "conflict" in normalized:
        return "Some target files already exist. Leave overwrite off to skip them, or turn overwrite on after reviewing what will be replaced."
    return text


def friendly_warnings(warnings: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for warning in warnings:
        friendly = friendly_warning_text(warning)
        if friendly and friendly not in result:
            result.append(friendly)
    return tuple(result)


def category_warning_texts(
    category: str | MigrationCategory,
    items: tuple[MigrationItem, ...] | list[MigrationItem],
    *,
    overwrite: bool = False,
) -> tuple[str, ...]:
    category_value = category.value if isinstance(category, MigrationCategory) else str(category)
    messages: list[str] = []
    if any(item.status == MigrationStatus.CONFLICT for item in items):
        if overwrite:
            messages.append("Overwrite is on for this section. Selected conflicting files will be backed up before replacement.")
        else:
            messages.append("Some files in this section already exist in the target. Turn on overwrite to make them selectable, or leave them skipped.")
    if category_value == MigrationCategory.API_KEYS.value:
        if any(item.status == MigrationStatus.SKIPPED for item in items):
            messages.append("API keys or tokens were found but are not selected. To import them, go back to step 1 and turn on Include API keys and tokens.")
        elif any(item.status == MigrationStatus.SENSITIVE for item in items):
            messages.append("API keys or tokens are selected. Review them carefully; migration reports will hide their values.")
    if category_value == MigrationCategory.ARCHIVE.value and any(item.is_archive_only for item in items):
        messages.append("These files are kept for reference only. Thoth copies them into the migration report, not into the live setup.")
    if category_value == MigrationCategory.MCP.value:
        messages.append("MCP servers stay disabled after import until you review and enable them in Thoth.")
    if category_value in {MigrationCategory.CHANNELS.value, MigrationCategory.SETTINGS.value} and any(
        item.status == MigrationStatus.SKIPPED and item.requires_confirmation for item in items
    ):
        messages.append("These settings need manual review before Thoth can safely activate them.")
    return tuple(dict.fromkeys(messages))


def workflow_steps() -> tuple[str, ...]:
    return WIZARD_FLOW_STEPS


def wizard_step_titles() -> tuple[str, ...]:
    return WIZARD_STEP_TITLES


def field_help_text(key: str) -> str:
    return FIELD_HELP[key]


def default_thoth_data_dir() -> Path:
    return Path(os.environ.get("THOTH_DATA_DIR") or Path.home() / ".thoth").expanduser()


def default_source_dir(provider: str) -> Path:
    return Path.home() / PROVIDER_DEFAULT_DIRS.get(provider, ".hermes")


def plan_with_selected_ids(plan: MigrationPlan, selected_ids: set[str]) -> MigrationPlan:
    return MigrationPlan(
        source=plan.source,
        items=tuple(item.with_selection(item.id in selected_ids) for item in plan.items),
        warnings=plan.warnings,
        metadata=dict(plan.metadata),
    )


def selectable_item_ids(plan: MigrationPlan, *, overwrite: bool = False) -> set[str]:
    allowed = {MigrationStatus.PLANNED, MigrationStatus.SENSITIVE}
    if overwrite:
        allowed.add(MigrationStatus.CONFLICT)
    return {item.id for item in plan.items if item.status in allowed and item.action.value != "manual_review"}


def selectable_category_item_ids(
    plan: MigrationPlan,
    category: str | MigrationCategory,
    *,
    overwrite: bool = False,
) -> set[str]:
    category_value = category.value if isinstance(category, MigrationCategory) else str(category)
    allowed = selectable_item_ids(plan, overwrite=overwrite)
    return {item.id for item in plan.items if item.category.value == category_value and item.id in allowed}


def non_selectable_item_note(item: MigrationItem, *, overwrite: bool = False) -> str:
    if item.is_archive_only:
        return "Report only"
    if item.status == MigrationStatus.CONFLICT and not overwrite:
        return "Turn on overwrite to select"
    if item.status == MigrationStatus.SKIPPED and item.category == MigrationCategory.API_KEYS:
        return "Enable API key import in step 1"
    if item.action.value == "manual_review" or item.requires_confirmation:
        return "Review manually"
    return "Not selectable"


def selected_item_ids(plan: MigrationPlan) -> set[str]:
    return {item.id for item in plan.items if item.selected}


def ordered_category_items(plan: MigrationPlan) -> list[tuple[str, list[MigrationItem]]]:
    grouped = plan.items_by_category()
    ordered: list[tuple[str, list[MigrationItem]]] = []
    for category in CATEGORY_ORDER:
        if category in grouped:
            ordered.append((category, grouped[category]))
    for category, items in grouped.items():
        if category not in CATEGORY_ORDER:
            ordered.append((category, items))
    return ordered


def category_counts(plan: MigrationPlan) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category, items in ordered_category_items(plan):
        rows.append({
            "category": category,
            "total": len(items),
            "selected": sum(1 for item in items if item.selected),
            "ready": sum(1 for item in items if item.status in {MigrationStatus.PLANNED, MigrationStatus.SENSITIVE}),
            "conflicts": sum(1 for item in items if item.status == MigrationStatus.CONFLICT),
            "archive_only": sum(1 for item in items if item.is_archive_only),
            "skipped": sum(1 for item in items if item.status == MigrationStatus.SKIPPED),
        })
    return rows


def result_report_paths(result: MigrationApplyResult) -> dict[str, str]:
    report_dir = result.report_dir
    if report_dir is None:
        return {}
    return {
        "report_dir": str(report_dir),
        "summary": str(report_dir / "summary.md"),
        "result": str(report_dir / "result.json"),
        "plan": str(report_dir / "plan.json"),
        "backup_manifest": str(report_dir / "backup_manifest.json"),
    }


def status_color(status: MigrationStatus) -> str:
    return {
        MigrationStatus.PLANNED: "blue",
        MigrationStatus.SENSITIVE: "orange",
        MigrationStatus.CONFLICT: "red",
        MigrationStatus.ARCHIVE_ONLY: "grey",
        MigrationStatus.SKIPPED: "grey",
        MigrationStatus.BLOCKED: "red",
        MigrationStatus.MIGRATED: "green",
        MigrationStatus.ERROR: "red",
    }.get(status, "grey")


def build_migration_wizard_tab(reopen: Callable[[str], None] | None = None) -> None:
    state: dict[str, Any] = {
        "plan": None,
        "selected_ids": set(),
        "result": None,
        "expanded_categories": set(DEFAULT_EXPANDED_CATEGORIES),
    }

    ui.label("Migration Wizard").classes("text-h6")
    ui.label("Move selected identity, memory, skills, model settings, and reviewed credentials into Thoth with a scan-first apply flow.").classes("text-body2 text-grey-6")

    provider = None
    source_path = None
    target_path = None
    include_secrets = None
    overwrite_conflicts = None
    confirm_apply = None
    summary_col = None
    item_col = None
    result_col = None
    stepper = None

    async def _browse_source() -> None:
        from ui.helpers import browse_folder

        picked = await browse_folder("Select migration source", str(source_path.value or Path.home()))
        if picked:
            source_path.value = picked

    async def _browse_target() -> None:
        from ui.helpers import browse_folder

        picked = await browse_folder("Select Thoth data folder", str(target_path.value or default_thoth_data_dir()))
        if picked:
            target_path.value = picked

    def _current_plan() -> MigrationPlan | None:
        plan = state.get("plan")
        return plan if isinstance(plan, MigrationPlan) else None

    def _render_flow_intro() -> None:
        with ui.column().classes("gap-1 q-mb-md"):
            for index, step in enumerate(workflow_steps(), start=1):
                with ui.row().classes("items-start gap-2 no-wrap"):
                    ui.badge(str(index), color="blue")
                    ui.label(step).classes("text-body2 text-grey-7")

    def _render_summary() -> None:
        summary_col.clear()
        plan = _current_plan()
        if plan is None:
            return
        with summary_col:
            if not plan.source.found:
                ui.label("Source not found.").classes("text-warning text-sm")
            summary = plan.summary
            with ui.row().classes("items-center gap-2"):
                ui.badge(f"{summary.total} items", color="blue")
                ui.badge(f"{summary.selected} selected", color="green" if summary.selected else "grey")
                ui.badge(f"{summary.conflicts} conflicts", color="red" if summary.conflicts else "grey")
                ui.badge(f"{summary.sensitive} sensitive", color="orange" if summary.sensitive else "grey")
                ui.badge(f"{summary.archive_only} archive-only", color="grey")
            if category_counts(plan):
                with ui.grid(columns="repeat(auto-fit, minmax(160px, 1fr))").classes("w-full gap-2"):
                    for row in category_counts(plan):
                        with ui.card().classes("w-full q-pa-sm"):
                            ui.label(str(row["category"]).replace("_", " ").title()).classes("font-medium")
                            ui.label(
                                f'{row["selected"]}/{row["total"]} selected, {row["conflicts"]} conflicts'
                            ).classes("text-caption text-grey-6")
                            notices = category_warning_texts(row["category"], plan.items_by_category()[row["category"]], overwrite=bool(overwrite_conflicts.value))
                            if notices:
                                ui.label(notices[0]).classes("text-caption text-orange-8 q-mt-xs")

    def _set_item_selected(item_id: str, selected: bool) -> None:
        selected_ids = set(state.get("selected_ids") or set())
        if selected:
            selected_ids.add(item_id)
        else:
            selected_ids.discard(item_id)
        state["selected_ids"] = selected_ids
        plan = _current_plan()
        if plan is not None:
            state["plan"] = plan_with_selected_ids(plan, selected_ids)
        _render_summary()

    def _refresh_conflict_selection() -> None:
        plan = _current_plan()
        if plan is None:
            return
        allowed = selectable_item_ids(plan, overwrite=bool(overwrite_conflicts.value))
        selected_ids = set(state.get("selected_ids") or set()) & allowed
        state["selected_ids"] = selected_ids
        state["plan"] = plan_with_selected_ids(plan, selected_ids)
        _render_preview()

    def _set_category_expanded(category: str, expanded: bool) -> None:
        expanded_categories = set(state.get("expanded_categories") or set())
        if expanded:
            expanded_categories.add(category)
        else:
            expanded_categories.discard(category)
        state["expanded_categories"] = expanded_categories

    def _select_category(category: MigrationCategory, selected: bool) -> None:
        plan = _current_plan()
        if plan is None:
            return
        allowed = selectable_category_item_ids(plan, category, overwrite=bool(overwrite_conflicts.value))
        selected_ids = set(state.get("selected_ids") or set())
        for item in plan.items:
            if item.category == category and item.id in allowed:
                if selected:
                    selected_ids.add(item.id)
                else:
                    selected_ids.discard(item.id)
        state["selected_ids"] = selected_ids
        state["plan"] = plan_with_selected_ids(plan, selected_ids)
        _render_preview()

    def _render_items(plan: MigrationPlan) -> None:
        item_col.clear()
        with item_col:
            if not plan.items:
                ui.label("No migration items found.").classes("text-grey-6 text-sm")
                return
            allowed = selectable_item_ids(plan, overwrite=bool(overwrite_conflicts.value))
            expanded_categories = set(state.get("expanded_categories") or set(DEFAULT_EXPANDED_CATEGORIES))
            for category, items in ordered_category_items(plan):
                expansion = ui.expansion(category.replace("_", " ").title(), value=category in expanded_categories).classes("w-full")
                expansion.on_value_change(lambda e, c=category: _set_category_expanded(c, bool(e.value)))
                with expansion:
                    for notice in category_warning_texts(category, items, overwrite=bool(overwrite_conflicts.value)):
                        with ui.row().classes("items-start gap-2 no-wrap q-mb-xs"):
                            ui.icon("warning", color="orange").classes("q-mt-xs")
                            ui.label(notice).classes("text-caption text-orange-8")
                    category_allowed = selectable_category_item_ids(plan, category, overwrite=bool(overwrite_conflicts.value))
                    if category_allowed:
                        with ui.row().classes("items-center gap-2 q-mb-xs"):
                            ui.label("Section actions:").classes("text-caption text-grey-6")
                            ui.button("Select all", on_click=lambda c=items[0].category: _select_category(c, True)).props("flat dense no-caps")
                            ui.button("Clear all", on_click=lambda c=items[0].category: _select_category(c, False)).props("flat dense no-caps")
                    for item in items:
                        selectable = item.id in allowed
                        with ui.row().classes("items-start gap-2 w-full no-wrap"):
                            if selectable:
                                ui.checkbox(value=item.selected, on_change=lambda e, item_id=item.id: _set_item_selected(item_id, bool(e.value)))
                            else:
                                ui.icon("info", color="grey").classes("q-mt-sm").tooltip(non_selectable_item_note(item, overwrite=bool(overwrite_conflicts.value)))
                            with ui.column().classes("gap-0 col"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(item.label or item.id).classes("font-medium")
                                    ui.badge(item.status.value.replace("_", " "), color=status_color(item.status))
                                    if item.requires_confirmation:
                                        ui.badge("review", color="orange")
                                if item.reason:
                                    ui.label(item.reason).classes("text-caption text-grey-6")
                                target = str(item.target or "")
                                if target:
                                    ui.label(target).classes("text-caption text-grey-7")

    def _render_result(result: MigrationApplyResult | None) -> None:
        result_col.clear()
        if result is None:
            return
        paths = result_report_paths(result)
        with result_col:
            ui.separator().classes("q-my-md")
            ui.label("Apply Result").classes("text-h6")
            summary = result.summary
            with ui.row().classes("items-center gap-2"):
                ui.badge(f"{summary.migrated} migrated", color="green")
                ui.badge(f"{summary.skipped} skipped", color="grey")
                ui.badge(f"{summary.blocked} blocked", color="red" if summary.blocked else "grey")
                ui.badge(f"{summary.errors} errors", color="red" if summary.errors else "green")
            if paths:
                for label, value in paths.items():
                    ui.label(f"{label.replace('_', ' ').title()}: {value}").classes("text-caption text-grey-7")
            for item in result.items:
                if item.status in {MigrationStatus.ERROR, MigrationStatus.BLOCKED}:
                    ui.label(f"{item.id}: {item.reason}").classes("text-caption text-negative")

    def _render_preview() -> None:
        plan = _current_plan()
        _render_summary()
        if plan is None:
            item_col.clear()
            return
        _render_items(plan)

    async def _scan() -> None:
        result_col.clear()
        summary_col.clear()
        item_col.clear()
        with summary_col:
            ui.spinner(size="lg")
        try:
            source_root = Path(str(source_path.value or "")).expanduser()
            target_root = Path(str(target_path.value or default_thoth_data_dir())).expanduser()
            plan = await run.io_bound(
                lambda: build_migration_plan(
                    str(provider.value),
                    source_root,
                    target_root=target_root,
                    include_secrets=bool(include_secrets.value),
                )
            )
            state["plan"] = plan
            state["selected_ids"] = selected_item_ids(plan)
            state["result"] = None
            _render_preview()
            if stepper is not None:
                stepper.next()
        except Exception as exc:
            summary_col.clear()
            with summary_col:
                ui.label(f"Scan failed: {exc}").classes("text-negative text-sm")

    async def _apply() -> None:
        plan = _current_plan()
        if plan is None:
            ui.notify("Scan a source before applying.", type="warning")
            return
        if not confirm_apply.value:
            ui.notify("Review the preview and confirm before applying.", type="warning")
            return
        selected_ids = set(state.get("selected_ids") or set())
        apply_plan = plan_with_selected_ids(plan, selected_ids)
        if not apply_plan.apply_candidates and not any(item.selected and item.status == MigrationStatus.CONFLICT for item in apply_plan.items):
            ui.notify("No selected items are ready to apply.", type="warning")
            return
        note = ui.notification("Applying migration...", type="ongoing", spinner=True, timeout=None)
        try:
            options = MigrationApplyOptions(overwrite=bool(overwrite_conflicts.value))
            result = await run.io_bound(lambda: apply_migration_plan(apply_plan, options))
            note.dismiss()
            state["result"] = result
            _render_result(result)
            if result.summary.errors:
                ui.notify("Migration finished with item errors. Review the report.", type="warning")
            else:
                ui.notify("Migration applied. Report written.", type="positive")
        except Exception as exc:
            note.dismiss()
            ui.notify(f"Migration apply failed: {exc}", type="negative")

    with ui.stepper().props("vertical").classes("w-full q-mt-md") as created_stepper:
        stepper = created_stepper
        with ui.step(wizard_step_titles()[0]):
            _render_flow_intro()
            provider = ui.select(
                PROVIDER_OPTIONS,
                value=MigrationProvider.HERMES.value,
                label="Agent to migrate from",
            ).classes("w-full")
            ui.label(field_help_text("provider")).classes("text-caption text-grey-6")

            source_path = ui.input("Old agent folder", value=str(default_source_dir(provider.value))).classes("w-full")
            ui.label(field_help_text("source")).classes("text-caption text-grey-6")
            ui.button("Browse old agent folder", icon="folder_open", on_click=_browse_source).props("flat dense")

            target_path = ui.input("Thoth target data folder", value=str(default_thoth_data_dir())).classes("w-full q-mt-sm")
            ui.label(field_help_text("target")).classes("text-caption text-grey-6")
            ui.button("Browse Thoth target folder", icon="folder_open", on_click=_browse_target).props("flat dense")

            include_secrets = ui.checkbox("Include API keys and tokens", value=False).classes("q-mt-sm")
            ui.label(field_help_text("secrets")).classes("text-caption text-grey-6 q-ml-md")

            provider.on_value_change(lambda e: source_path.set_value(str(default_source_dir(str(e.value)))))
            with ui.stepper_navigation():
                ui.button("Scan preview", icon="search", on_click=_scan).props("unelevated")

        with ui.step(wizard_step_titles()[1]):
            ui.label("Use the preview to decide what should move. Disabled rows need review or an option change before apply.").classes("text-body2 text-grey-6")
            overwrite_conflicts = ui.checkbox("Allow overwrite of conflicting target files", value=False, on_change=lambda _e: _refresh_conflict_selection()).classes("q-mt-sm")
            ui.label(field_help_text("overwrite")).classes("text-caption text-grey-6 q-ml-md")
            summary_col = ui.column().classes("w-full gap-2 q-mt-md")
            item_col = ui.column().classes("w-full gap-2")
            with ui.stepper_navigation():
                ui.button("Continue to apply", icon="arrow_forward", on_click=stepper.next).props("unelevated")
                ui.button("Back", icon="arrow_back", on_click=stepper.previous).props("flat")
                ui.button("Rescan", icon="refresh", on_click=_scan).props("flat")

        with ui.step(wizard_step_titles()[2]):
            ui.label("Apply writes only the currently selected items. Reports are redacted; backups are written before replacing existing files.").classes("text-body2 text-grey-6")
            confirm_apply = ui.checkbox("I reviewed the preview and want to apply the selected items", value=False).classes("q-mt-sm")
            ui.label(field_help_text("confirm")).classes("text-caption text-grey-6 q-ml-md")
            result_col = ui.column().classes("w-full gap-2")
            with ui.stepper_navigation():
                ui.button("Apply selected", icon="play_arrow", on_click=_apply).props("color=primary")
                ui.button("Back", icon="arrow_back", on_click=stepper.previous).props("flat")

    if reopen:
        ui.button("Refresh Wizard", icon="refresh", on_click=lambda: reopen("Migration")).props("flat")
