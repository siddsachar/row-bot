"""NiceGUI surfaces for Computer Use disclosure, readiness, and local control."""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from typing import Any

from nicegui import run, ui

from row_bot.computer_use.readiness import (
    DISCLOSURE_TEXT,
    ReadinessCode,
    acknowledge_disclosure,
    cancel_disclosure,
    disclosure_acknowledged,
    install_cua_runtime,
    load_cua_manifest,
    readiness,
    run_cua_diagnostics,
    selected_asset,
    mark_cua_observation_verified,
    configure_system_cua,
    verify_system_cua,
    uninstall_cua_runtime,
)
from row_bot.computer_use.service import LeaseOwner, get_computer_use_service


@dataclass(frozen=True)
class ComputerUseSettingsView:
    """Plain-language settings state independent of NiceGUI rendering."""

    title: str
    detail: str
    icon: str
    color: str
    primary_action: str = ""
    needs_install: bool = False
    allow_check: bool = False
    allow_test: bool = False
    show_manage: bool = False


def computer_use_settings_view(
    state: Any,
    *,
    operation: str = "",
) -> ComputerUseSettingsView:
    """Map detailed runtime readiness to a non-technical user-facing state."""

    if operation == "installing":
        return ComputerUseSettingsView(
            "Installing Computer Use…",
            "Row-Bot is downloading and verifying the reviewed component.",
            "downloading",
            "primary",
        )
    if operation == "checking":
        return ComputerUseSettingsView(
            "Checking Computer Use…",
            "Row-Bot is checking local access and the reviewed component.",
            "health_and_safety",
            "primary",
        )
    if operation == "testing":
        return ComputerUseSettingsView(
            "Testing Computer Use…",
            "Calculator will open briefly so Row-Bot can verify local access.",
            "calculate",
            "primary",
        )

    code = getattr(state, "code", None)
    remediation = str(getattr(state, "remediation", "") or "").casefold()
    has_runtime = bool(getattr(state, "executable", ""))
    if code is ReadinessCode.DISABLED:
        return ComputerUseSettingsView(
            "Computer Use is off",
            "Turn it on when you want Row-Bot to work with native apps and OS dialogs.",
            "computer",
            "blue-grey",
        )
    if code is ReadinessCode.DISCLOSURE_REQUIRED:
        return ComputerUseSettingsView(
            "Review required",
            "Review the Cua telemetry notice before setting up Computer Use.",
            "privacy_tip",
            "warning",
        )
    if code is ReadinessCode.UNSUPPORTED:
        return ComputerUseSettingsView(
            "Computer Use is unavailable",
            "This build or device is not supported. Browser remains available for websites.",
            "block",
            "negative",
        )
    if code is ReadinessCode.NOT_INSTALLED:
        return ComputerUseSettingsView(
            "Set up Computer Use",
            "Install the reviewed local component, then check that it can access native apps.",
            "download",
            "warning",
            primary_action="Install Computer Use",
            needs_install=True,
        )
    if code is ReadinessCode.DEGRADED:
        needs_test = "calculator" in remediation or "test" in remediation
        return ComputerUseSettingsView(
            "Finish setting up Computer Use",
            (
                "Run a quick Calculator test to finish setup."
                if needs_test
                else "Check local access before using Computer Use."
            ),
            "build_circle",
            "warning",
            primary_action="Test Computer Use" if needs_test else "Check setup",
            allow_check=True,
            allow_test=needs_test,
            show_manage=has_runtime,
        )
    if code is ReadinessCode.READY:
        return ComputerUseSettingsView(
            "Computer Use is ready",
            "Row-Bot can work with approved native apps in a task-scoped session.",
            "check_circle",
            "positive",
            primary_action="Test Computer Use",
            allow_check=True,
            allow_test=True,
            show_manage=True,
        )
    return ComputerUseSettingsView(
        "Computer Use needs attention",
        "Check the setup for a clear recovery step. You can reinstall it from Manage Computer Use if needed.",
        "error",
        "negative",
        primary_action="Check setup",
        allow_check=True,
        show_manage=has_runtime or code in {
            ReadinessCode.HASH_MISMATCH,
            ReadinessCode.VERSION_MISMATCH,
            ReadinessCode.PERMISSION_MISSING,
            ReadinessCode.FAILED,
        },
    )


