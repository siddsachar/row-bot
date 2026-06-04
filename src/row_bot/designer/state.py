"""Designer — data models for projects, pages, and brand configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid

from row_bot.brand import APP_BRAND_ACCENT


# ═══════════════════════════════════════════════════════════════════════
# ASPECT RATIOS
# ═══════════════════════════════════════════════════════════════════════

ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "16:9":    (1920, 1080),
    "4:3":     (1024, 768),
    "A4":      (794, 1123),
    "letter":  (816, 1056),
    "1:1":     (1080, 1080),
    "9:16":    (1080, 1920),
    # Interactive-mode viewports. ``landing`` is a tall scrollable page: the
    # stored canvas_height is a sizing hint, not a hard clip — prompt rules
    # tell the agent to use height:auto and let content flow vertically.
    # ``phone`` / ``desktop`` are fixed per-screen viewports used by
    # app_mockup mode; one section == one device screen.
    "landing": (1440, 3200),
    "phone":   (390, 844),
    "desktop": (1440, 900),
}

CANVAS_PRESETS: dict[str, dict[str, str]] = {
    "presentation_widescreen": {
        "label": "Presentation Widescreen",
        "aspect_ratio": "16:9",
        "description": "Standard keynote and pitch deck format.",
    },
    "presentation_standard": {
        "label": "Presentation Standard",
        "aspect_ratio": "4:3",
        "description": "Classic slide layout for older projectors and reports.",
    },
    "square_social": {
        "label": "Square Social",
        "aspect_ratio": "1:1",
        "description": "Instagram, LinkedIn, and feed-friendly social posts.",
    },
    "story_vertical": {
        "label": "Story Vertical",
        "aspect_ratio": "9:16",
        "description": "Stories, reels, and mobile-first vertical canvases.",
    },
    "a4_document": {
        "label": "A4 Document",
        "aspect_ratio": "A4",
        "description": "Printable A4 reports and one-pagers.",
    },
    "letter_document": {
        "label": "Letter Document",
        "aspect_ratio": "letter",
        "description": "US Letter handouts and printable summaries.",
    },
}

DEFAULT_ASPECT_RATIO = "16:9"


# ═══════════════════════════════════════════════════════════════════════
# DESIGNER MODES
# ═══════════════════════════════════════════════════════════════════════

# Taxonomy of designer project modes. Deck/document preserve the legacy
# single-file HTML + PDF/PPTX/PNG export path. Landing/app_mockup switch
# to the interactive site-bundle publish path with the declarative
# ``data-row-bot-*`` runtime bridge. Storyboard is the motion track.
DESIGNER_MODES: dict[str, dict[str, str]] = {
    "deck":       {"label": "Slide deck",         "page_kind": "slide",  "default_aspect": "16:9"},
    "document":   {"label": "Document",            "page_kind": "slide",  "default_aspect": "A4"},
    "landing":    {"label": "Landing page",        "page_kind": "screen", "default_aspect": "landing"},
    "app_mockup": {"label": "App mockup",          "page_kind": "screen", "default_aspect": "phone"},
    "storyboard": {"label": "Motion storyboard",   "page_kind": "shot",   "default_aspect": "16:9"},
}

DEFAULT_DESIGNER_MODE = "deck"


def normalize_designer_mode(mode: Optional[str]) -> str:
    """Return a valid mode string, falling back to the deck default."""
    if isinstance(mode, str) and mode in DESIGNER_MODES:
        return mode
    return DEFAULT_DESIGNER_MODE


def default_page_kind_for_mode(mode: str) -> str:
    """Return the canonical per-page kind for a designer mode."""
    info = DESIGNER_MODES.get(normalize_designer_mode(mode), {})
    return info.get("page_kind", "slide")


def default_aspect_for_mode(mode: str) -> str:
    """Return the recommended aspect-ratio key for a designer mode.

    Used by the setup flow so that picking e.g. "landing" on Blank Canvas
    overrides the template's deck-shaped 16:9 default with a tall,
    scrollable landing viewport instead of cramming a marketing page into
    a 1920×1080 slide.
    """
    info = DESIGNER_MODES.get(normalize_designer_mode(mode), {})
    key = info.get("default_aspect", DEFAULT_ASPECT_RATIO)
    if key not in ASPECT_RATIOS:
        return DEFAULT_ASPECT_RATIO
    return key


# ═══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class BrandConfig:
    """Brand / theme configuration for a designer project."""
    primary_color: str = "#2563EB"
    secondary_color: str = "#1E40AF"
    accent_color: str = APP_BRAND_ACCENT
    bg_color: str = "#0F172A"
    text_color: str = "#F8FAFC"
    heading_font: str = "Inter"
    body_font: str = "Inter"
    logo_b64: Optional[str] = None
    logo_asset_id: str = ""
    logo_mime_type: str = "image/png"
    logo_filename: str = ""
    logo_mode: str = "auto"
    logo_scope: str = "all"
    logo_position: str = "top_right"
    logo_max_height: int = 72
    logo_padding: int = 24

    def to_dict(self) -> dict:
        stored_logo_b64 = None if self.logo_asset_id else self.logo_b64
        return {
            "primary_color": self.primary_color,
            "secondary_color": self.secondary_color,
            "accent_color": self.accent_color,
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "heading_font": self.heading_font,
            "body_font": self.body_font,
            "logo_b64": stored_logo_b64,
            "logo_asset_id": self.logo_asset_id,
            "logo_mime_type": self.logo_mime_type,
            "logo_filename": self.logo_filename,
            "logo_mode": self.logo_mode,
            "logo_scope": self.logo_scope,
            "logo_position": self.logo_position,
            "logo_max_height": self.logo_max_height,
            "logo_padding": self.logo_padding,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BrandConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ProjectBrief:
    """Structured setup brief captured when a designer project is created."""

    output_type: str = ""
    audience: str = ""
    tone: str = ""
    length: str = ""
    build_description: str = ""
    brand_url: str = ""
    brand_preset: str = ""
    reference_notes: str = ""

    def to_dict(self) -> dict:
        return {
            "output_type": self.output_type,
            "audience": self.audience,
            "tone": self.tone,
            "length": self.length,
            "build_description": self.build_description,
            "brand_url": self.brand_url,
            "brand_preset": self.brand_preset,
            "reference_notes": self.reference_notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProjectBrief:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def is_empty(self) -> bool:
        return not any(
            [
                self.output_type.strip(),
                self.audience.strip(),
                self.tone.strip(),
                self.length.strip(),
                self.build_description.strip(),
                self.brand_url.strip(),
                self.brand_preset.strip(),
                self.reference_notes.strip(),
            ]
        )


@dataclass
class DesignerPage:
    """A single page / slide / screen / shot in a designer project."""
    html: str = ""
    title: str = "Untitled"
    notes: str = ""
    thumbnail_b64: Optional[str] = None
    # Stable slug used by interactive modes for routing (e.g. "home",
    # "pricing"). Deck/document pages fall back to synthetic "page-N".
    route_id: str = ""
    # "slide" (deck/document), "screen" (landing/app_mockup), "shot" (storyboard).
    kind: str = "slide"
    # App-mockup modal/drawer state labels (e.g. ["cart-open", "menu-open"]).
    states: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "html": self.html,
            "title": self.title,
            "notes": self.notes,
            "route_id": self.route_id,
            "kind": self.kind,
            "states": list(self.states),
            # thumbnail is transient — not persisted
        }

    @classmethod
    def from_dict(cls, d: dict) -> DesignerPage:
        return cls(
            html=d.get("html", ""),
            title=d.get("title", "Untitled"),
            notes=d.get("notes", ""),
            route_id=d.get("route_id", "") or "",
            kind=d.get("kind", "slide") or "slide",
            states=list(d.get("states", []) or []),
        )


@dataclass
class DesignerAsset:
    """A persistent project asset available to Designer pages and branding.

    ``kind`` covers "image" | "video" | "audio" | "chart" | "logo". The
    video-only fields (``poster_asset_id``, ``duration_ms``, ``autoplay``,
    ``loop``, ``muted``, ``controls``) are ignored for non-video kinds
    but default to safe values so legacy assets round-trip unchanged.
    """

    id: str = field(default_factory=lambda: f"asset-{uuid.uuid4().hex[:8]}")
    kind: str = "image"
    label: str = ""
    mime_type: str = "application/octet-stream"
    stored_name: str = ""
    filename: str = ""
    size_bytes: int = 0
    sha256: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Media-aware fields (video/audio only; defaults are inert for images).
    poster_asset_id: str = ""
    duration_ms: int = 0
    autoplay: bool = False
    loop: bool = False
    muted: bool = True
    controls: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "mime_type": self.mime_type,
            "stored_name": self.stored_name,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "created_at": self.created_at,
            "poster_asset_id": self.poster_asset_id,
            "duration_ms": self.duration_ms,
            "autoplay": self.autoplay,
            "loop": self.loop,
            "muted": self.muted,
            "controls": self.controls,
        }

    def to_summary_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "poster_asset_id": self.poster_asset_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DesignerAsset:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DesignerReference:
    """A persistent project reference file available across designer turns."""

    id: str = field(default_factory=lambda: f"ref-{uuid.uuid4().hex[:8]}")
    name: str = ""
    stored_name: str = ""
    kind: str = "file"
    mime_type: str = ""
    suffix: str = ""
    size_bytes: int = 0
    sha256: str = ""
    summary: str = ""
    content_excerpt: str = ""
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "stored_name": self.stored_name,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "summary": self.summary,
            "content_excerpt": self.content_excerpt,
            "warnings": list(self.warnings),
            "created_at": self.created_at,
        }

    def to_summary_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "summary": self.summary,
            "warning_count": len(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> DesignerReference:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DesignerInteraction:
    """A declarative hotspot / state-toggle / media-play interaction.

    Interactions live on the project (not per-page) so cross-screen links
    survive page reorders. The runtime bridge (``runtime_bridge.js`` —
    Phase 2.2) reads this graph and binds ``data-row-bot-*`` attributes
    accordingly; the agent never writes raw JavaScript.
    """

    id: str = field(default_factory=lambda: f"int-{uuid.uuid4().hex[:8]}")
    source_route: str = ""       # route_id of the source page/screen
    selector: str = ""            # CSS selector or data-asset-id anchor
    event: str = "click"          # "click" | "hover" | "enter"
    action: str = "navigate"      # "navigate" | "toggle_state" | "play_media"
    target: str = ""              # target route_id / state key / asset_id
    transition: str = "fade"      # "fade" | "slide_left" | "slide_up" | "none"
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_route": self.source_route,
            "selector": self.selector,
            "event": self.event,
            "action": self.action,
            "target": self.target,
            "transition": self.transition,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, d: dict) -> DesignerInteraction:
        return cls(
            id=d.get("id") or f"int-{uuid.uuid4().hex[:8]}",
            source_route=d.get("source_route", "") or "",
            selector=d.get("selector", "") or "",
            event=d.get("event", "click") or "click",
            action=d.get("action", "navigate") or "navigate",
            target=d.get("target", "") or "",
            transition=d.get("transition", "fade") or "fade",
            data=dict(d.get("data", {}) or {}),
        )


@dataclass
class DesignerProject:
    """A multi-page design project with brand configuration."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Untitled Project"
    pages: list[DesignerPage] = field(default_factory=lambda: [DesignerPage()])
    active_page: int = 0
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    canvas_width: int = 1920
    canvas_height: int = 1080
    brand: Optional[BrandConfig] = None
    brief: Optional[ProjectBrief] = None
    template_id: Optional[str] = None
    thread_id: Optional[str] = None
    assets: list[DesignerAsset] = field(default_factory=list)
    references: list[DesignerReference] = field(default_factory=list)
    manual_edits: list[str] = field(default_factory=list)
    publish_url: str = ""
    published_at: str = ""
    # Designer mode — drives authoring rules, export path, navigator UI,
    # and prompt block selection. Legacy projects default to "deck".
    mode: str = DEFAULT_DESIGNER_MODE
    # Declarative interaction graph (landing/app_mockup modes only).
    interactions: list[DesignerInteraction] = field(default_factory=list)
    # Bumped when runtime_bridge.js schema breaks back-compat.
    runtime_version: str = "0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if self.aspect_ratio in ASPECT_RATIOS:
            self.canvas_width, self.canvas_height = ASPECT_RATIOS[self.aspect_ratio]
        # Normalize mode + synthesize page route_ids/kinds for legacy
        # projects loaded before Phase 2.1 schema existed.
        self.mode = normalize_designer_mode(self.mode)
        page_kind = default_page_kind_for_mode(self.mode)
        # Route IDs must match templates' data-row-bot-action="navigate:<slug>"
        # targets, so derive from page.title (e.g. "Home" → "home"). Fall
        # back to "page-N" only when the title is unusable. A legacy project
        # whose pages were written with the old "page-N" scheme is left
        # alone — resolving mismatches there would invalidate existing
        # saved interactions.
        # Synthesize deterministic page-N route_ids when missing. We don't
        # slugify the title — legacy projects often have generic titles like
        # "Untitled" or "Old" that would collide or carry no meaning, and
        # interactive templates can declare explicit route_ids on their
        # pages when they need them.
        seen: set[str] = set()
        for idx, page in enumerate(self.pages):
            if not page.route_id:
                slug = f"page-{idx + 1}"
                base = slug
                dedup = 2
                while slug in seen:
                    slug = f"{base}-{dedup}"
                    dedup += 1
                page.route_id = slug
            seen.add(page.route_id)
            if not page.kind:
                page.kind = page_kind

    def touch(self) -> None:
        """Update the ``updated_at`` timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "pages": [p.to_dict() for p in self.pages],
            "active_page": self.active_page,
            "aspect_ratio": self.aspect_ratio,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "brand": self.brand.to_dict() if self.brand else None,
            "brief": self.brief.to_dict() if self.brief else None,
            "template_id": self.template_id,
            "thread_id": self.thread_id,
            "assets": [asset.to_dict() for asset in self.assets],
            "references": [reference.to_dict() for reference in self.references],
            "publish_url": self.publish_url,
            "published_at": self.published_at,
            "mode": self.mode,
            "interactions": [i.to_dict() for i in self.interactions],
            "runtime_version": self.runtime_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DesignerProject:
        brand = BrandConfig.from_dict(d["brand"]) if d.get("brand") else None
        brief = ProjectBrief.from_dict(d["brief"]) if d.get("brief") else None
        pages = [DesignerPage.from_dict(p) for p in d.get("pages", [])]
        assets = [DesignerAsset.from_dict(a) for a in d.get("assets", [])]
        references = [DesignerReference.from_dict(r) for r in d.get("references", [])]
        interactions = [
            DesignerInteraction.from_dict(i)
            for i in (d.get("interactions") or [])
        ]
        if not pages:
            pages = [DesignerPage()]
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Untitled Project"),
            pages=pages,
            active_page=d.get("active_page", 0),
            aspect_ratio=d.get("aspect_ratio", DEFAULT_ASPECT_RATIO),
            canvas_width=d.get("canvas_width", 1920),
            canvas_height=d.get("canvas_height", 1080),
            brand=brand,
            brief=brief,
            template_id=d.get("template_id"),
            thread_id=d.get("thread_id"),
            assets=assets,
            references=references,
            publish_url=d.get("publish_url", ""),
            published_at=d.get("published_at", ""),
            mode=normalize_designer_mode(d.get("mode")),
            interactions=interactions,
            runtime_version=d.get("runtime_version", "0") or "0",
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=d.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )
