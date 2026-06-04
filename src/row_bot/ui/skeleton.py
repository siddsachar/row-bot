"""Lightweight skeleton placeholders shown while a heavy view hydrates.

These are plain HTML/CSS — no I/O, no project loading — so they paint in
<5 ms and give the user instant visual feedback while the real view
builds in the next event-loop tick.
"""
from __future__ import annotations

from nicegui import ui

_SHIMMER_CSS = """
<style>
@keyframes row-bot-skel-shimmer {
  0%   { background-position: -400px 0; }
  100% { background-position: 400px 0; }
}
.row-bot-skel {
  background: linear-gradient(90deg,
      rgba(255,255,255,0.04) 0%,
      rgba(255,255,255,0.10) 50%,
      rgba(255,255,255,0.04) 100%);
  background-size: 800px 100%;
  animation: row-bot-skel-shimmer 1.4s linear infinite;
  border-radius: 6px;
}
.row-bot-skel-card {
  padding: 0.75rem;
  border-radius: 8px;
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(255,255,255,0.06);
}
</style>
"""

_css_injected = False


def _ensure_css() -> None:
    global _css_injected
    if _css_injected:
        return
    ui.add_head_html(_SHIMMER_CSS)
    _css_injected = True


def show_gallery_skeleton(n: int = 6) -> None:
    """Render N placeholder cards for the Designer gallery grid."""
    _ensure_css()
    with ui.column().classes("w-full q-pa-sm gap-0"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.element("div").classes("row-bot-skel").style(
                "width: 180px; height: 28px;"
            )
            ui.element("div").classes("row-bot-skel").style(
                "width: 110px; height: 32px;"
            )
        ui.separator().classes("q-my-sm")
        with ui.element("div").classes("w-full").style(
            "display: grid;"
            "grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));"
            "gap: 0.75rem;"
        ):
            for _ in range(n):
                with ui.element("div").classes("row-bot-skel-card"):
                    ui.element("div").classes("row-bot-skel").style(
                        "width: 100%; height: 80px; margin-bottom: 8px;"
                    )
                    ui.element("div").classes("row-bot-skel").style(
                        "width: 70%; height: 14px; margin: 6px auto;"
                    )
                    ui.element("div").classes("row-bot-skel").style(
                        "width: 50%; height: 10px; margin: 4px auto;"
                    )


def show_chat_skeleton(n: int = 4) -> None:
    """Render N placeholder message rows for the chat view."""
    _ensure_css()
    with ui.column().classes("w-full gap-3 q-pa-md"):
        for i in range(n):
            mine = (i % 2 == 1)
            with ui.row().classes("w-full items-start gap-2").style(
                "flex-direction: row-reverse;" if mine else ""
            ):
                ui.element("div").classes("row-bot-skel").style(
                    "width: 32px; height: 32px; border-radius: 50%; flex: none;"
                )
                with ui.column().classes("gap-1").style(
                    "max-width: 70%; flex: 1;"
                ):
                    ui.element("div").classes("row-bot-skel").style(
                        "width: 120px; height: 12px;"
                    )
                    ui.element("div").classes("row-bot-skel").style(
                        f"width: {60 + (i * 7) % 30}%; height: 14px;"
                    )
                    if i % 2 == 0:
                        ui.element("div").classes("row-bot-skel").style(
                            "width: 45%; height: 14px;"
                        )


def show_home_skeleton() -> None:
    """Generic home-view skeleton (header + two content blocks)."""
    _ensure_css()
    with ui.column().classes("w-full q-pa-md gap-3"):
        ui.element("div").classes("row-bot-skel").style(
            "width: 200px; height: 32px;"
        )
        ui.element("div").classes("row-bot-skel").style(
            "width: 100%; height: 120px;"
        )
        ui.element("div").classes("row-bot-skel").style(
            "width: 100%; height: 200px;"
        )


def show_generic_skeleton() -> None:
    """Tiny spinner fallback for any view we don't have a tailored skeleton for."""
    _ensure_css()
    with ui.column().classes("w-full h-full items-center justify-center q-pa-lg"):
        ui.spinner("dots", size="2rem", color="grey-6")
