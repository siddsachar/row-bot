"""Designer setup-flow helpers.

Pure helpers used by the New Design dialog so creation logic can be tested
without relying on UI interactions.
"""

from __future__ import annotations

from row_bot.designer.brand import get_all_presets
from row_bot.designer.briefing import build_initial_design_request, project_has_build_brief
from row_bot.designer.state import (
    ASPECT_RATIOS,
    BrandConfig,
    DESIGNER_MODES,
    DEFAULT_DESIGNER_MODE,
    DesignerPage,
    DesignerProject,
    ProjectBrief,
    default_aspect_for_mode,
    default_page_kind_for_mode,
    normalize_designer_mode,
)
from row_bot.designer.templates import get_template


DEFAULT_PROJECT_NAME = "Untitled Design"
_INFERRED_OUTPUT_TYPES = {
    "pitch_deck": "pitch deck",
    "status_report": "status report",
    "marketing_one_pager": "marketing one-pager",
    "product_launch": "product launch presentation",
    "social_media": "social media set",
    "wireframe_kit": "wireframe kit",
}

# Phase 2.3.E — Canvas control shown in the setup dialog is scoped to
# the selected mode so users don't get irrelevant options (e.g.
# picking "A4" for a landing page). Returned list is
# [(aspect_key, human_label), ...]. All keys are guaranteed to live
# in ``ASPECT_RATIOS``.
_CANVAS_CHOICES_BY_MODE: dict[str, list[tuple[str, str]]] = {
    "deck": [
        ("16:9", "16:9 · Widescreen slide (1920×1080)"),
        ("4:3",  "4:3 · Standard slide (1440×1080)"),
        ("1:1",  "1:1 · Square (1080×1080)"),
        ("9:16", "9:16 · Vertical (1080×1920)"),
    ],
    "document": [
        ("A4",     "A4 · Portrait (794×1123)"),
        ("letter", "Letter · US portrait (816×1056)"),
    ],
    "landing": [
        ("landing", "Standard · 1440px wide"),
    ],
    "app_mockup": [
        ("phone",   "Phone · 390×844"),
        ("desktop", "Desktop · 1440×900"),
    ],
    "storyboard": [
        ("16:9", "16:9 · Widescreen frame"),
        ("9:16", "9:16 · Vertical frame"),
        ("1:1",  "1:1 · Square frame"),
    ],
}


def canvas_choices_for_mode(mode: str) -> list[tuple[str, str]]:
    """Return the list of ``(aspect_key, label)`` options shown in the
    setup dialog's Canvas control for the given mode.

    Unknown modes fall back to the deck options so the UI never ends
    up empty. Every returned key is present in
    :data:`designer.state.ASPECT_RATIOS`.
    """

    key = (mode or "").strip().lower()
    return list(_CANVAS_CHOICES_BY_MODE.get(key, _CANVAS_CHOICES_BY_MODE["deck"]))

# UI-facing choices for the mode picker. The first entry means
# "infer the mode from the selected output type".
MODE_CHOICE_AUTO = "auto"
DESIGNER_MODE_CHOICES: list[tuple[str, str]] = [
    (MODE_CHOICE_AUTO, "Auto-detect from output type"),
    ("deck", DESIGNER_MODES["deck"]["label"]),
    ("document", DESIGNER_MODES["document"]["label"]),
    ("landing", DESIGNER_MODES["landing"]["label"]),
    ("app_mockup", DESIGNER_MODES["app_mockup"]["label"]),
    ("storyboard", DESIGNER_MODES["storyboard"]["label"]),
]

# Phase 2.3.C — the setup dialog asks the user to pick a mode
# explicitly; "auto" is still callable programmatically but never
# offered in the UI picker. Consumers that render the picker should
# use this list instead of ``DESIGNER_MODE_CHOICES``.
DESIGNER_MODE_PICKER_CHOICES: list[tuple[str, str]] = [
    (key, label) for (key, label) in DESIGNER_MODE_CHOICES
    if key != MODE_CHOICE_AUTO
]


def infer_mode_from_output_type(output_type: str, *, template_id: str = "") -> str:
    """Best-effort inference of a designer mode from the brief's output type
    and/or the chosen template id. Returns a valid mode key from
    ``DESIGNER_MODES`` — defaults to ``DEFAULT_DESIGNER_MODE`` when nothing
    matches."""

    haystack = f"{output_type or ''} {template_id or ''}".lower()
    if not haystack.strip():
        return DEFAULT_DESIGNER_MODE
    if any(tok in haystack for tok in ("storyboard", "motion", "animation")):
        return "storyboard"
    if any(tok in haystack for tok in ("app mock", "app_mockup", "app-mockup", "wireframe", "prototype", "ui mock")):
        return "app_mockup"
    if any(tok in haystack for tok in ("landing", "one-pager", "one pager", "microsite")):
        return "landing"
    if any(tok in haystack for tok in ("document", "report", "whitepaper", "memo")):
        return "document"
    return DEFAULT_DESIGNER_MODE


