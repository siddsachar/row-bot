"""Designer command palette (⌘K).

Provides a fuzzy-filterable picker over designer sub-tools, pages, and
assets. The logic helpers (`build_palette_items`, `filter_items`, `tool_prefill`)
are pure and independently testable; the `open_command_palette` function
renders a NiceGUI dialog that wires picks back to the editor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal


ItemCategory = Literal["tool", "page", "asset"]


@dataclass
class PaletteItem:
    label: str
    category: ItemCategory
    hint: str = ""
    payload: Any = None


# Tool-specific prefill templates. Fall back to _DEFAULT_PREFILL for any
# tool not listed here.
_TOOL_PREFILL: dict[str, str] = {
    "designer_generate_image": "Use designer_generate_image to create an image: ",
    "designer_generate_video": "Use designer_generate_video to create a clip: ",
    "designer_insert_image": "Use designer_insert_image with source=",
    "designer_insert_video": "Use designer_insert_video with source=",
    "designer_add_chart": "Use designer_add_chart with chart_type=bar, data_csv=",
    "designer_insert_component": "Use designer_insert_component with component_name=",
    "designer_set_brand": "Use designer_set_brand to update ",
    "designer_resize_project": "Use designer_resize_project to switch to ",
    "designer_export": "Use designer_export with format=",
    "designer_publish_link": "Use designer_publish_link",
    "designer_generate_notes": "Use designer_generate_notes for the current page",
    "designer_critique_page": "Use designer_critique_page for the current page",
    "designer_apply_repairs": "Use designer_apply_repairs for the current page",
    "designer_refine_text": "Use designer_refine_text with action=",
    "designer_move_image": "Use designer_move_image with image_ref=",
    "designer_replace_image": "Use designer_replace_image with image_ref=",
    "designer_move_element": "Use designer_move_element with selector=",
    "designer_duplicate_element": "Use designer_duplicate_element with selector=",
    "designer_restyle_element": "Use designer_restyle_element with selector=",
    "designer_add_page": "Use designer_add_page to add ",
    "designer_delete_page": "Use designer_delete_page to delete page ",
    "designer_move_page": "Use designer_move_page from_index=",
    "designer_update_page": "Use designer_update_page to rewrite page ",
    "designer_set_pages": "Use designer_set_pages to replace all pages with ",
    "designer_get_project": "Use designer_get_project to summarize the current project",
    "designer_get_page_html": "Use designer_get_page_html for page ",
    "designer_get_reference": "Use designer_get_reference to read reference ",
}
_DEFAULT_PREFILL = "Use {name} to "


def tool_prefill(tool_name: str) -> str:
    """Return a human-friendly prefill for a designer sub-tool name."""
    return _TOOL_PREFILL.get(tool_name, _DEFAULT_PREFILL.format(name=tool_name))


def _humanize_tool(name: str) -> str:
    rest = name[len("designer_"):] if name.startswith("designer_") else name
    return rest.replace("_", " ").strip().capitalize()


def build_palette_items(project, *, tool_names: Iterable[str]) -> list[PaletteItem]:
    """Build the unfiltered palette list from the project + tool names.

    Order: tools → pages → assets (this is also the default sort tie-break).
    """
    items: list[PaletteItem] = []
    for name in tool_names:
        items.append(PaletteItem(
            label=_humanize_tool(name),
            category="tool",
            hint=name,
            payload=name,
        ))

    pages = getattr(project, "pages", None) or []
    for idx, page in enumerate(pages):
        title = (getattr(page, "title", "") or f"Page {idx + 1}").strip()
        items.append(PaletteItem(
            label=f"Go to: {title}",
            category="page",
            hint=f"page {idx + 1}",
            payload=idx,
        ))

    assets = getattr(project, "assets", None) or []
    for asset in assets:
        kind = getattr(asset, "kind", "asset") or "asset"
        label = (getattr(asset, "label", "") or "").strip()
        aid = getattr(asset, "id", "") or ""
        display = label or aid or "asset"
        items.append(PaletteItem(
            label=f"{kind}: {display}",
            category="asset",
            hint=aid,
            payload=aid,
        ))

    return items


_CATEGORY_BIAS = {"tool": 1.20, "page": 1.10, "asset": 1.00}


def _subsequence_score(text: str, query: str) -> float:
    """Return a positive score if query is a subsequence of text, else 0.

    Score = (len(query) / matched_span) so tighter matches rank higher.
    """
    if not query:
        return 1.0
    text = text.lower()
    query = query.lower()
    i = 0
    first = -1
    last = -1
    for j, ch in enumerate(text):
        if i < len(query) and ch == query[i]:
            if first == -1:
                first = j
            last = j
            i += 1
    if i < len(query):
        return 0.0
    span = max(1, last - first + 1)
    return len(query) / span


def filter_items(items: list[PaletteItem], query: str, *, limit: int = 60) -> list[PaletteItem]:
    """Fuzzy-filter palette items by a query string.

    Empty/whitespace queries return items unchanged (truncated to ``limit``).
    Non-empty queries score on subsequence match over label+hint with a
    small category bias (tool > page > asset) to break ties.
    """
    if not query or not query.strip():
        return list(items)[:limit]

    q = query.strip()
    scored: list[tuple[float, int, PaletteItem]] = []
    for idx, item in enumerate(items):
        text = f"{item.label} {item.hint}"
        base = _subsequence_score(text, q)
        if base <= 0:
            continue
        score = base * _CATEGORY_BIAS.get(item.category, 1.0)
        # idx preserved as stable tie-breaker
        scored.append((score, idx, item))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [triple[2] for triple in scored[:limit]]


# ── NiceGUI dialog ───────────────────────────────────────────────────────

def open_command_palette(
    project,
    *,
    tool_names: Iterable[str],
    prefill_input: Callable[[str], None],
    on_navigate_page: Callable[[int], None] | None = None,
) -> None:
    """Open the ⌘K command palette dialog.

    Parameters
    ----------
    project : DesignerProject
    tool_names : iterable of str
        Registered designer sub-tool names.
    prefill_input : callable(str)
        Called when the user picks a tool or asset — should populate the
        chat input with the supplied text and focus it.
    on_navigate_page : callable(int), optional
        Called when the user picks a page — receives the 0-based index.
    """
    from nicegui import ui

    items = build_palette_items(project, tool_names=list(tool_names))

    with ui.dialog() as dlg, ui.card().style(
        "min-width: 520px; max-width: 640px; padding: 12px 14px;"
    ):
        search = ui.input(placeholder="Search tools, pages, assets…").props(
            "autofocus dense outlined clearable"
        ).classes("w-full")

        results_col = ui.column().classes("w-full gap-0").style(
            "max-height: 360px; overflow-y: auto; margin-top: 8px;"
        )

        def _pick(item: PaletteItem) -> None:
            dlg.close()
            if item.category == "tool":
                prefill_input(tool_prefill(str(item.payload)))
            elif item.category == "page" and on_navigate_page is not None:
                try:
                    on_navigate_page(int(item.payload))
                except Exception:
                    pass
            elif item.category == "asset":
                aid = str(item.payload)
                prefill_input(f"Reuse asset {aid} on the current page: ")

        def _render(filtered: list[PaletteItem]) -> None:
            results_col.clear()
            with results_col:
                if not filtered:
                    ui.label("No matches.").classes("text-grey-5 text-sm q-pa-sm")
                    return
                for item in filtered:
                    row = ui.row().classes(
                        "w-full items-center justify-between no-wrap cursor-pointer"
                    ).style("padding: 6px 10px; border-radius: 6px;")
                    row.on("mouseenter", lambda r=row: r.style("background:#f4f5f7"))
                    row.on("mouseleave", lambda r=row: r.style("background:transparent"))
                    row.on("click", lambda _e, it=item: _pick(it))
                    with row:
                        with ui.column().classes("gap-0"):
                            ui.label(item.label).classes("text-sm text-weight-medium")
                            if item.hint:
                                ui.label(item.hint).classes("text-xs text-grey-6")
                        ui.label(item.category).classes("text-xs text-grey-5")

        def _on_change(_e=None) -> None:
            _render(filter_items(items, search.value or ""))

        search.on("update:model-value", _on_change)
        search.on("keydown.enter", lambda _e: _pick_first())

        def _pick_first() -> None:
            filtered = filter_items(items, search.value or "")
            if filtered:
                _pick(filtered[0])

        _render(items)
        dlg.open()
