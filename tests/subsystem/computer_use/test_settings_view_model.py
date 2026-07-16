from __future__ import annotations

import pytest

from row_bot.computer_use.readiness import CuaReadiness, ReadinessCode
from row_bot.ui.computer_use import computer_use_settings_view


@pytest.mark.parametrize(
    ("state", "title", "primary"),
    [
        (CuaReadiness(ReadinessCode.DISABLED, "internal"), "Computer Use is off", ""),
        (
            CuaReadiness(ReadinessCode.DISCLOSURE_REQUIRED, "internal"),
            "Review required",
            "",
        ),
        (
            CuaReadiness(ReadinessCode.NOT_INSTALLED, "internal"),
            "Set up Computer Use",
            "Install Computer Use",
        ),
        (
            CuaReadiness(
                ReadinessCode.DEGRADED,
                "internal",
                executable="cua.exe",
                remediation="Run diagnostics.",
            ),
            "Finish setting up Computer Use",
            "Check setup",
        ),
        (
            CuaReadiness(
                ReadinessCode.DEGRADED,
                "internal",
                executable="cua.exe",
                remediation="Test with Calculator.",
            ),
            "Finish setting up Computer Use",
            "Test Computer Use",
        ),
        (
            CuaReadiness(ReadinessCode.READY, "internal", executable="cua.exe"),
            "Computer Use is ready",
            "Test Computer Use",
        ),
        (
            CuaReadiness(ReadinessCode.PERMISSION_MISSING, "internal", executable="cua.exe"),
            "Computer Use needs attention",
            "Check setup",
        ),
        (
            CuaReadiness(ReadinessCode.UNSUPPORTED, "internal"),
            "Computer Use is unavailable",
            "",
        ),
    ],
)
def test_settings_view_uses_plain_language_and_contextual_primary_action(
    state: CuaReadiness,
    title: str,
    primary: str,
) -> None:
    view = computer_use_settings_view(state)
    assert view.title == title
    assert view.primary_action == primary
    assert "Cua" not in view.title
    assert "SHA" not in view.detail
    assert "Driver" not in view.detail


@pytest.mark.parametrize(
    ("operation", "title"),
    [
        ("installing", "Installing Computer Use…"),
        ("checking", "Checking Computer Use…"),
        ("testing", "Testing Computer Use…"),
    ],
)
def test_settings_view_has_explicit_busy_states(operation: str, title: str) -> None:
    view = computer_use_settings_view(
        CuaReadiness(ReadinessCode.READY, "internal", executable="cua.exe"),
        operation=operation,
    )
    assert view.title == title
    assert view.primary_action == ""


def test_ready_state_hides_install_and_keeps_manage_recovery() -> None:
    view = computer_use_settings_view(
        CuaReadiness(ReadinessCode.READY, "internal", executable="cua.exe")
    )
    assert not view.needs_install
    assert view.show_manage
    assert view.allow_test
    assert view.allow_check
