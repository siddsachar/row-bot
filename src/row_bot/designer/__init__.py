"""Row-Bot Designer — AI-powered multi-page design tool.

Create slide decks, one-pagers, marketing material, wireframes, and reports
with live HTML/CSS preview, brand configuration, and export to PDF/PPTX/HTML/PNG.
"""

from row_bot.designer.state import (
    DesignerProject,
    DesignerPage,
    DesignerInteraction,
    BrandConfig,
    ProjectBrief,
    ASPECT_RATIOS,
    DEFAULT_ASPECT_RATIO,
    DESIGNER_MODES,
    DEFAULT_DESIGNER_MODE,
    normalize_designer_mode,
    default_page_kind_for_mode,
)
from row_bot.designer.storage import (
    save_project,
    load_project,
    list_projects,
    delete_project,
    duplicate_project,
)
from row_bot.designer.brand import (
    BRAND_PRESETS,
    get_all_presets,
    save_brand_preset,
    extract_brand_from_url,
)
from row_bot.designer.history import (
    snapshot,
    list_snapshots,
    restore_snapshot,
    UndoStack,
)
from row_bot.designer.briefing import (
    build_initial_design_request,
    project_has_build_brief,
)

__all__ = [
    "DesignerProject",
    "DesignerPage",
    "DesignerInteraction",
    "BrandConfig",
    "ProjectBrief",
    "ASPECT_RATIOS",
    "DEFAULT_ASPECT_RATIO",
    "DESIGNER_MODES",
    "DEFAULT_DESIGNER_MODE",
    "normalize_designer_mode",
    "default_page_kind_for_mode",
    "save_project",
    "load_project",
    "list_projects",
    "delete_project",
    "duplicate_project",
    "BRAND_PRESETS",
    "get_all_presets",
    "save_brand_preset",
    "extract_brand_from_url",
    "snapshot",
    "list_snapshots",
    "restore_snapshot",
    "UndoStack",
    "build_initial_design_request",
    "project_has_build_brief",
]
