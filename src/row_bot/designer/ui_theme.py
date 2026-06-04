"""Shared Designer UI styling helpers for modal surfaces and CTAs."""

from __future__ import annotations

from row_bot.brand import APP_BRAND_ACCENT, APP_BRAND_ACCENT_RGB


DIALOG_CARD_BASE_STYLE = (
    "background:"
    f"radial-gradient(circle at top left, rgba({APP_BRAND_ACCENT_RGB},0.16), transparent 30%),"
    f"radial-gradient(circle at top right, rgba({APP_BRAND_ACCENT_RGB},0.14), transparent 34%),"
    "linear-gradient(180deg, rgba(15,23,42,0.985), rgba(2,6,23,0.98));"
    "border: 1px solid rgba(148,163,184,0.16);"
    "border-radius: 28px;"
    "box-shadow: 0 30px 90px rgba(2,6,23,0.48);"
    "backdrop-filter: blur(20px);"
)

SECTION_PANEL_STYLE = (
    "background: rgba(15,23,42,0.46);"
    "border: 1px solid rgba(148,163,184,0.12);"
    "border-radius: 22px;"
    "box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);"
)

SOFT_PANEL_STYLE = (
    "background: rgba(15,23,42,0.38);"
    "border: 1px solid rgba(148,163,184,0.12);"
    "border-radius: 18px;"
)

SECTION_LABEL_CLASSES = "text-caption text-weight-bold"
SECTION_LABEL_STYLE = "letter-spacing: 0.12em; text-transform: uppercase; color: #94a3b8;"


def dialog_card_style(
    *,
    min_width: str = "",
    max_width: str = "",
    max_height: str = "",
    width: str = "",
    height: str = "",
    padding: str = "24px",
) -> str:
    """Return a consistent dialog-card style string for Designer surfaces."""

    parts = [DIALOG_CARD_BASE_STYLE, f"padding: {padding};"]
    if min_width:
        parts.append(f"min-width: {min_width};")
    if max_width:
        parts.append(f"max-width: {max_width};")
    if max_height:
        parts.append(f"max-height: {max_height};")
    if width:
        parts.append(f"width: {width};")
    if height:
        parts.append(f"height: {height};")
    return "".join(parts)


def surface_style(*, padding: str = "16px", strong: bool = False) -> str:
    """Return a translucent surface style for nested cards and sections."""

    base = SECTION_PANEL_STYLE if strong else SOFT_PANEL_STYLE
    return f"{base} padding: {padding};"


def style_primary_button(button, *, compact: bool = False, round: bool = False):
    """Apply the primary brand CTA treatment."""

    button.props("unelevated no-caps")
    size_style = (
        "min-width: 40px; width: 40px; height: 40px; padding: 0; border-radius: 999px;"
        if round
        else (
            "min-height: 36px; padding: 0 14px; border-radius: 12px;"
            if compact
            else "min-height: 42px; padding: 0 18px; border-radius: 14px;"
        )
    )
    button.style(
        f"background: linear-gradient(180deg, #5D82A8, {APP_BRAND_ACCENT});"
        "color: #ffffff;"
        "font-weight: 700;"
        "letter-spacing: 0.01em;"
        f"box-shadow: 0 14px 28px rgba({APP_BRAND_ACCENT_RGB},0.24);"
        + size_style
    )
    return button


def style_secondary_button(button, *, compact: bool = False):
    """Apply a high-contrast secondary button treatment."""

    button.props("outline no-caps")
    button.style(
        "background: rgba(15,23,42,0.5);"
        "color: #e2e8f0;"
        "border: 1px solid rgba(148,163,184,0.24);"
        f"{'min-height: 36px; padding: 0 14px;' if compact else 'min-height: 42px; padding: 0 18px;'}"
        "border-radius: 14px;"
        "font-weight: 600;"
    )
    return button


def style_ghost_button(button, *, compact: bool = False):
    """Apply a subtle tertiary button treatment."""

    button.props("flat no-caps")
    button.style(
        "color: #cbd5e1;"
        f"{'min-height: 34px; padding: 0 10px;' if compact else 'min-height: 38px; padding: 0 12px;'}"
        "border-radius: 12px;"
        "font-weight: 600;"
    )
    return button


def style_destructive_button(button, *, compact: bool = False):
    """Apply a readable destructive action treatment."""

    button.props("unelevated no-caps")
    button.style(
        "background: rgba(239,68,68,0.14);"
        "color: #fecaca;"
        "border: 1px solid rgba(239,68,68,0.34);"
        f"{'min-height: 36px; padding: 0 14px;' if compact else 'min-height: 42px; padding: 0 18px;'}"
        "border-radius: 12px;"
        "font-weight: 600;"
    )
    return button


def style_choice_button(button, *, active: bool):
    """Apply a pill-like filter button treatment used in template/category pickers."""

    button.props("no-caps")
    if active:
        button.style(
            f"background: linear-gradient(180deg, #5D82A8, {APP_BRAND_ACCENT});"
            "color: #ffffff;"
            "border-radius: 999px;"
            "min-height: 36px; padding: 0 14px;"
            "font-weight: 700;"
            f"box-shadow: 0 10px 24px rgba({APP_BRAND_ACCENT_RGB},0.24);"
        )
    else:
        button.style(
            "background: rgba(15,23,42,0.42);"
            "color: #cbd5e1;"
            "border: 1px solid rgba(148,163,184,0.18);"
            "border-radius: 999px;"
            "min-height: 36px; padding: 0 14px;"
            "font-weight: 600;"
        )
    return button
