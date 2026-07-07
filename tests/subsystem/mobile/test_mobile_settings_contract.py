from __future__ import annotations

from pathlib import Path

from row_bot.ui.mobile_access_settings import _preferred_pairing_origin


def test_system_settings_include_mobile_access_section() -> None:
    settings_src = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")
    mobile_settings_src = Path("src/row_bot/ui/mobile_access_settings.py").read_text(encoding="utf-8")

    assert "build_mobile_access_settings_section" in settings_src
    assert "Mobile Access" in mobile_settings_src
    assert "Create pairing QR" in mobile_settings_src
    assert "generate_qr_png_b64" in mobile_settings_src
    assert "qr_data_uri" not in mobile_settings_src
    assert "store.revoke_device" in mobile_settings_src
    assert "mobile: bool = False" in settings_src
    assert "row-bot-settings-mobile-shell" in settings_src
    assert "data-mobile-settings=true" in settings_src
    assert "ui.splitter(value=18)" in settings_src
    assert "settings-mobile-section-select" in settings_src


def test_mobile_settings_use_mobile_safe_provider_skill_plugin_sections() -> None:
    settings_src = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")
    mobile_settings_src = Path("src/row_bot/ui/mobile_settings.py").read_text(encoding="utf-8")

    assert "build_mobile_providers_settings" in settings_src
    assert "build_mobile_skills_settings" in settings_src
    assert "build_mobile_plugins_settings" in settings_src
    assert "(\"Providers\", \"cloud\", lambda: build_mobile_providers_settings" in settings_src
    assert "(\"Skills\", \"auto_fix_high\", build_mobile_skills_settings)" in settings_src
    assert "(\"Plugins\", \"extension\", build_mobile_plugins_settings)" in settings_src
    assert "row-bot-mobile-provider-card" in mobile_settings_src
    assert "Secret values are never shown" in mobile_settings_src


def test_mobile_settings_keep_marketplaces_desktop_only() -> None:
    mobile_settings_src = Path("src/row_bot/ui/mobile_settings.py").read_text(encoding="utf-8")
    settings_src = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")

    assert "Skills Hub is desktop-only in Mobile V1" in mobile_settings_src
    assert "Plugin Marketplace is desktop-only in Mobile V1" in mobile_settings_src
    assert "open_skills_hub_dialog" not in mobile_settings_src
    assert "open_marketplace_dialog" not in mobile_settings_src
    assert "open_skills_hub_dialog" in settings_src
    assert "open_marketplace_dialog" in settings_src


def test_mobile_settings_keep_local_skill_management_controls() -> None:
    mobile_settings_src = Path("src/row_bot/ui/mobile_settings.py").read_text(encoding="utf-8")

    assert "skills_mod.set_enabled" in mobile_settings_src
    assert "skills_mod.set_pinned" in mobile_settings_src
    assert "ui.switch(\"\", value=is_enabled" in mobile_settings_src
    assert "[\"All\", \"Enabled\", \"Pinned\", \"Custom\", \"Public\"]" in mobile_settings_src
    assert "Mobile keeps local enable, disable, and pin controls." in mobile_settings_src


def test_mobile_settings_keep_local_plugin_enablement_controls() -> None:
    mobile_settings_src = Path("src/row_bot/ui/mobile_settings.py").read_text(encoding="utf-8")

    assert "_can_enable_plugin" in mobile_settings_src
    assert "plugin_state.set_plugin_enabled" in mobile_settings_src
    assert "button_label = \"Disable\" if enabled else \"Enable\"" in mobile_settings_src
    assert "[\"All\", \"Enabled\", \"Disabled\", \"Setup needed\"]" in mobile_settings_src
    assert "Mobile keeps installed plugin enable and disable controls." in mobile_settings_src


def test_pairing_origin_prefers_reachable_remote_candidate() -> None:
    candidates = [
        {
            "access_mode": "localhost",
            "available": True,
            "url": "http://127.0.0.1:8080",
        },
        {
            "access_mode": "lan",
            "available": False,
            "url": "http://192.168.68.87:8080",
        },
        {
            "access_mode": "ngrok",
            "available": True,
            "url": "https://viability-pulverize-proxy.ngrok-free.dev",
        },
    ]

    assert _preferred_pairing_origin(candidates) == "https://viability-pulverize-proxy.ngrok-free.dev"


def test_pairing_origin_falls_back_to_localhost_when_no_remote_is_ready() -> None:
    candidates = [
        {
            "access_mode": "localhost",
            "available": True,
            "url": "http://127.0.0.1:8080",
        },
        {
            "access_mode": "lan",
            "available": False,
            "url": "http://192.168.68.87:8080",
        },
    ]

    assert _preferred_pairing_origin(candidates) == "http://127.0.0.1:8080"