def resolve_project_mode(
    mode: str,
    *,
    brief: ProjectBrief | None,
    template_id: str,
) -> str:
    """Resolve the effective mode from the setup dialog's ``mode`` control.

    Priority:
      1. Explicit, valid ``mode`` arg wins (non-"auto").
      2. Selected template's ``mode`` field (Phase 2.3.A).
      3. Keyword inference from ``brief.output_type`` / ``template_id``.
    """

    candidate = (mode or "").strip().lower() or MODE_CHOICE_AUTO
    if candidate != MODE_CHOICE_AUTO and candidate in DESIGNER_MODES:
        return normalize_designer_mode(candidate)

    # Fall through: prefer the template's declared mode over keyword
    # inference. This removes the dependence on the legacy
    # ``output_type`` free-text field.
    tmpl = get_template(template_id) if template_id else None
    if tmpl and getattr(tmpl, "mode", "") and tmpl.mode in DESIGNER_MODES:
        return normalize_designer_mode(tmpl.mode)

    output_type = (brief.output_type if brief else "") or ""
    return infer_mode_from_output_type(output_type, template_id=template_id)


def default_project_name_for_template(template_id: str) -> str:
    """Return the default name shown for a selected template."""

    tmpl = get_template(template_id) or get_template("blank_canvas")
    # Blank starters (2.3.B) keep the generic "Untitled Design" label so
    # the name input doesn't get pre-filled with e.g. "Blank Landing".
    if tmpl and not tmpl.id.startswith("blank_"):
        return tmpl.name
    return DEFAULT_PROJECT_NAME


def infer_output_type_for_template(template_id: str) -> str:
    """Return the implied output type for non-blank templates."""

    tmpl = get_template(template_id) or get_template("blank_canvas")
    if tmpl is None or tmpl.id.startswith("blank_"):
        return ""
    return _INFERRED_OUTPUT_TYPES.get(tmpl.id, tmpl.name.lower())


def resolve_project_brand(
    *,
    preset_name: str = "",
    extracted_brand: BrandConfig | None = None,
) -> BrandConfig:
    """Return the effective setup-time brand.

    URL-extracted brand wins over preset selection.
    """

    if extracted_brand is not None:
        return BrandConfig.from_dict(extracted_brand.to_dict())

    presets = get_all_presets()
    if preset_name and preset_name in presets:
        return BrandConfig.from_dict(presets[preset_name].to_dict())

    return BrandConfig()


def create_project_from_setup(
    template_id: str,
    *,
    aspect_ratio: str = "",
    project_name: str = "",
    brief: ProjectBrief | None = None,
    preset_name: str = "",
    extracted_brand: BrandConfig | None = None,
    mode: str = MODE_CHOICE_AUTO,
) -> DesignerProject:
    """Create a designer project from setup-dialog selections."""

    tmpl = get_template(template_id) or get_template("blank_canvas")
    if tmpl is None:
        raise ValueError("No template available for project creation.")

    resolved_mode = resolve_project_mode(mode, brief=brief, template_id=tmpl.id)

    # Aspect resolution order:
    #   1. explicit aspect_ratio arg from the caller (user override in UI)
    #   2. the resolved mode's default (so picking Landing on Blank Canvas
    #      lands on a tall landing viewport, not a 16:9 slide)
    #   3. the template's own aspect_ratio (legacy fallback)
    if aspect_ratio:
        ratio = aspect_ratio
    else:
        mode_default = default_aspect_for_mode(resolved_mode)
        tmpl_ratio = tmpl.aspect_ratio
        # If the template matches the resolved mode (e.g. picking a
        # landing-shaped template and landing mode), honour the template.
        # Otherwise the mode default wins so the canvas fits real-world
        # use of that output type.
        if tmpl_ratio and tmpl_ratio == mode_default:
            ratio = tmpl_ratio
        elif tmpl.id.startswith("blank_"):
            # All blank starters defer to the mode default so picking a
            # different mode after choosing a blank starter rewrites the
            # canvas to match (e.g. blank_deck + landing → landing).
            ratio = mode_default
        else:
            ratio = tmpl_ratio or mode_default
    canvas_width, canvas_height = ASPECT_RATIOS.get(ratio, (1920, 1080))

    page_kind = default_page_kind_for_mode(resolved_mode)
    pages = [
        DesignerPage(
            html=p["html"], title=p["title"], notes=p.get("notes", ""),
            kind=page_kind,
        )
        for p in tmpl.pages
    ]

    brief_value = None
    if brief is not None and not brief.is_empty():
        brief_value = ProjectBrief.from_dict(brief.to_dict())

    name = project_name.strip() if project_name else ""
    if not name:
        name = default_project_name_for_template(tmpl.id)

    return DesignerProject(
        name=name,
        pages=pages,
        aspect_ratio=ratio,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        brand=resolve_project_brand(preset_name=preset_name, extracted_brand=extracted_brand),
        brief=brief_value,
        template_id=tmpl.id,
        mode=resolved_mode,
    )


def prepare_project_creation(
    template_id: str,
    *,
    aspect_ratio: str = "",
    project_name: str = "",
    brief: ProjectBrief | None = None,
    preset_name: str = "",
    extracted_brand: BrandConfig | None = None,
    auto_build: bool = False,
    mode: str = MODE_CHOICE_AUTO,
) -> tuple[DesignerProject, str | None]:
    """Return the newly created project plus an optional initial build prompt."""

    project = create_project_from_setup(
        template_id,
        aspect_ratio=aspect_ratio,
        project_name=project_name,
        brief=brief,
        preset_name=preset_name,
        extracted_brand=extracted_brand,
        mode=mode,
    )
    initial_prompt = None
    if auto_build and project_has_build_brief(project):
        initial_prompt = build_initial_design_request(project)
    return project, initial_prompt