def build_active_session_card(*, compact: bool = False) -> Any:
    """Render direct Stop/Take over/Resume controls with ephemeral thumbnail."""

    service = get_computer_use_service()
    card = ui.card().classes("w-full q-pa-sm").style(
        "border: 1px solid rgba(56,189,248,.35); background: rgba(8,47,73,.22);"
    )
    with card:
        container = ui.column().classes("w-full gap-2")

    def _refresh() -> None:
        if getattr(container, "client", None) is None:
            return
        snapshot = service.status_snapshot()
        card.set_visibility(bool(snapshot["active"] or snapshot["paused"]))
        container.clear()
        with container:
            with ui.row().classes("w-full items-center justify-between gap-2"):
                title = f"Computer · {snapshot['app']}" if snapshot["app"] else "Computer Use"
                ui.label(title).classes("text-sm font-bold")
                ui.badge(str(snapshot["state"]).replace("_", " ").title(), color="blue-grey")
            if snapshot["window"]:
                ui.label(str(snapshot["window"])[:120]).classes("text-xs text-grey-5")
            if snapshot["last_action"]:
                ui.label(f"{snapshot['last_action']} · {snapshot['last_effect'] or 'pending verification'}").classes("text-xs")
            image = service.ephemeral_screenshot()
            if image and not compact:
                source = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
                ui.image(source).classes("w-full").style("max-height: 220px; object-fit: contain;")
            with ui.row().classes("items-center gap-2"):
                ui.button("Stop", icon="stop", on_click=service.stop).props("color=negative dense no-caps")
                if snapshot["paused"]:
                    async def _resume() -> None:
                        try:
                            await run.io_bound(service.resume_from_local_ui)
                            ui.notify("Computer session resumed from a fresh capture.", type="positive")
                        except Exception as exc:
                            ui.notify(str(exc), type="negative")
                    ui.button("Resume", icon="play_arrow", on_click=_resume).props("color=positive dense no-caps")
                elif snapshot["active"]:
                    ui.button("Take over", icon="pan_tool", on_click=service.take_over).props("outline dense no-caps")
                if snapshot["action_count"]:
                    ui.label(f"{snapshot['action_count']} verified action(s)").classes("text-xs text-grey-6")

    _refresh()
    ui.timer(0.5, _refresh)
    return card


def build_computer_use_settings_card(tool_registry: Any) -> None:
    """Build a state-first, non-technical Computer Use setup flow."""

    manifest = load_cua_manifest()
    asset = selected_asset()
    tool = tool_registry.get_tool("computer_use")
    if tool is None:
        ui.label("Computer Use is unavailable in this build.").classes("text-grey-6 text-sm")
        return

    with ui.row().classes("w-full items-center justify-between"):
        toggle = ui.switch(
            "Computer Use (Beta)",
            value=tool_registry.is_enabled("computer_use"),
        )
    ui.label(
        "For native apps and OS dialogs. Browser remains separate and is preferred for websites."
    ).classes("text-xs text-grey-5")

    status_container = ui.column().classes("w-full gap-1 q-mt-sm")
    action_container = ui.row().classes("w-full items-center gap-2 q-mt-xs")
    technical_status: Any = None
    operation = {"value": ""}
    manage_section: Any = None
    developer_section: Any = None

    disclosure = ui.dialog().props("persistent")
    with disclosure, ui.card().classes("q-pa-lg").style("width: 680px; max-width: 94vw;"):
        ui.label("Cua Driver telemetry warning").classes("text-h6")
        ui.label(DISCLOSURE_TEXT).classes("text-sm")
        ui.link("Learn more", "https://github.com/trycua/cua/blob/cua-driver-rs-v0.7.1/libs/cua-driver/rust/crates/cua-driver/src/telemetry.rs", new_tab=True)
        with ui.row().classes("w-full justify-end gap-2"):
            def _cancel() -> None:
                cancel_disclosure()
                toggle.value = False
                tool_registry.set_enabled("computer_use", False)
                disclosure.close()
                _refresh_status()

            def _continue() -> None:
                acknowledge_disclosure()
                tool_registry.set_enabled("computer_use", True)
                toggle.value = True
                disclosure.close()
                _refresh_status()

            ui.button("Cancel", on_click=_cancel).props("flat no-caps")
            ui.button("Continue", on_click=_continue).props("color=primary no-caps")

    def _toggle(event: Any) -> None:
        if bool(event.value):
            if not disclosure_acknowledged():
                toggle.value = False
                disclosure.open()
                return
            tool_registry.set_enabled("computer_use", True)
        else:
            tool_registry.set_enabled("computer_use", False)
            get_computer_use_service().stop()
        _refresh_status()

    toggle.on_value_change(_toggle)

    try:
        from row_bot.vision import vision_provider_disclosure

        vision = vision_provider_disclosure()
        vision_summary = (
            "Target-window screenshots may be sent to your selected Vision provider only when visual help is needed."
            if vision["is_cloud"]
            else "Visual help uses your configured local Vision provider only when needed."
        )
    except Exception:
        vision = {"provider_label": "Unavailable", "is_cloud": False}
        vision_summary = "Visual help is currently unavailable."

    privacy_line = ui.row().classes("w-full items-start gap-2 q-mt-xs")
    with privacy_line:
        ui.icon("privacy_tip", size="xs").classes("text-grey-5 q-mt-xs")
        ui.label(vision_summary).classes("text-xs text-grey-5")

    async def _install() -> None:
        operation["value"] = "installing"
        _refresh_status()
        try:
            result = await run.io_bound(install_cua_runtime)
            ui.notify(result.message, type="positive" if result.ok else "negative")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        finally:
            operation["value"] = ""
        _refresh_status()

    async def _diagnostics() -> None:
        operation["value"] = "checking"
        _refresh_status()
        try:
            result = await run.io_bound(run_cua_diagnostics)
            ui.notify(result.message + (f" {result.remediation}" if result.remediation else ""), type="positive" if result.code is ReadinessCode.READY else "warning")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        finally:
            operation["value"] = ""
        _refresh_status()

    async def _calculator_test() -> None:
        service = get_computer_use_service()
        owner = LeaseOwner("settings-calculator", f"ui-{secrets.token_urlsafe(8)}", "settings-calculator")
        operation["value"] = "testing"
        _refresh_status()
        try:
            await run.io_bound(service.acquire, owner, validate_context=False)
            service.grant_app_permission_for_local_ui(owner, "Calculator")
            windows = await run.io_bound(service.launch_app, "Calculator", owner)
            if not windows:
                raise RuntimeError("Calculator launched but no target window was reported.")
            if service.current_observation(windows[0]["target_id"]) is None:
                raise RuntimeError("Calculator opened, but Row-Bot could not verify its window.")
            mark_cua_observation_verified()
            ui.notify("Computer Use is ready.", type="positive")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        finally:
            service.stop()
            operation["value"] = ""
            _refresh_status()

    uninstall_dialog = ui.dialog()
    with uninstall_dialog, ui.card().classes("q-pa-lg"):
        ui.label("Uninstall managed Cua Driver?").classes("text-h6")
        ui.label("This removes only Row-Bot's private Cua runtime. Computer Use will be disabled until it is installed and tested again.").classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=uninstall_dialog.close).props("flat no-caps")
            def _confirm_uninstall() -> None:
                removed = uninstall_cua_runtime()
                tool_registry.set_enabled("computer_use", False)
                toggle.value = False
                uninstall_dialog.close()
                _refresh_status()
                ui.notify(
                    "Computer Use was removed." if removed else "Computer Use was not installed.",
                    type="info",
                )
            ui.button("Remove", on_click=_confirm_uninstall).props("color=negative no-caps")

    technical_section = ui.expansion("Technical details", icon="info").classes("w-full")
    with technical_section:
        ui.label(
            f"Reviewed Cua Driver {manifest['version']} · Cua telemetry notice "
            f"{'accepted' if disclosure_acknowledged() else 'not accepted'}"
        ).classes("text-xs text-grey-6")
        if asset:
            ui.label(f"Artifact: {asset['name']}").classes("text-xs text-grey-6")
            ui.label(f"SHA-256: {asset['sha256']}").classes("text-xs text-grey-6 break-all")
        ui.label(
            f"Vision provider: {vision['provider_label']} "
            f"({'cloud' if vision['is_cloud'] else 'local'})"
        ).classes("text-xs text-grey-6")
        technical_status = ui.column().classes("w-full gap-1")

    manage_section = ui.expansion("Manage Computer Use", icon="settings").classes("w-full")
    with manage_section:
        ui.label(
            "Use these recovery actions only if setup stops working or you no longer want Computer Use."
        ).classes("text-xs text-grey-5")
        with ui.row().classes("items-center gap-2"):
            ui.button("Reinstall", icon="refresh", on_click=_install).props("flat dense no-caps")
            ui.button("Remove", icon="delete", on_click=uninstall_dialog.open).props(
                "flat dense no-caps color=negative"
            )

    developer_section = ui.expansion(
        "Developer options", icon="code"
    ).classes("w-full")
    with developer_section:
        ui.label(
            "Use a separately reviewed Cua executable instead of Row-Bot's managed component."
        ).classes("text-xs text-grey-5")
        system_path = ui.input("Absolute Cua executable path").classes("w-full").props("dense outlined")
        use_system = ui.checkbox("Use this reviewed system binary instead of Row-Bot's managed runtime", value=False)

        async def _verify_system() -> None:
            configure_system_cua(str(system_path.value or ""), enabled=bool(use_system.value))
            result = await run.io_bound(verify_system_cua)
            ui.notify(result.message, type="positive" if result.code is ReadinessCode.READY else "warning")
            _refresh_status()

        ui.button("Verify system Cua", icon="verified", on_click=_verify_system).props("flat dense no-caps")

    def _refresh_status() -> None:
        state = readiness(enabled=tool_registry.is_enabled("computer_use"))
        view = computer_use_settings_view(state, operation=operation["value"])
        status_container.clear()
        with status_container:
            with ui.row().classes("items-center gap-2"):
                ui.icon(view.icon, color=view.color)
                ui.label(view.title).classes("text-sm text-weight-medium")
            ui.label(view.detail).classes("text-xs text-grey-5 q-ml-lg")

        action_container.clear()
        with action_container:
            if not operation["value"] and view.primary_action == "Install Computer Use":
                ui.button(view.primary_action, icon="download", on_click=_install).props(
                    "unelevated dense no-caps color=primary"
                )
            elif not operation["value"] and view.primary_action == "Test Computer Use":
                ui.button(view.primary_action, icon="calculate", on_click=_calculator_test).props(
                    "unelevated dense no-caps color=primary"
                )
                if view.allow_check:
                    ui.button("Check setup", icon="health_and_safety", on_click=_diagnostics).props(
                        "flat dense no-caps"
                    )
            elif not operation["value"] and view.primary_action == "Check setup":
                ui.button(view.primary_action, icon="health_and_safety", on_click=_diagnostics).props(
                    "unelevated dense no-caps color=primary"
                )

        technical_status.clear()
        with technical_status:
            if state.executable:
                ui.label(f"Executable: {state.executable}").classes("text-xs text-grey-6 break-all")
            ui.label(f"Integrity: {state.hash_status or 'not yet verified'}").classes(
                "text-xs text-grey-6"
            )
            if state.remediation:
                ui.label(f"Recovery detail: {state.remediation}").classes("text-xs text-grey-6")

        enabled_and_disclosed = bool(
            tool_registry.is_enabled("computer_use") and disclosure_acknowledged()
        )
        privacy_line.set_visibility(enabled_and_disclosed)
        technical_section.set_visibility(enabled_and_disclosed)
        manage_section.set_visibility(enabled_and_disclosed and view.show_manage)
        developer_section.set_visibility(enabled_and_disclosed)

    _refresh_status()
