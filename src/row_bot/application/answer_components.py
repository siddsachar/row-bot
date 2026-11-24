"""Answer components: small, fixed, validated blocks inside a reply (F3).

A reply may hold a fenced block such as ```rowbot-table whose body is JSON.
Each tag has one strict schema. A body that isn't JSON, or doesn't fit its
schema or its limits, stays an ordinary code block: nothing is lost and
nothing runs. Text is reduced to plain text (no HTML, links or images), so a
reply quoting a web page can't put markup or remote content on screen.

Reply buttons (``actions``, and the ``rowbot-choices`` tag) carry only words:
a tap sends them as the person's own message through the normal send path.
A reply that starts with "/" is refused, so a button can never run a slash command.
"""
from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator


MAX_COMPONENT_CHARS = 128 * 1024
MAX_COMPONENT_TEXT_CHARS = 256 * 1024
# An inline visual's HTML/SVG; it only ever runs in the client's sandboxed frame, with no network.
MAX_VISUAL_CHARS = 200 * 1024
# "ui" is a surface: A2UI 1.0 drawn with Row-Bot's components (application/surfaces.py).
TAGS = ("table", "chart", "checklist", "figures", "progress", "choices", "visual", "ui")
_VISUAL_TITLE_RE = re.compile(r"<title[^>]*>([\s\S]{0,2000}?)</title>", re.IGNORECASE)
FENCE_RE = re.compile(
    r"```rowbot-(" + "|".join(TAGS) + r")[ \t]*\n([\s\S]*?)\n```"
)
_CURRENCY = frozenset("£$€¥₹₩₽₺₪")

_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")
_TAG_RE = re.compile(r"<[^>\n]*>")
_MARK_RE = re.compile(r"\*\*|__|~~|`")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def plain_text(value: str) -> str:
    """One line of plain text: images, links, HTML tags and emphasis markers removed."""

    text = _IMAGE_RE.sub(r"\1", value)
    text = _LINK_RE.sub(r"\1", text)
    text = _TAG_RE.sub("", text)
    text = _MARK_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return " ".join(text.split())


Name = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=40)]
Reply = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Line = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Title = Annotated[str, StringConstraints(max_length=200)]
Note = Annotated[str, StringConstraints(max_length=500)]
Unit = Annotated[str, StringConstraints(max_length=12)]
Delta = Annotated[str, StringConstraints(max_length=60)]
Shown = Annotated[str, StringConstraints(min_length=1, max_length=40)]
Key = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Point = Annotated[str, StringConstraints(max_length=100)]
Number = Annotated[float, Field(allow_inf_nan=False)]
Cell = str | bool | int | float | None


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Action(_Body):
    label: Label
    reply: Reply


class _Component(_Body):
    title: Title | None = None
    actions: list[Action] = Field(default_factory=list, max_length=4)


class Column(_Body):
    key: Key
    label: Line | None = None
    type: Literal["text", "number", "currency", "percent", "date"] = "text"


class TableBody(_Component):
    columns: list[Column] = Field(min_length=1, max_length=12)
    rows: list[dict[str, Cell]] = Field(min_length=1, max_length=200)
    note: Note | None = None

    @model_validator(mode="after")
    def _fits(self) -> TableBody:
        keys = [column.key for column in self.columns]
        if len(set(keys)) != len(keys):
            raise ValueError("column keys repeat")
        for row in self.rows:
            if not set(row) <= set(keys):
                raise ValueError("a row has a key no column names")
            for value in row.values():
                if isinstance(value, str) and len(value) > 200:
                    raise ValueError("a cell is too long")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("a cell is not a number")
        return self


class Series(_Body):
    name: Name
    values: list[Number | None] = Field(min_length=1, max_length=200)


class ChartBody(_Component):
    kind: Literal["bar", "line"]
    x: list[Point | int | float] = Field(min_length=1, max_length=200)
    series: list[Series] = Field(min_length=1, max_length=6)
    x_label: Point | None = None
    y_label: Point | None = None
    unit: Unit | None = None

    @model_validator(mode="after")
    def _fits(self) -> ChartBody:
        if any(len(series.values) != len(self.x) for series in self.series):
            raise ValueError("a series has a different length from x")
        if any(isinstance(value, float) and not math.isfinite(value) for value in self.x):
            raise ValueError("x is not a number")
        return self


class ChecklistItem(_Body):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")] | None = None
    text: Line
    done: bool = False


class ChecklistBody(_Component):
    items: list[ChecklistItem] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _fits(self) -> ChecklistBody:
        ids = [item.id for item in self.items if item.id]
        if len(set(ids)) != len(ids):
            raise ValueError("item ids repeat")
        return self


class Figure(_Body):
    label: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    value: Number | Shown
    unit: Unit | None = None
    delta: Delta | None = None
    tone: Literal["good", "bad", "neutral"] = "neutral"


class FiguresBody(_Component):
    items: list[Figure] = Field(min_length=1, max_length=6)


class Goal(_Body):
    label: Name
    value: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    target: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    unit: Unit | None = None


class ProgressBody(_Component):
    items: list[Goal] = Field(min_length=1, max_length=10)


class ChoicesBody(_Body):
    prompt: Annotated[str, StringConstraints(max_length=300)] | None = None
    options: list[Action] = Field(min_length=2, max_length=6)


_SCHEMAS: dict[str, type[_Body]] = {
    "table": TableBody,
    "chart": ChartBody,
    "checklist": ChecklistBody,
    "figures": FiguresBody,
    "progress": ProgressBody,
    "choices": ChoicesBody,
}


def _reject_constant(value: str) -> Any:
    raise ValueError(f"{value} is not JSON")


def format_number(value: float) -> str:
    """1200 -> "1,200"; 2.50 -> "2.5"."""

    if abs(value) >= 1e15:
        return f"{value:.6g}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def with_unit(value: float | str, unit: str | None) -> str:
    """A value with its unit where people write it: "£1,200", "62%", "5 kg"."""

    shown = value if isinstance(value, str) else format_number(value)
    if not unit:
        return shown
    if unit in _CURRENCY:
        return f"-{unit}{shown[1:]}" if shown.startswith("-") else f"{unit}{shown}"
    return f"{shown}{unit}" if unit == "%" else f"{shown} {unit}"


def _actions(body: _Component) -> list[dict[str, str]]:
    actions = []
    for action in body.actions:
        label, reply = plain_text(action.label), action.reply.strip()
        if not label or not reply or reply.startswith("/"):
            raise ValueError("an action is empty or a slash command")
        actions.append({"label": label, "reply": reply})
    return actions


def _title(body: _Component) -> str:
    return plain_text(body.title or "")


def _required(value: str) -> str:
    text = plain_text(value)
    if not text:
        raise ValueError("required text is empty")
    return text


def _heading(title: str, lines: list[str]) -> str:
    return "\n".join([title, *lines] if title else lines)


def _cell(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return format_number(value)
    return plain_text(value)


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|") or " "


def _table(body: TableBody) -> dict[str, Any]:
    columns = [
        {"key": column.key, "label": plain_text(column.label or column.key) or column.key, "type": column.type}
        for column in body.columns
    ]
    rows = [[_cell(row.get(column["key"])) for column in columns] for row in body.rows]
    title = _title(body)
    note = plain_text(body.note or "")
    lines = [
        "| " + " | ".join(_markdown_cell(column["label"]) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(_markdown_cell(cell) for cell in row) + " |" for row in rows),
    ]
    if note:
        lines.append(note)
    return {"title": title, "columns": columns, "rows": rows, "note": note, "text": _heading(title, lines)}


def _chart(body: ChartBody) -> dict[str, Any]:
    title = _title(body)
    x = [plain_text(value) if isinstance(value, str) else value for value in body.x]
    names = [_required(series.name) for series in body.series]
    data = [
        {
            "type": "bar" if body.kind == "bar" else "scatter",
            **({} if body.kind == "bar" else {"mode": "lines+markers"}),
            "name": name,
            "x": x,
            "y": series.values,
        }
        for name, series in zip(names, body.series)
    ]
    unit = plain_text(body.unit or "")
    yaxis: dict[str, Any] = {"automargin": True}
    if body.y_label:
        yaxis["title"] = {"text": plain_text(body.y_label)}
    if unit:
        yaxis["tickprefix" if unit in _CURRENCY else "ticksuffix"] = unit if unit in _CURRENCY or unit == "%" else f" {unit}"
    xaxis: dict[str, Any] = {"automargin": True}
    if body.x_label:
        xaxis["title"] = {"text": plain_text(body.x_label)}
    layout: dict[str, Any] = {"xaxis": xaxis, "yaxis": yaxis, "showlegend": len(data) > 1}
    if title:
        layout["title"] = {"text": title}
    if body.kind == "bar" and len(data) > 1:
        layout["barmode"] = "group"
    figure = json.dumps({"data": data, "layout": layout}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    lines = []
    for index, point in enumerate(x):
        values = ", ".join(
            f"{name} {'—' if series.values[index] is None else with_unit(series.values[index], unit)}"
            for name, series in zip(names, body.series)
        )
        lines.append(f"{point if isinstance(point, str) else format_number(point)}: {values}")
    text = _heading(title or ("Bar chart" if body.kind == "bar" else "Line chart"), lines)
    return {"figure_json": figure, "text": text[:4096]}


def _checklist(body: ChecklistBody) -> dict[str, Any]:
    title = _title(body)
    taken = {item.id for item in body.items if item.id}
    items = []
    for index, item in enumerate(body.items, start=1):
        identity = item.id
        if not identity:
            identity = f"item-{index}"
            while identity in taken:
                identity = f"{identity}-{index}"
            taken.add(identity)
        items.append({"id": identity, "text": _required(item.text), "done": item.done})
    lines = [f"- [{'x' if item['done'] else ' '}] {item['text']}" for item in items]
    return {"title": title, "items": items, "text": _heading(title, lines)}


def _figures(body: FiguresBody) -> dict[str, Any]:
    title = _title(body)
    items = []
    for item in body.items:
        value: float | str = item.value if not isinstance(item.value, str) else _required(item.value)
        items.append({
            "label": _required(item.label),
            "value": float(value) if not isinstance(value, str) else value,
            "unit": plain_text(item.unit or ""),
            "delta": plain_text(item.delta or ""),
            "tone": item.tone,
        })
    lines = [
        f"{item['label']}: {with_unit(item['value'], item['unit'])}" + (f" ({item['delta']})" if item["delta"] else "")
        for item in items
    ]
    return {"title": title, "items": items, "text": _heading(title, lines)}


def _progress(body: ProgressBody) -> dict[str, Any]:
    title = _title(body)
    items = [
        {"label": _required(item.label), "value": float(item.value), "target": float(item.target),
         "unit": plain_text(item.unit or "")}
        for item in body.items
    ]
    lines = [
        f"{item['label']}: {with_unit(item['value'], item['unit'])} of {with_unit(item['target'], item['unit'])}"
        f" ({round(100 * item['value'] / item['target'])}%)"
        for item in items
    ]
    return {"title": title, "items": items, "text": _heading(title, lines)}


def _choices(body: ChoicesBody) -> dict[str, Any]:
    prompt = plain_text(body.prompt or "")
    options = []
    for option in body.options:
        label, reply = plain_text(option.label), option.reply.strip()
        if not label or not reply or reply.startswith("/"):
            raise ValueError("an option is empty or a slash command")
        options.append({"label": label, "reply": reply})
    lines = [f"{index}. {option['label']}" for index, option in enumerate(options, start=1)]
    return {"prompt": prompt, "options": options, "text": _heading(prompt, lines)}


_BUILDERS = {
    "table": _table,
    "chart": _chart,
    "checklist": _checklist,
    "figures": _figures,
    "progress": _progress,
    "choices": _choices,
}


def _visual(source: str) -> tuple[str, dict[str, Any]] | None:
    """A rowbot-visual: HTML or SVG kept as written, for the client's sandboxed frame only.

    Nothing here renders it. The client draws it in a frame with ``sandbox="allow-scripts"`` (an opaque
    origin: no cookies, storage, forms, pop-ups or top navigation) under a policy with no network at all.
    """

    body = source.strip()
    if not body or len(source) > MAX_VISUAL_CHARS:
        return None
    named = _VISUAL_TITLE_RE.search(body)
    title = plain_text(named.group(1))[:120] if named else ""
    return "visual", {"title": title, "source": body, "text": f"Visual: {title or 'drawn by Row-Bot'}"}


def visual_frame(service: Any, conversation_id: str, html: str, theme: dict[str, Any]) -> str:
    """A one-time frame address for a visual in this chat (the pages' frame: served once, no network)."""

    from row_bot import pages
    from row_bot.application.conversation_search import _require_readable

    _require_readable(service, conversation_id)
    return pages.render_visual_frame(conversation_id, html, theme)


def visual_page(service: Any, conversation_id: str, title: str, html: str) -> dict[str, Any]:
    """The person's "Open as page" on a visual: the same HTML as a page beside the chat (N5's sandbox)."""

    from row_bot import pages
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.conversation_search import _require_readable

    _require_readable(service, conversation_id)
    if not html.strip() or len(html) > MAX_VISUAL_CHARS:
        raise ClientPlatformError("invalid_command")
    page = pages.create(conversation_id, plain_text(title)[:120] or "Visual", html)
    return pages.view(page)


def components_as_text(text: str) -> str:
    """The reply with each answer component as its plain text, for places that show only text.

    A chat app (Telegram, Slack, SMS…) never shows a component's JSON: a table reads as a Markdown
    table, a checklist as "- [x]" lines, figures as "Label: value", choices as a numbered list the
    person can answer in words. A fence that doesn't fit stays as it was written.
    """

    spans = component_spans(text)
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end, kind, fields in spans:
        parts.append(text[cursor:start])
        # A chat app can't draw a visual: say where it is, never send its code.
        parts.append(f"[{fields['text']}: open the chat in Row-Bot to see it]" if kind == "visual" else fields["text"])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _load(source: str) -> Any:
    """The JSON a component body holds; raises ValueError (or RecursionError) when it isn't JSON."""

    return json.loads(source, parse_constant=_reject_constant)


def _build(tag: str, raw: Any) -> tuple[str, dict[str, Any]] | None:
    """The block *raw* makes under *tag*'s schema, or None when it doesn't fit."""

    try:
        body = _SCHEMAS[tag].model_validate(raw)
        fields = _BUILDERS[tag](body)  # type: ignore[arg-type]
        if isinstance(body, _Component):
            fields["actions"] = _actions(body)
    except (ValueError, TypeError, ValidationError, RecursionError):
        return None
    if len(fields.get("text", "")) > MAX_COMPONENT_TEXT_CHARS:
        return None
    return tag, fields


def parse_component(tag: str, source: str) -> tuple[str, dict[str, Any]] | None:
    """The block a ```rowbot-<tag> fence becomes, or None to keep it as a code block.

    Returns the block type (a rowbot-chart becomes the existing ``chart`` block) and its fields.
    """

    if tag == "visual":
        return _visual(source)
    if tag == "ui":
        from row_bot.application.surfaces import parse

        return parse(source)
    if tag not in _SCHEMAS or len(source) > MAX_COMPONENT_CHARS:
        return None
    try:
        raw = _load(source)
    except (ValueError, RecursionError):
        return None
    return _build(tag, raw)


def infer_component(source: str) -> tuple[str, dict[str, Any]] | None:
    """The component an untagged JSON object is, when exactly one schema takes it whole (W14).

    A local model may write a component's body without its rowbot- tag (in a ```json fence, a bare
    fence, or as the whole reply). Each schema forbids keys it doesn't know and needs its own (a table's
    columns and rows, a chart's kind, x and series…), so an object that fits one schema exactly is that
    component. One that fits none, or more than one, stays as it was written. A visual is never inferred:
    it runs as HTML, so only its own tag makes one.
    """

    text = source.strip()
    if not (text.startswith("{") and text.endswith("}")) or len(text) > MAX_COMPONENT_CHARS:
        return None
    try:
        raw = _load(text)
    except (ValueError, RecursionError):
        return None
    from row_bot.application.surfaces import looks_like, parse

    if looks_like(raw):
        # A2UI written without its rowbot-ui tag: an envelope, or a createSurface body with components.
        return parse(text)
    if not isinstance(raw, dict):
        return None
    fits = [component for tag in _SCHEMAS if (component := _build(tag, raw)) is not None]
    return fits[0] if len(fits) == 1 else None


# A fence line: ``` with an optional language. A line with a language never closes a fence.
_FENCE_LINE_RE = re.compile(r"[ \t]{0,3}```[ \t]*([A-Za-z0-9_+.-]*)[ \t]*")
# The languages an untagged component body may be fenced with.
_LOOSE_LANGUAGES = frozenset({"", "json"})

Span = tuple[int, int, str, dict[str, Any]]


def _loose_spans(text: str) -> list[Span]:
    """Components written without their tag: a ```json or bare fence holding exactly one component's body."""

    spans: list[Span] = []
    opening: tuple[int, int, str] | None = None  # (fence start, body start, language)
    offset = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        match = _FENCE_LINE_RE.fullmatch(bare)
        if match is not None:
            language = match.group(1)
            if opening is None:
                opening = (offset, offset + len(line), language.lower())
            elif not language:
                start, body_start, opened = opening
                if opened in _LOOSE_LANGUAGES:
                    component = infer_component(text[body_start:offset])
                    if component is not None:
                        spans.append((start, offset + len(bare), *component))
                opening = None
        offset += len(line)
    return spans


def component_spans(text: str) -> list[Span]:
    """Every answer component in a reply as (start, end, block type, fields), in order.

    A ```rowbot-<tag> fence is read by its tag. A ```json or untagged fence, or a reply that is nothing
    but one JSON object, is a component only when ``infer_component`` finds exactly one that fits (W14).
    """

    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        component = infer_component(stripped)
        if component is not None:
            start = text.index(stripped)
            return [(start, start + len(stripped), *component)]
    if "```" not in text:
        return []
    tagged: list[Span] = []
    if "```rowbot-" in text:
        for match in FENCE_RE.finditer(text):
            component = parse_component(match.group(1), match.group(2))
            if component is not None:
                tagged.append((match.start(), match.end(), *component))
    loose = [span for span in _loose_spans(text)
             if not any(span[0] < end and start < span[1] for start, end, *_ in tagged)]
    return sorted(tagged + loose, key=lambda span: span[0])
