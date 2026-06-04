"""Zero-state sidebar quick actions (Phase 2.1.I).

When a designer project has no chat history and no build brief yet, the
sidebar shows a small set of per-mode "quick start" actions. Each action
is a pre-filled agent message — one click sends the prompt and the normal
designer agent flow takes over.

Logic here is pure (no NiceGUI) so the editor UI can wire it up and tests
can exercise mode routing + prompt content directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from row_bot.designer.state import normalize_designer_mode


@dataclass(frozen=True)
class QuickAction:
    label: str
    icon: str
    prompt: str


_ACTIONS_BY_MODE: dict[str, list[QuickAction]] = {
    "deck": [
        QuickAction(
            label="Draft 3 slides from a brief",
            icon="auto_stories",
            prompt=(
                "Draft a 3-slide deck outline based on this brief: "
                "<describe the topic, audience, and desired takeaway>. "
                "Use my brand colors and fonts."
            ),
        ),
        QuickAction(
            label="Create an on-brand title slide",
            icon="title",
            prompt=(
                "Create a single title slide that matches my brand. "
                "Use a short headline, supporting subtitle, and leave "
                "space for a logo in the configured corner."
            ),
        ),
        QuickAction(
            label="Generate an on-brand hero image",
            icon="image",
            prompt=(
                "Use designer_generate_image to create an on-brand hero "
                "image for the current slide. Keep composition balanced "
                "with space for a headline."
            ),
        ),
    ],
    "document": [
        QuickAction(
            label="Outline a 1-page brief",
            icon="article",
            prompt=(
                "Draft a one-page document with headline, three key "
                "points, and a short CTA section. Use my brand colors "
                "and typography."
            ),
        ),
        QuickAction(
            label="Add a summary banner",
            icon="view_day",
            prompt=(
                "Insert a summary banner at the top of the current page "
                "using the brand primary color and a two-line headline."
            ),
        ),
        QuickAction(
            label="Generate a header illustration",
            icon="palette",
            prompt=(
                "Use designer_generate_image to create a subtle header "
                "illustration that fits my brand tone."
            ),
        ),
    ],
    "landing": [
        QuickAction(
            label="Scaffold a landing page",
            icon="web",
            prompt=(
                "Scaffold a single landing page with hero, 3 feature "
                "cards, a testimonial, and a CTA section. Use my brand."
            ),
        ),
        QuickAction(
            label="Add a pricing section",
            icon="sell",
            prompt=(
                "Insert a pricing component with three tiers and a clear "
                "primary CTA on the middle tier."
            ),
        ),
        QuickAction(
            label="Generate a hero background",
            icon="wallpaper",
            prompt=(
                "Use designer_generate_image to create an on-brand hero "
                "background appropriate for a landing page."
            ),
        ),
    ],
    "app_mockup": [
        QuickAction(
            label="Scaffold a 3-screen flow",
            icon="phone_iphone",
            prompt=(
                "Scaffold 3 connected app screens: sign-in, home, and "
                "detail view. Keep navigation consistent across screens."
            ),
        ),
        QuickAction(
            label="Add a tab bar",
            icon="view_carousel",
            prompt=(
                "Insert a bottom tab bar with Home, Search, and Profile "
                "icons on every screen."
            ),
        ),
        QuickAction(
            label="Generate an on-brand app icon",
            icon="apps",
            prompt=(
                "Use designer_generate_image to create a square app icon "
                "that fits my brand."
            ),
        ),
    ],
    "storyboard": [
        QuickAction(
            label="Draft a 3-shot storyboard",
            icon="movie",
            prompt=(
                "Draft a 3-shot storyboard (6 seconds each). For each "
                "shot, write prompt + duration + aspect; keep continuity "
                "between shots and hold on the logo at the end."
            ),
        ),
        QuickAction(
            label="Animate the current image",
            icon="animation",
            prompt=(
                "Use designer_generate_video with image_source=\"last\" "
                "to animate the current image with subtle brand-safe "
                "motion."
            ),
        ),
        QuickAction(
            label="Generate an opening clip",
            icon="play_circle",
            prompt=(
                "Use designer_generate_video to create a 6-second "
                "opening clip with on-brand motion and an end-frame "
                "hold on the logo."
            ),
        ),
    ],
}


def get_quick_actions(project) -> list[QuickAction]:
    """Return quick actions appropriate for ``project.mode``.

    Falls back to deck actions for unknown modes (or missing project).
    """
    mode_raw = getattr(project, "mode", None) if project is not None else None
    mode = normalize_designer_mode(mode_raw)
    return list(_ACTIONS_BY_MODE.get(mode, _ACTIONS_BY_MODE["deck"]))


def _page_is_trivial(html: str) -> bool:
    """Heuristic: treat a page as empty if its HTML has very little text
    and no embedded assets. Placeholder pages from project creation are
    usually a wrapper ``<div>`` with a short label or nothing."""
    if not html:
        return True
    stripped = html.strip()
    low = stripped.lower()
    if "<img" in low or "<video" in low or "<canvas" in low:
        return False
    if len(stripped) < 60:
        return True
    # Count visible-ish text length (rough proxy): strip tags.
    import re as _re
    text = _re.sub(r"<[^>]+>", " ", stripped)
    text = _re.sub(r"\s+", " ", text).strip()
    return len(text) < 32


def is_project_empty(project, messages: Iterable | None = None) -> bool:
    """Return True if the project has no chat history and no meaningful
    content yet."""
    if project is None:
        return True
    if messages:
        # Any truthy message counts as non-empty history.
        try:
            for m in messages:
                if m:
                    return False
        except TypeError:
            pass
    pages = getattr(project, "pages", None) or []
    if not pages:
        return True
    for page in pages:
        html = getattr(page, "html", "") or ""
        if not _page_is_trivial(html):
            return False
    assets = getattr(project, "assets", None) or []
    if assets:
        return False
    return True
