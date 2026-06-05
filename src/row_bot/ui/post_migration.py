"""One-time post-migration report for the Row-Bot v4 rebrand."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from row_bot.brand import APP_DISPLAY_NAME, DEFAULT_DATA_DIR_NAME, DEFAULT_WORKSPACE_DIR_NAME
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.migration.row_bot_legacy_rebrand import MARKER_REL, MIGRATION_ID, REPORTS_REL, REPORT_PREFIX
from row_bot.ui.helpers import load_app_config, save_app_config
from row_bot.ui.timer_utils import defer_ui

logger = logging.getLogger(__name__)

NOTICE_SEEN_CONFIG_KEY = "row_bot_v4_rebrand_notice_seen"
NOTICE_SEEN_REPORT_CONFIG_KEY = "row_bot_v4_rebrand_notice_seen_report"


def _read_json(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Could not read migration notice JSON from %s", path, exc_info=True)
    return {}


def latest_rebrand_report(data_dir: Path | None = None) -> dict[str, Any]:
    root = (data_dir or get_row_bot_data_dir()).expanduser()
    reports_dir = root / REPORTS_REL
    candidates: list[Path] = []

    marker = _read_json(root / MARKER_REL)
    marker_report = marker.get("report_path") if isinstance(marker, dict) else ""
    if marker_report:
        marker_path = Path(str(marker_report)).expanduser()
        if marker_path.exists():
            candidates.append(marker_path)

    if reports_dir.exists():
        candidates.extend(path for path in reports_dir.glob(f"{REPORT_PREFIX}*.json") if path.is_file())

    if not candidates:
        return {}

    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    report = _read_json(latest)
    if isinstance(report, dict):
        report.setdefault("report_path", str(latest))
        return report
    return {}


def _secret_lines(secret_migration: Any) -> list[str]:
    if not isinstance(secret_migration, dict):
        return []
    labels = {
        "api_keys": "API keys",
        "channel_secrets": "Channel credentials",
        "plugin_secrets": "Plugin credentials",
        "provider_secrets": "Provider accounts",
    }
    lines: list[str] = []
    for key, values in secret_migration.items():
        if not isinstance(values, dict):
            continue
        copied = int(values.get("copied", 0) or 0)
        metadata = int(values.get("metadata_updated", 0) or 0)
        failed = int(values.get("failed", 0) or 0)
        if not (copied or metadata or failed):
            continue
        label = labels.get(str(key), str(key).replace("_", " ").title())
        parts = []
        if copied:
            parts.append(f"copied {copied}")
        if metadata:
            parts.append(f"metadata repaired {metadata}")
        if failed:
            parts.append(f"failed {failed}")
        lines.append(f"{label}: {', '.join(parts)}")
    return lines


def _workspace_headline(workspace: dict[str, Any]) -> str:
    action = str(workspace.get("action") or "")
    if action == "rewritten_to_row_bot_default":
        return f"Workspace set to Documents/{DEFAULT_WORKSPACE_DIR_NAME}; old workspace files were left untouched."
    if action == "preserved_custom_workspace":
        return "Your custom workspace was preserved."
    if action == "preserved_custom_inside_legacy_default":
        return "Your custom workspace was preserved inside the legacy workspace tree."
    if action == "already_row_bot_default":
        return f"Workspace already uses Documents/{DEFAULT_WORKSPACE_DIR_NAME}."
    return f"New filesystem workspace default is Documents/{DEFAULT_WORKSPACE_DIR_NAME}."


def build_notice_payload(report: dict[str, Any]) -> dict[str, Any]:
    report_path = str(report.get("report_path") or "")
    notice_id = report_path or str(report.get("completed_at") or MIGRATION_ID)
    workspace = report.get("workspace_migration")
    if not isinstance(workspace, dict):
        workspace = {}

    copied = int(report.get("files_copied_count", 0) or 0)
    rewritten = int(report.get("files_rewritten_count", 0) or 0)
    skipped = int(report.get("files_skipped_count", 0) or 0)
    source = str(report.get("source") or "")
    target = str(report.get("target") or "")

    next_steps = [
        f"Keep the old data folder as a backup until you verify conversations, providers, channels, OAuth, tools, Buddy, MCP, plugins, and skills.",
        f"Review the legacy workspace before deleting it. Row-Bot does not move user-owned workspace files automatically.",
        f"If you used the old default workspace, copy or move selected files from Documents/Row-Bot into Documents/{DEFAULT_WORKSPACE_DIR_NAME}.",
        "If you use a custom workspace, keep using it; no workspace move is required.",
    ]
    if source:
        next_steps[0] = f"Keep {source} as a backup until you verify conversations, providers, channels, OAuth, tools, Buddy, MCP, plugins, and skills."

    return {
        "id": notice_id,
        "title": f"{APP_DISPLAY_NAME} migration complete",
        "status": str(report.get("status") or ""),
        "source": source,
        "target": target,
        "report_path": report_path,
        "summary": [
            f"Copied {copied} file(s)",
            f"Rewritten {rewritten} app-owned file(s)",
            f"Skipped {skipped} existing target file(s)",
        ],
        "secret_lines": _secret_lines(report.get("secret_migration")),
        "warnings": [str(item) for item in report.get("warnings", []) if item],
        "workspace": {
            **workspace,
            "headline": _workspace_headline(workspace),
        },
        "next_steps": next_steps,
    }


def should_show_post_migration_notice(
    *,
    data_dir: Path | None = None,
    app_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    report = latest_rebrand_report(data_dir)
    if not report:
        return None
    if str(report.get("migration_id") or "") != MIGRATION_ID:
        return None
    if str(report.get("status") or "") not in {"completed", "already_completed"}:
        return None

    payload = build_notice_payload(report)
    cfg = load_app_config() if app_config is None else app_config
    seen_value = cfg.get(NOTICE_SEEN_CONFIG_KEY)
    if seen_value is True or str(seen_value or "") == MIGRATION_ID:
        return None
    if str(cfg.get(NOTICE_SEEN_REPORT_CONFIG_KEY) or ""):
        return None
    return payload


def mark_post_migration_notice_seen(notice_id: str) -> None:
    cfg = load_app_config()
    cfg[NOTICE_SEEN_CONFIG_KEY] = MIGRATION_ID
    cfg[NOTICE_SEEN_REPORT_CONFIG_KEY] = str(notice_id or "")
    save_app_config(cfg)


def _open_folder(path: str) -> None:
    folder = Path(path).expanduser()
    if folder.is_file():
        folder = folder.parent
    if not folder.exists():
        return
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def _notice_copy_text(notice: dict[str, Any]) -> str:
    workspace = notice.get("workspace") if isinstance(notice.get("workspace"), dict) else {}
    lines = [
        str(notice.get("title") or f"{APP_DISPLAY_NAME} migration complete"),
        f"Data: {notice.get('source') or '(none)'} -> {notice.get('target') or '~/' + DEFAULT_DATA_DIR_NAME}",
        f"Workspace: {workspace.get('headline') or ''}",
        f"Report: {notice.get('report_path') or '(not available)'}",
        "",
        "Next steps:",
    ]
    lines.extend(f"- {step}" for step in notice.get("next_steps", []))
    warnings = notice.get("warnings") or []
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def maybe_show_post_migration_report(*, open_settings: Callable[[str], None] | None = None) -> None:
    notice = should_show_post_migration_notice()
    if not notice:
        return

    def _show() -> None:
        try:
            from nicegui import ui
        except Exception:
            logger.debug("NiceGUI unavailable for post-migration notice", exc_info=True)
            return

        dialog = ui.dialog().props("persistent transition-show=fade transition-hide=fade")
        with dialog:
            with ui.card().classes("w-full q-pa-lg").style(
                "max-width: 46rem; border-radius: 10px;"
            ):
                with ui.row().classes("w-full items-start justify-between gap-3 no-wrap"):
                    with ui.column().classes("gap-1").style("min-width: 0;"):
                        ui.label(str(notice["title"])).classes("text-h5 text-weight-medium")
                        ui.label(
                            "Your old local data was copied into Row-Bot. Nothing was deleted."
                        ).classes("text-grey-5 text-sm")
                    ui.badge("v4", color="blue-grey").props("outline")

                ui.separator()

                with ui.row().classes("w-full gap-2"):
                    for item in notice.get("summary", []):
                        ui.badge(str(item), color="grey").props("outline")

                secret_lines = notice.get("secret_lines") or []
                if secret_lines:
                    ui.label("Credentials").classes("text-subtitle2 q-mt-sm")
                    with ui.column().classes("gap-1"):
                        for line in secret_lines:
                            ui.label(str(line)).classes("text-grey-5 text-sm")

                workspace = notice.get("workspace") if isinstance(notice.get("workspace"), dict) else {}
                ui.label("Workspace").classes("text-subtitle2 q-mt-sm")
                with ui.card().classes("w-full q-pa-md no-shadow").style(
                    "border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; background: rgba(255,255,255,0.03);"
                ):
                    ui.label(str(workspace.get("headline") or "")).classes("text-body2")
                    guidance = str(workspace.get("user_guidance") or "")
                    if guidance:
                        ui.label(guidance).classes("text-grey-5 text-sm")
                    before = str(workspace.get("configured_workspace_before") or "")
                    after = str(workspace.get("configured_workspace_after") or "")
                    if before or after:
                        ui.label(f"Configured workspace: {before or '(default)'} -> {after or '(default)'}").classes(
                            "text-grey-6 text-xs"
                        )

                ui.label("Next Steps").classes("text-subtitle2 q-mt-sm")
                with ui.column().classes("gap-1"):
                    for step in notice.get("next_steps", []):
                        with ui.row().classes("items-start gap-2 no-wrap"):
                            ui.icon("check_circle", size="xs").classes("text-green-4 q-mt-xs")
                            ui.label(str(step)).classes("text-grey-5 text-sm")

                warnings = notice.get("warnings") or []
                if warnings:
                    ui.label("Warnings").classes("text-subtitle2 q-mt-sm")
                    with ui.column().classes("gap-1"):
                        for warning in warnings[:4]:
                            ui.label(str(warning)).classes("text-amber-3 text-sm")

                def _dismiss() -> None:
                    mark_post_migration_notice_seen(str(notice["id"]))
                    dialog.close()

                with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                    if notice.get("report_path"):
                        ui.button(
                            "Open Report Folder",
                            icon="folder_open",
                            on_click=lambda: _open_folder(str(notice.get("report_path") or "")),
                        ).props("flat no-caps")
                    ui.button(
                        "Copy Summary",
                        icon="content_copy",
                        on_click=lambda: (
                            ui.run_javascript(
                                f"navigator.clipboard.writeText({json.dumps(_notice_copy_text(notice))})"
                            ),
                            ui.notify("Migration summary copied", type="positive"),
                        ),
                    ).props("flat no-caps")
                    if open_settings is not None:
                        def _open_workspace_settings() -> None:
                            mark_post_migration_notice_seen(str(notice["id"]))
                            dialog.close()
                            open_settings("System")

                        ui.button(
                            "Workspace Settings",
                            icon="settings",
                            on_click=_open_workspace_settings,
                        ).props("outline no-caps")
                    ui.button("Done", icon="done", on_click=_dismiss).props("color=primary no-caps")

        dialog.open()

    defer_ui(_show, delay=0.45)
