"""Surfaces: interfaces a reply draws with A2UI 1.0, from Row-Bot's own components (generative UI, G1).

A reply may hold a ```rowbot-ui fence whose body is A2UI 1.0: one envelope, a list or JSON lines of
envelopes (``createSurface``, ``updateComponents``, ``updateDataModel``), or a bare ``createSurface``
body, with an optional top-level ``title``. This module folds those messages into one surface, heals
what models commonly get slightly wrong, checks it against Row-Bot's catalog and its limits, and
returns inert data: the client draws it with its own components.

Nothing here runs or fetches anything. A surface is data, not code: only the catalog's components and
functions exist, a value is a literal, a data-model path or a catalog function call, and the client
loads no URL by itself. A Button's ``action.event`` is a name and a context; it reaches the agent only
when the person presses it, as a labelled message that never counts as approval (``action_message``).
A surface that can't be read stays an ordinary code block: nothing is lost.
"""
from __future__ import annotations

import json
import re
from typing import Any

CATALOG_ID = "https://row-bot.ai/a2ui/catalogs/rowbot/v1"
# The A2UI basic catalog: Row-Bot's extends it, so a surface written for it is drawn the same way.
BASIC_CATALOG_ID = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
VERSION = "1.0"

MAX_SOURCE_CHARS = 128 * 1024
MAX_COMPONENTS = 300
MAX_DATA_CHARS = 64 * 1024
MAX_STRING_CHARS = 4000
MAX_TEXT_CHARS = 16 * 1024
MAX_DEPTH = 24
MAX_VALUE_DEPTH = 8
MAX_ACTIONS = 24
MAX_CONTEXT_CHARS = 8 * 1024

_ACTION_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_PATH = re.compile(r"^[^\x00-\x1f]{0,300}$")

# A value's kind. "str", "num", "bool" and "strlist" are A2UI's Dynamic* types (a literal, a path or a
# function call); "child"/"children" reference components; the rest are literals or catalog shapes.
_COMMON = {"id", "component", "@catalogId", "@accessibility", "@metadata", "weight"}
_CHECKABLE = {"@checks"}
_ALIGN = frozenset({"start", "center", "end", "stretch"})
_JUSTIFY = frozenset({"start", "center", "end", "spaceBetween", "spaceAround", "spaceEvenly", "stretch"})

ICONS = frozenset({
    "accountCircle", "add", "arrowBack", "arrowForward", "attachFile", "calendarToday", "call", "camera",
    "check", "close", "delete", "download", "edit", "event", "error", "fastForward", "favorite",
    "favoriteOff", "folder", "help", "home", "info", "locationOn", "lock", "lockOpen", "mail", "menu",
    "moreVert", "moreHoriz", "notificationsOff", "notifications", "pause", "payment", "person", "phone",
    "photo", "play", "print", "refresh", "rewind", "search", "send", "settings", "share", "shoppingCart",
    "skipNext", "skipPrevious", "star", "starHalf", "starOff", "stop", "upload", "visibility",
    "visibilityOff", "volumeDown", "volumeMute", "volumeOff", "volumeUp", "warning",
})

# component -> (properties: kind, required properties, checkable)
CATALOG: dict[str, tuple[dict[str, Any], frozenset[str], bool]] = {
    "Text": ({"text": "str", "variant": frozenset({"caption", "body"})}, frozenset({"text"}), False),
    "Image": ({"url": "str", "description": "str",
               "fit": frozenset({"contain", "cover", "fill", "none", "scaleDown"}),
               "variant": frozenset({"icon", "avatar", "smallFeature", "mediumFeature", "largeFeature", "header"})},
              frozenset({"url"}), False),
    "Icon": ({"name": "icon"}, frozenset({"name"}), False),
    "Video": ({"url": "str", "posterUrl": "str"}, frozenset({"url"}), False),
    "AudioPlayer": ({"url": "str", "description": "str"}, frozenset({"url"}), False),
    "Row": ({"children": "children", "justify": _JUSTIFY, "align": _ALIGN}, frozenset({"children"}), False),
    "Column": ({"children": "children", "justify": _JUSTIFY, "align": _ALIGN}, frozenset({"children"}), False),
    "List": ({"children": "children", "direction": frozenset({"vertical", "horizontal"}), "align": _ALIGN},
             frozenset({"children"}), False),
    "Card": ({"child": "child"}, frozenset({"child"}), False),
    "Tabs": ({"tabs": "tabs"}, frozenset({"tabs"}), False),
    "Modal": ({"trigger": "child", "content": "child"}, frozenset({"trigger", "content"}), False),
    "Divider": ({"axis": frozenset({"horizontal", "vertical"})}, frozenset(), False),
    "Button": ({"child": "child", "variant": frozenset({"default", "primary", "borderless"}), "action": "action"},
               frozenset({"child", "action"}), True),
    "TextField": ({"label": "str", "value": "str", "placeholder": "str",
                   "variant": frozenset({"longText", "number", "shortText", "obscured"})},
                  frozenset({"label", "value"}), True),
    "CheckBox": ({"label": "str", "value": "bool"}, frozenset({"label", "value"}), True),
    "ChoicePicker": ({"label": "str", "variant": frozenset({"multipleSelection", "mutuallyExclusive"}),
                      "options": "options", "value": "strlist", "displayStyle": frozenset({"checkbox", "chips"}),
                      "filterable": "lbool"},
                     frozenset({"options", "value"}), True),
    "Slider": ({"label": "str", "min": "lnum", "max": "lnum", "value": "num", "steps": "lint"},
               frozenset({"value", "max"}), True),
    "DateTimeInput": ({"value": "str", "enableDate": "lbool", "enableTime": "lbool", "min": "str", "max": "str",
                       "label": "str"},
                      frozenset({"value"}), True),
    # Row-Bot's own components (G2), beyond the basic catalog. Their values bind like any other, so a chart
    # or a total follows a slider as the person moves it.
    "Chart": ({"kind": frozenset({"bar", "line", "area", "pie"}), "title": "str", "x": "any", "series": "series",
               "unit": "lstr", "stacked": "lbool", "rows": "any"},
              frozenset({"kind", "x", "series"}), False),
    "Table": ({"title": "str", "columns": "columns", "rows": "any"}, frozenset({"rows"}), False),
    "Metric": ({"label": "str", "value": "any", "unit": "lstr", "delta": "str",
                "tone": frozenset({"good", "bad", "neutral"})},
               frozenset({"label", "value"}), False),
    "ProgressBar": ({"label": "str", "value": "num", "max": "num", "unit": "lstr"},
                    frozenset({"label", "value", "max"}), False),
    "Breakdown": ({"items": "any", "currency": "lstr", "unit": "lstr", "totalLabel": "str", "max": "lnum"},
                  frozenset({"items"}), False),
    "Timeline": ({"items": "any"}, frozenset({"items"}), False),
    "Code": ({"code": "str", "language": "lstr"}, frozenset({"code"}), False),
}

# Basic-catalog functions. ``openUrl`` needs the person's click, so it is allowed only as a Button's action.
FUNCTIONS = frozenset({
    "required", "regex", "length", "numeric", "email", "formatString", "formatNumber", "formatCurrency",
    "formatDate", "pluralize", "and", "or", "not", "@index",
})
ACTION_FUNCTIONS = frozenset({"openUrl"})

_REFERENCE_KINDS = frozenset({"child", "children", "tabs"})


class SurfaceError(ValueError):
    """The body isn't a surface Row-Bot can draw."""


# ── Reading the messages ──────────────────────────────────────────────────────


def _reject_constant(value: str) -> Any:
    raise ValueError(f"{value} is not JSON")


def _closed_component(text: str, error: json.JSONDecodeError) -> str | None:
    """Healed: a component left unclosed before the next one starts (`..."/fun"}}}},{"id": ...`, one
    "}" short), as local models write it. The object the next one landed in is closed."""

    comma = text.rfind(",", 0, error.pos)
    if not (error.msg.startswith("Expecting property name") and text.startswith("{", error.pos)
            and comma >= 0 and not text[comma + 1:error.pos].strip()):
        return None
    return f"{text[:comma]}}}{text[comma:]}"


def _without_broken_line(text: str, error: json.JSONDecodeError) -> str | None:
    """Healed: in a body written one component per line, a line that isn't JSON
    (`{"id": "i4", "component": "Text": "..."}`) is left out, so the rest of the interface draws."""

    start = text.rfind("\n", 0, error.pos) + 1
    end = text.find("\n", error.pos)
    end = len(text) if end < 0 else end
    if start == 0 or not text[start:end].strip().startswith('{"id"'):
        return None
    before, after = text[:start].rstrip(), text[end:].lstrip()
    if before.endswith(",") and after.startswith("]"):
        before = before[:-1]  # it was the list's last item
    return f"{before}\n{after}"


def _load(source: str) -> list[Any]:
    """The JSON values a body holds: one value, or one per line (JSON lines)."""

    text = source.strip()
    for _attempt in range(6):
        try:
            return [json.loads(text, parse_constant=_reject_constant)]
        except json.JSONDecodeError as error:
            healed = _closed_component(text, error) or _without_broken_line(text, error)
            if healed is None:
                break
            text = healed
        except (ValueError, RecursionError):
            break
    text = source.strip()
    values = []
    for line in text.splitlines():
        if line.strip():
            try:
                values.append(json.loads(line, parse_constant=_reject_constant))
            except (ValueError, RecursionError):
                raise SurfaceError("not JSON") from None
    if not values:
        raise SurfaceError("empty")
    return values


_MESSAGES = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")


def _envelopes(values: list[Any]) -> tuple[list[tuple[str, dict[str, Any]]], str, dict[str, Any]]:
    """(message kind, body) for each message, and the title and sources given beside them."""

    found: list[tuple[str, dict[str, Any]]] = []
    title = ""
    sources: dict[str, Any] = {}
    pending = list(values)
    while pending:
        value = pending.pop(0)
        if isinstance(value, list):
            pending[:0] = value
            continue
        if not isinstance(value, dict):
            raise SurfaceError("a message is not an object")
        if isinstance(value.get("title"), str) and not title:
            title = value["title"]
        if isinstance(value.get("sources"), dict) and not sources:
            sources = value["sources"]
        kinds = [kind for kind in _MESSAGES if kind in value]
        if kinds:
            for kind in kinds:
                body = value[kind]
                if not isinstance(body, dict):
                    raise SurfaceError("a message body is not an object")
                if isinstance(body.get("title"), str) and not title:
                    title = body["title"]
                if isinstance(body.get("sources"), dict) and not sources:
                    sources = body["sources"]
                found.append((kind, body))
        elif isinstance(value.get("components"), list):
            # A bare createSurface body (no envelope).
            found.append(("createSurface", value))
        else:
            raise SurfaceError("not an A2UI message")
    return found, title, sources


# ── Data model ────────────────────────────────────────────────────────────────


def _pointer(path: str) -> list[str]:
    if path in ("", "/"):
        return []
    return [part.replace("~1", "/").replace("~0", "~") for part in path.strip("/").split("/")]


def _set(model: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    parts = _pointer(path)
    if not parts:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise SurfaceError("the data model is not an object")
        return value
    node: Any = model
    for part in parts[:-1]:
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
            continue
        if not isinstance(node, dict):
            raise SurfaceError("a data path goes through a value")
        node = node.setdefault(part, {})
    last = parts[-1]
    if isinstance(node, list) and last.isdigit():
        index = int(last)
        if index < len(node):
            node[index] = value
        elif index == len(node) and value is not None:
            node.append(value)
        return model
    if not isinstance(node, dict):
        raise SurfaceError("a data path goes through a value")
    if value is None:
        node.pop(last, None)
    else:
        node[last] = value
    return model


def read_path(model: Any, path: str, scope: Any = None) -> Any:
    """The value at a JSON Pointer: absolute ("/a/b") from the model, relative ("b") from ``scope``."""

    node = model if path.startswith("/") or scope is None else scope
    for part in _pointer(path):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


# ── Healing and checking ──────────────────────────────────────────────────────


def _identifier(value: Any) -> str:
    text = re.sub(r"\W", "_", str(value or "").strip())[:64]
    if not text:
        return ""
    return text if text.isidentifier() else f"_{text}"


def _healed_value(value: Any) -> Any:
    """A0.8/0.9 spellings models still write, in 1.0's: {"path": p} -> {"@path": p}, {"literalString": s} -> s."""

    if isinstance(value, dict):
        if len(value) == 1:
            key, inner = next(iter(value.items()))
            if key == "path" and isinstance(inner, str):
                return {"@path": inner}
            if key in {"literalString", "literalNumber", "literalBoolean", "literal"}:
                return inner
            if key == "literalArray" and isinstance(inner, list):
                return inner
        if "call" in value and "@call" not in value and isinstance(value.get("call"), str):
            return {"@call": value["call"], **({"args": value["args"]} if "args" in value else {})}
    return value


def _value(value: Any, kind: str, depth: int = 0) -> Any:
    """A checked Dynamic* value, or raises SurfaceError."""

    value = _healed_value(value)
    if depth > MAX_VALUE_DEPTH:
        raise SurfaceError("a value nests too deep")
    if isinstance(value, dict):
        if "@path" in value:
            path = value["@path"]
            if set(value) != {"@path"} or not isinstance(path, str) or not _PATH.match(path):
                raise SurfaceError("a data binding is not a path")
            return {"@path": path}
        if "@call" in value:
            return _call(value, FUNCTIONS, depth)
        if kind != "any":
            raise SurfaceError("an object where a value belongs")
        if any(str(key).startswith("@") for key in value):
            raise SurfaceError("a literal object uses a reserved key")
        return {str(key)[:64]: _value(item, "any", depth + 1) for key, item in value.items()}
    if kind == "str":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise SurfaceError("not text")
        return _text(value)
    if kind == "num":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SurfaceError("not a number")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise SurfaceError("not true or false")
        return value
    if kind == "strlist":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SurfaceError("not a list of text")
        return [_text(item) for item in value[:200]]
    # "any": a literal of any JSON type.
    if isinstance(value, list):
        return [_value(item, "any", depth + 1) for item in value[:200]]
    if isinstance(value, str):
        return _text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise SurfaceError("not a value")


def _text(value: Any) -> str:
    text = str(value)
    if len(text) > MAX_STRING_CHARS:
        raise SurfaceError("a text is too long")
    return text


def _call(value: dict[str, Any], allowed: frozenset[str], depth: int) -> dict[str, Any]:
    name = value.get("@call")
    if not isinstance(name, str) or name not in allowed:
        raise SurfaceError("a function the catalog doesn't have")
    if set(value) - {"@call", "args", "catalogId"}:
        raise SurfaceError("a function call has unknown keys")
    args = value.get("args", {})
    if not isinstance(args, dict):
        raise SurfaceError("a function's arguments are not an object")
    checked = {str(key)[:64]: _value(item, "any", depth + 1) for key, item in args.items()}
    return {"@call": name, "args": checked} if checked or "args" in value else {"@call": name}


def _children(value: Any) -> Any:
    if isinstance(value, str):
        value = [value]  # Healed: one child written without its list.
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise SurfaceError("children are not component ids")
        return list(value[:MAX_COMPONENTS])
    if isinstance(value, dict):
        template = value.get("componentId")
        path = value.get("path")
        if isinstance(path, dict):  # Healed: {"path": {"@path": "/items"}}.
            path = path.get("@path")
        if not isinstance(template, str) or not isinstance(path, str) or not _PATH.match(path):
            raise SurfaceError("a template is not a path and a component")
        return {"path": path, "componentId": template}
    raise SurfaceError("children are not a list or a template")


def _action(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and "name" in value and "event" not in value and "functionCall" not in value:
        value = {"event": value}  # Healed: 0.8/0.9's {"name", "context"} without its "event".
    if not isinstance(value, dict) or len(value) != 1:
        raise SurfaceError("an action is not an event or a function call")
    if "functionCall" in value:
        call = value["functionCall"]
        if not isinstance(call, dict):
            raise SurfaceError("an action's function call is not an object")
        checked_call = _call(call, FUNCTIONS | ACTION_FUNCTIONS, 0)
        if checked_call["@call"] == "openUrl" and not isinstance(checked_call.get("args", {}).get("url"), str):
            # A link is fixed when the interface is drawn: never built from the data model, so a value
            # Row-Bot filled in on this computer can't leave in an address.
            raise SurfaceError("a link that isn't a fixed address")
        return {"functionCall": checked_call}
    event = value.get("event")
    if not isinstance(event, dict) or not isinstance(event.get("name"), str):
        raise SurfaceError("an action has no event name")
    name = event["name"].strip()
    if not _ACTION_NAME.match(name):
        raise SurfaceError("an action's name is not a name")
    checked: dict[str, Any] = {"name": name}
    if "userMessage" in event:
        checked["userMessage"] = _value(event["userMessage"], "str")
    context = event.get("context")
    if isinstance(context, list):  # Healed: 0.8's [{"key": k, "value": v}].
        context = {str(item.get("key")): item.get("value") for item in context
                   if isinstance(item, dict) and item.get("key")}
    if context is not None:
        if not isinstance(context, dict):
            raise SurfaceError("an action's context is not an object")
        checked["context"] = {str(key)[:64]: _value(item, "any") for key, item in list(context.items())[:32]}
    return {"event": checked}


def _property(kind: Any, value: Any) -> Any:
    if isinstance(kind, frozenset):
        if value not in kind:
            raise SurfaceError("not one of the allowed values")
        return value
    if kind in {"str", "num", "bool", "strlist", "any"}:
        return _value(value, kind)
    if kind == "lstr":
        if not isinstance(value, str):
            raise SurfaceError("not text")
        return _text(value)[:40]
    if kind == "series":
        if not isinstance(value, list) or not value:
            raise SurfaceError("series are not a list")
        series = []
        for item in value[:8]:
            if not isinstance(item, dict) or "values" not in item:
                raise SurfaceError("a series has no values")
            series.append({"name": _value(item.get("name", ""), "str"), "values": _value(item["values"], "any")})
        return series
    if kind == "columns":
        if not isinstance(value, list) or not value:
            raise SurfaceError("columns are not a list")
        columns = []
        for column in value[:12]:
            if isinstance(column, str):
                column = {"key": column}  # Healed: a bare column key.
            if not isinstance(column, dict) or not isinstance(column.get("key"), str):
                raise SurfaceError("a column has no key")
            kind_of = column.get("type", "text")
            columns.append({"key": _text(column["key"])[:64],
                            "label": _value(column.get("label", column["key"]), "str"),
                            "type": kind_of if kind_of in {"text", "number", "currency", "percent", "date"} else "text"})
        return columns
    if kind == "child":
        if not isinstance(value, str):
            raise SurfaceError("a child is not a component id")
        return value
    if kind == "children":
        return _children(value)
    if kind == "action":
        return _action(value)
    if kind == "icon":
        if isinstance(value, str) and value in ICONS:
            return value
        if isinstance(value, dict) and "@path" in value:
            return _value(value, "str")
        if isinstance(value, dict) and set(value) == {"svgPath"}:
            return {"svgPath": _value(value["svgPath"], "str")}
        raise SurfaceError("an icon the catalog doesn't have")
    if kind == "tabs":
        if not isinstance(value, list) or not value:
            raise SurfaceError("tabs are not a list")
        tabs = []
        for tab in value[:12]:
            if not isinstance(tab, dict) or not isinstance(tab.get("child"), str) or "title" not in tab:
                raise SurfaceError("a tab has no title or child")
            tabs.append({"title": _value(tab["title"], "str"), "child": tab["child"]})
        return tabs
    if kind == "options":
        if not isinstance(value, list) or not value:
            raise SurfaceError("options are not a list")
        options = []
        for option in value[:50]:
            if isinstance(option, str):
                option = {"label": option, "value": option}  # Healed: a bare option.
            if not isinstance(option, dict) or "label" not in option or not isinstance(option.get("value"), str):
                raise SurfaceError("an option has no label or value")
            options.append({"label": _value(option["label"], "str"), "value": _text(option["value"])})
        return options
    if kind == "lbool":
        if not isinstance(value, bool):
            raise SurfaceError("not true or false")
        return value
    if kind in {"lnum", "lint"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SurfaceError("not a number")
        return int(value) if kind == "lint" else value
    raise SurfaceError("an unknown property kind")


def _checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SurfaceError("checks are not a list")
    checks = []
    for rule in value[:8]:
        if not isinstance(rule, dict) or "condition" not in rule:
            raise SurfaceError("a check has no condition")
        condition = _healed_value(rule["condition"])
        if not isinstance(condition, dict) or not ({"@path", "@call"} & set(condition)):
            raise SurfaceError("a check's condition is not a path or a call")
        checked: dict[str, Any] = {"condition": _value(condition, "bool")}
        if isinstance(rule.get("message"), str):
            checked["message"] = _text(rule["message"])
        checks.append(checked)
    return checks


def _accessibility(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SurfaceError("accessibility is not an object")
    checked: dict[str, Any] = {}
    for key in ("label", "description"):
        if key in value:
            checked[key] = _value(value[key], "str")
    if value.get("live") in {"off", "polite", "assertive"}:
        checked["live"] = value["live"]
    if "hidden" in value:
        checked["hidden"] = _value(value["hidden"], "bool")
    return checked


def _component(raw: Any) -> dict[str, Any] | None:
    """A checked component, or None when it can't be drawn (unknown, or a required property is wrong)."""

    if not isinstance(raw, dict):
        return None
    component = raw.get("component")
    if isinstance(component, dict) and len(component) == 1:
        # Healed: 0.8's {"id": ..., "component": {"Text": {...}}}.
        name, properties = next(iter(component.items()))
        raw = {**(properties if isinstance(properties, dict) else {}), "id": raw.get("id"), "component": name}
        component = name
    if not isinstance(component, str) or component not in CATALOG:
        return None
    if component == "Text" and "text" not in raw and "value" in raw:
        raw = {**raw, "text": raw["value"]}  # Healed: a Text's words written as its "value".
    if component in _EMPTY_INPUT and "value" not in raw:
        raw = {**raw, "value": _EMPTY_INPUT[component]}  # Healed: an input written without its value.
    properties, required, checkable = CATALOG[component]
    checked: dict[str, Any] = {"id": raw.get("id"), "component": component}
    for key, value in raw.items():
        if key in properties:
            try:
                checked[key] = _property(properties[key], value)
            except SurfaceError:
                if key in required:
                    return None
        elif key == "weight" and isinstance(value, (int, float)) and not isinstance(value, bool):
            checked["weight"] = max(0, min(float(value), 12))
        elif key == "@accessibility":
            try:
                checked[key] = _accessibility(value)
            except SurfaceError:
                pass
        elif key == "@checks" and checkable:
            try:
                checked[key] = _checks(value)
            except SurfaceError:
                pass
        # Anything else (an unknown property, @metadata, @catalogId) is dropped.
    if not required <= set(checked):
        return None
    return checked


def _references(component: dict[str, Any]) -> list[str]:
    """The component ids a component points at, in order."""

    kinds = CATALOG[component["component"]][0]
    found: list[str] = []
    for key, kind in kinds.items():
        if kind not in _REFERENCE_KINDS or key not in component:
            continue
        value = component[key]
        if kind == "child":
            found.append(value)
        elif kind == "children":
            found.extend(value if isinstance(value, list) else [value["componentId"]])
        else:
            found.extend(tab["child"] for tab in value)
    return found


def _renamed(component: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    kinds = CATALOG[component["component"]][0]
    renamed = {**component, "id": names.get(component["id"], component["id"])}
    for key, kind in kinds.items():
        if kind not in _REFERENCE_KINDS or key not in renamed:
            continue
        value = renamed[key]
        if kind == "child":
            renamed[key] = names.get(value, value)
        elif kind == "children":
            renamed[key] = ([names.get(item, item) for item in value] if isinstance(value, list)
                            else {**value, "componentId": names.get(value["componentId"], value["componentId"])})
        else:
            renamed[key] = [{**tab, "child": names.get(tab["child"], tab["child"])} for tab in value]
    return renamed


def _settle(components: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Ids made identifiers everywhere, a root named, and only components reachable from it, without cycles."""

    names: dict[str, str] = {}
    taken: set[str] = set()
    for original in components:
        name = _identifier(original) or "component"
        base, count = name, 1
        while name in taken:
            count += 1
            name = f"{base}_{count}"
        taken.add(name)
        names[original] = name
    settled = {names[key]: _renamed(value, names) for key, value in components.items()}
    if "root" not in settled:
        # Healed: no component named root. The first one nothing points at becomes it.
        pointed = {child for component in settled.values() for child in _references(component)}
        first = next((key for key in settled if key not in pointed), None)
        if first is None:
            raise SurfaceError("no root")
        names = {first: "root"}
        settled = {names.get(key, key): _renamed(value, names) for key, value in settled.items()}
    reachable: dict[str, dict[str, Any]] = {}

    def visit(key: str, depth: int, path: frozenset[str]) -> None:
        if key in path:
            raise SurfaceError("a component contains itself")
        if depth > MAX_DEPTH:
            raise SurfaceError("the surface nests too deep")
        component = settled.get(key)
        if component is None:
            return  # A missing child draws nothing.
        reachable[key] = component
        for child in _references(component):
            visit(child, depth + 1, path | {key})

    visit("root", 0, frozenset())
    return reachable


# An input written without its value starts empty (and _bound_inputs gives it its place).
_EMPTY_INPUT: dict[str, Any] = {"TextField": "", "DateTimeInput": "", "CheckBox": False, "ChoicePicker": []}

# What the person changes in each editable component.
_EDITABLE = {"TextField": "value", "CheckBox": "value", "ChoicePicker": "value", "Slider": "value",
             "DateTimeInput": "value", "Breakdown": "items"}


def _templated(components: dict[str, dict[str, Any]]) -> set[str]:
    """The components drawn once per item of a list (a template and everything under it)."""

    templated: set[str] = set()
    pending = [component["children"]["componentId"] for component in components.values()
               if isinstance(component.get("children"), dict)]
    while pending:
        key = pending.pop()
        if key in templated or key not in components:
            continue
        templated.add(key)
        pending.extend(_references(components[key]))
    return templated


def _sending_inputs(components: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Healed: a button that sends nothing the person entered (a form's Send with only
    ``"context": {"purpose": "booking"}``) sends the interface's inputs too, each under its id. Without
    them the press reaches Row-Bot empty. A button drawn per item keeps its item's context; a value
    Row-Bot fills in on this computer still never goes (``declared_actions``)."""

    templated = _templated(components)
    inputs = {key: component[_EDITABLE[component["component"]]] for key, component in components.items()
              if component["component"] in _EDITABLE and key not in templated
              and isinstance(component.get(_EDITABLE[component["component"]]), dict)
              and str(component[_EDITABLE[component["component"]]].get("@path", "")).startswith("/")}
    if not inputs:
        return components
    healed = dict(components)
    for key, component in components.items():
        event = component.get("action", {}).get("event") if component["component"] == "Button" else None
        if not event or key in templated:
            continue
        context = event.get("context") or {}
        if any('"@path"' in json.dumps(value) or "${" in json.dumps(value) for value in context.values()):
            continue
        sent = {**dict(list(inputs.items())[:12]), **context}
        healed[key] = {**component, "action": {**component["action"], "event": {**event, "context": sent}}}
    return healed


def _bound_inputs(components: dict[str, dict[str, Any]], data: Any) -> tuple[dict[str, dict[str, Any]], Any]:
    """Healed: an input written with a plain value ("value": false, a Breakdown's list of items) gets its
    own place in the data model, starting at that value, so the person can tick, type, pick or slide in
    it. One inside a repeated item binds relative to its item. Without a path it would only show the
    value, never take a change."""

    if not isinstance(data, dict):
        return components, data
    templated = _templated(components)
    healed, data = dict(components), dict(data)
    for key, component in components.items():
        prop = _EDITABLE.get(component["component"])
        if prop is None or prop not in component:
            continue
        value = component[prop]
        if isinstance(value, dict) and ("@path" in value or "@call" in value):
            continue
        if key in templated:
            # Each item starts empty there, so only an empty plain value (unticked, blank) can move.
            if value in (False, "", None) or value == []:
                healed[key] = {**component, prop: {"@path": key}}
        elif key not in data:
            healed[key] = {**component, prop: {"@path": f"/{key}"}}
            data[key] = value
    return healed, data


MAX_SOURCES = 8


def _sources(raw: dict[str, Any]) -> dict[str, Any]:
    """A surface's private bindings: data paths Row-Bot fills in on this computer (``surface_sources``).

    Each is an absolute data path and one of Row-Bot's read-only sources, with literal arguments only: a
    source never reads the data model, so nothing the person entered can steer what it reads.
    """

    from row_bot.application.surface_sources import SOURCES

    checked: dict[str, Any] = {}
    for path, call in list(raw.items())[:MAX_SOURCES]:
        call = _healed_value(call)
        if isinstance(path, str) and path and not path.startswith("/"):
            path = f"/{path}"  # Healed: a source named by its key ("rows") rather than its path.
        if not isinstance(path, str) or not path.startswith("/") or not _PATH.match(path) or path == "/":
            continue
        if not isinstance(call, dict) or call.get("@call") not in SOURCES:
            continue
        args = call.get("args") or {}
        if not isinstance(args, dict):
            continue
        literal = {str(key)[:40]: value for key, value in list(args.items())[:8]
                   if value is None or isinstance(value, (bool, int, float))
                   or (isinstance(value, str) and len(value) <= 300)}
        checked[path] = {"@call": call["@call"], "args": literal}
    return checked


def _healed_data(data: Any) -> tuple[Any, dict[str, Any]]:
    """Healed: data keys written as paths ({"/budget": {...}} is "budget"), and Row-Bot's sources written
    into the data model ({"tasks": {"@call": "rowbot.tasks"}}, or {"sources": {"/tasks": ...}}), which
    become the surface's sources."""

    if not isinstance(data, dict):
        return data, {}
    healed: dict[str, Any] = {}
    moved: dict[str, Any] = {}
    for key, value in data.items():
        path = key if key.startswith("/") else f"/{key}"
        if key == "sources" and isinstance(value, dict) and value and all(
                isinstance(call, dict) and str(call.get("@call") or "").startswith("rowbot.")
                for call in value.values()):
            moved.update(value)
        elif isinstance(value, dict) and str(value.get("@call") or "").startswith("rowbot."):
            moved[path] = value
        elif key.startswith("/") and key != "/" and _PATH.match(key):
            healed = _set(healed, key, value)
        else:
            healed[key] = value
    return healed, moved


def _healed_chart(chart: dict[str, Any], sources: dict[str, Any], data: Any) -> dict[str, Any]:
    """Healed: a chart over rows of objects written without ``rows``.

    Models write x as a column name (``"date"``) beside one source, or as a column inside a list of rows
    (``{"@path": "/spend/date"}``, which reads nothing): both become ``rows`` with column names.
    """

    if "rows" in chart:
        return chart
    x = chart.get("x")
    if isinstance(x, str):
        return {**chart, "rows": {"@path": next(iter(sources))}} if len(sources) == 1 else chart
    path = x.get("@path") if isinstance(x, dict) else None
    if not isinstance(path, str) or not path.startswith("/") or path.count("/") < 2:
        return chart
    parent, column = path.rsplit("/", 1)
    if not (parent in sources or isinstance(read_path(data, parent), list)) or isinstance(read_path(data, path), list):
        return chart

    def named(values: Any) -> Any:
        inner = values.get("@path") if isinstance(values, dict) else None
        return inner.rsplit("/", 1)[1] if isinstance(inner, str) and inner.startswith(parent + "/") else values

    return {**chart, "rows": {"@path": parent}, "x": column,
            "series": [{**item, "values": named(item["values"])} for item in chart["series"]]}


def _healed_rows(component: dict[str, Any], sources: dict[str, Any], data: Any) -> dict[str, Any]:
    """Healed: a table's or chart's rows, x or values written as a source's path in plain text ("/rows",
    "/rows/date") read that source, then a chart over rows gets its columns (``_healed_chart``). Only a
    path into one of the surface's own sources is read: any other text stays as written."""

    if component["component"] not in {"Table", "Chart"}:
        return component

    def bound(value: Any) -> Any:
        if isinstance(value, str) and any(value == path or value.startswith(path + "/") for path in sources):
            return {"@path": value}
        return value

    healed = {**component, **({"rows": bound(component["rows"])} if "rows" in component else {})}
    if component["component"] == "Table":
        return healed
    healed["x"] = bound(component["x"])
    healed["series"] = [{**item, "values": bound(item["values"])} for item in component["series"]]
    return _healed_chart(healed, sources, data)


def read_surface(source: str) -> dict[str, Any]:
    """The surface a ```rowbot-ui body describes, checked and healed; raises SurfaceError."""

    if len(source) > MAX_SOURCE_CHARS:
        raise SurfaceError("too large")
    envelopes, title, raw_sources = _envelopes(_load(source))
    surface_id = ""
    components: dict[str, dict[str, Any]] = {}
    data: dict[str, Any] = {}
    created = False
    for kind, body in envelopes:
        named = _identifier(body.get("surfaceId")) if body.get("surfaceId") is not None else ""
        if created and named and named != surface_id:
            continue  # One surface per block: messages for another are not this one's.
        if kind == "createSurface":
            if created:
                continue
            created, surface_id = True, named or "surface"
            if body.get("dataModel") is not None:
                data = _set({}, "/", body["dataModel"])
            raw_components = body.get("components") or []
        elif kind == "updateComponents":
            if not created:
                created, surface_id = True, named or "surface"
            raw_components = body.get("components") or []
        elif kind == "updateDataModel":
            path = body.get("path", "/")
            if not isinstance(path, str) or not _PATH.match(path):
                raise SurfaceError("a data path is not a path")
            data = _set(data, path, body.get("value"))
            continue
        else:  # deleteSurface
            raise SurfaceError("the surface was deleted")
        if not isinstance(raw_components, list):
            raise SurfaceError("components are not a list")
        for raw in raw_components:
            component = _component(raw)
            if component is None or not isinstance(component.get("id"), (str, int)):
                continue
            components[str(component["id"])] = {**component, "id": str(component["id"])}
            if len(components) > MAX_COMPONENTS:
                raise SurfaceError("too many components")
    if not created or not components:
        raise SurfaceError("no components")
    data, moved = _healed_data(data)
    raw_sources = {**moved, **raw_sources}
    data = _value(data, "any") if data else {}
    if len(json.dumps(data, ensure_ascii=False)) > MAX_DATA_CHARS:
        raise SurfaceError("the data model is too large")
    reachable, data = _bound_inputs(_settle(components), data)
    reachable = _sending_inputs(reachable)
    surface = {
        "surfaceId": surface_id,
        "catalogId": CATALOG_ID,
        "title": " ".join(str(title).split())[:120],
        "components": list(reachable.values()),
        "dataModel": data,
    }
    if sources := _sources(raw_sources):
        surface["sources"] = sources
    surface["components"] = [_healed_rows(component, sources, data) for component in surface["components"]]
    if not surface["title"]:
        # No title given: the surface's first words name it.
        first = next((line for line in surface_text(surface).splitlines() if line.strip()), "")
        surface["title"] = " ".join(first.lstrip("#").replace("*", "").split())[:120] or "Interface"
    return surface


# ── What a surface says, in words ─────────────────────────────────────────────


def _resolve(value: Any, data: Any, scope: Any) -> Any:
    if isinstance(value, dict):
        if "@path" in value:
            return read_path(data, value["@path"], scope)
        if value.get("@call") == "formatString":
            template = str((value.get("args") or {}).get("value") or "")
            return re.sub(r"\$\{([^}]*)\}", lambda match: _shown(read_path(data, match.group(1).strip(), scope)),
                          template)
        if value.get("@call") in {"formatNumber", "formatCurrency", "formatDate", "pluralize"}:
            return _shown(_resolve((value.get("args") or {}).get("value"), data, scope))
        if "@call" in value:
            return None
    return value


def _shown(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    items = list(value.values()) if isinstance(value, dict) else value
    if isinstance(items, list) and items and all(isinstance(item, bool) for item in items):
        return f"{sum(items)} of {len(items)} ticked"  # A checklist's ticks, not their JSON.
    if isinstance(value, list) and all(not isinstance(item, (list, dict)) for item in value):
        return ", ".join(_shown(item) for item in value)
    if isinstance(value, dict) and all(not isinstance(item, (list, dict)) for item in value.values()):
        return ", ".join(f"{_key_words(str(key))}: {_shown(item)}" for key, item in value.items())
    if isinstance(value, (list, dict)):
        return f"{len(value)} items" if isinstance(value, list) else json.dumps(value, ensure_ascii=False)
    return " ".join(str(value).split())


def _key_words(key: str) -> str:
    """A context key in words: "check_in" -> "Check in"."""

    words = " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace("_", " ").replace("-", " ").split())
    return words[:1].upper() + words[1:].lower()


def _row_keys(rows: Any) -> list[str]:
    """The columns rows of objects have, in the order they first appear."""

    keys: list[str] = []
    for row in (rows if isinstance(rows, list) else [])[:20]:
        if isinstance(row, dict):
            keys.extend(key for key in row if key not in keys)
    return keys


def _numeric(rows: list[Any], key: str) -> bool:
    values = [row.get(key) for row in rows if isinstance(row, dict) and row.get(key) not in (None, "")]
    return bool(values) and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)


def fit_columns(columns: list[dict[str, Any]], rows: Any) -> list[dict[str, Any]]:
    """A table's columns fitted to its rows: as drawn when the rows have every one, else the rows' own.

    The model often names a source's columns without seeing the file; the rows Row-Bot fills in on this
    computer decide which there are, keeping the model's label and type where a name matches.
    """

    keys = _row_keys(rows)
    if not keys or (columns and all(column["key"] in keys for column in columns)):
        return columns
    drawn = {column["key"]: column for column in columns}
    return [drawn.get(key) or {"key": key, "label": _key_words(key),
                               "type": "number" if _numeric(rows, key) else "text"} for key in keys[:12]]


def chart_points(component: dict[str, Any], data: Any, scope: Any) -> tuple[list[Any], list[tuple[str, Any]]]:
    """A chart's x values and its (name, values) series. Over ``rows``, x and the values name columns, fitted
    to the rows' real ones like a table's: the first text column for x, the number columns for values."""

    rows = _resolve(component.get("rows"), data, scope)
    series = component["series"]
    if not isinstance(rows, list) or not _row_keys(rows):
        x = _resolve(component.get("x"), data, scope)
        return (x if isinstance(x, list) else []), [
            (_shown(_resolve(item.get("name"), data, scope)), _resolve(item["values"], data, scope)) for item in series]
    keys = _row_keys(rows)
    numbers = [key for key in keys if _numeric(rows, key)]
    x_key = component.get("x") if component.get("x") in keys else next(
        (key for key in keys if key not in numbers), keys[0])
    named = [(_shown(_resolve(item.get("name"), data, scope)) or _key_words(item["values"]), item["values"])
             for item in series if isinstance(item.get("values"), str) and item["values"] in keys]
    named = named or [(_key_words(key), key) for key in numbers if key != x_key][:4]
    objects = [row for row in rows[:200] if isinstance(row, dict)]
    return [row.get(x_key) for row in objects], [(name, [row.get(key) for row in objects]) for name, key in named]


def surface_text(surface: dict[str, Any]) -> str:
    """The surface in plain words, for copying, search and places that show only text (chat apps)."""

    by_id = {component["id"]: component for component in surface["components"]}
    data = surface.get("dataModel") or {}
    lines: list[str] = []

    def say(value: Any, scope: Any) -> str:
        return _shown(_resolve(value, data, scope))

    def walk(key: str, scope: Any, depth: int) -> None:
        component = by_id.get(key)
        if component is None or depth > MAX_DEPTH or sum(len(line) for line in lines) > MAX_TEXT_CHARS:
            return
        kind = component["component"]
        if kind == "Text":
            if text := say(component["text"], scope):
                lines.append(text)
        elif kind == "Image":
            lines.append(f"[Image: {say(component.get('description'), scope) or 'picture'}]")
        elif kind in {"Video", "AudioPlayer"}:
            lines.append(f"[{'Video' if kind == 'Video' else 'Audio'}]")
        elif kind == "Button":
            lines.append(f"[{_label(component, by_id, data, scope) or component['action'].get('event', {}).get('name', '')}]")
        elif kind in {"TextField", "DateTimeInput", "Slider"}:
            label = say(component.get("label"), scope)
            lines.append(f"{label}: {say(component.get('value'), scope)}" if label else say(component.get("value"), scope))
        elif kind == "CheckBox":
            ticked = _resolve(component.get("value"), data, scope) is True
            lines.append(f"- [{'x' if ticked else ' '}] {say(component.get('label'), scope)}")
        elif kind == "ChoicePicker":
            chosen = _resolve(component.get("value"), data, scope)
            labels = [say(option["label"], scope) for option in component["options"]
                      if isinstance(chosen, list) and option["value"] in chosen]
            label = say(component.get("label"), scope)
            lines.append(f"{label}: {', '.join(labels)}" if label else ", ".join(labels))
        elif kind == "Chart":
            title = say(component.get("title"), scope)
            x, named = chart_points(component, data, scope)
            lines.append(f"Chart: {title}" if title else "Chart")
            for index, point in enumerate(x):
                parts = [f"{name} {_shown(column[index])}".strip() for name, column in named
                         if isinstance(column, list) and index < len(column)]
                lines.append(f"{_shown(point)}: {', '.join(parts)}")
        elif kind == "Table":
            if title := say(component.get("title"), scope):
                lines.append(title)
            rows = _resolve(component.get("rows"), data, scope)
            columns = fit_columns(component.get("columns") or [], rows)
            lines.append("| " + " | ".join(say(column["label"], scope) for column in columns) + " |")
            lines.append("| " + " | ".join("---" for _ in columns) + " |")
            for row in (rows if isinstance(rows, list) else [])[:200]:
                cells = [_shown(row.get(column["key"])) if isinstance(row, dict) else "" for column in columns]
                lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
        elif kind == "Metric":
            delta = say(component.get("delta"), scope)
            lines.append(f"{say(component['label'], scope)}: {_with_unit(component.get('value'), component, scope)}"
                         + (f" ({delta})" if delta else ""))
        elif kind == "ProgressBar":
            lines.append(f"{say(component['label'], scope)}: {_with_unit(component['value'], component, scope)} of "
                         f"{_with_unit(component['max'], component, scope)}")
        elif kind in {"Breakdown", "Timeline"}:
            items = _resolve(component.get("items"), data, scope)
            for item in (items if isinstance(items, list) else [])[:50]:
                if not isinstance(item, dict):
                    continue
                if kind == "Breakdown":
                    lines.append(f"{_shown(item.get('label'))}: {_shown(item.get('amount'))}")
                else:
                    line = " — ".join(part for part in (_shown(item.get("time")), _shown(item.get("title"))) if part)
                    lines.append(line + (f": {_shown(item.get('detail'))}" if item.get("detail") else ""))
        elif kind == "Code":
            lines.append(say(component.get("code"), scope))
        elif kind == "Tabs":
            for tab in component["tabs"]:
                lines.append(say(tab["title"], scope))
                walk(tab["child"], scope, depth + 1)
        elif kind == "Modal":
            walk(component["trigger"], scope, depth + 1)
        for child_key in ("child",):
            if child_key in component and kind not in {"Button"}:
                walk(component[child_key], scope, depth + 1)
        children = component.get("children")
        if isinstance(children, list):
            for child in children:
                walk(child, scope, depth + 1)
        elif isinstance(children, dict):
            items = read_path(data, children["path"], scope)
            for item in (items if isinstance(items, list) else [])[:200]:
                walk(children["componentId"], item, depth + 1)

    def _with_unit(value: Any, component: dict[str, Any], scope: Any) -> str:
        from row_bot.application.answer_components import with_unit

        resolved = _resolve(value, data, scope)
        unit = str(component.get("unit") or "")
        if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
            return with_unit(resolved, unit or None)
        return with_unit(_shown(resolved), unit or None) if _shown(resolved) else ""

    walk("root", None, 0)
    text = "\n".join(line for line in lines if line)
    return text[:MAX_TEXT_CHARS]


def _label(button: dict[str, Any], by_id: dict[str, dict[str, Any]], data: Any, scope: Any) -> str:
    """A button's words: its child Text (or the child's own child Text)."""

    child = by_id.get(button.get("child", ""))
    for _ in range(3):
        if child is None:
            return ""
        if child["component"] == "Text":
            return _shown(_resolve(child["text"], data, scope))[:80]
        nested = child.get("child") or next(iter(child.get("children") or []), None)
        child = by_id.get(nested) if isinstance(nested, str) else None
    return ""


def _under(path: str, keys: tuple[str, ...]) -> bool:
    return any(path == key or path.startswith(key + "/") for key in keys)


def _source_repeats(surface: dict[str, Any]) -> set[str]:
    """The components drawn inside a list repeated over one of the surface's sources."""

    by_id = {component["id"]: component for component in surface["components"]}
    keys = tuple(surface.get("sources") or {})
    inside: set[str] = set()

    def walk(key: str, base: str, private: bool, depth: int) -> None:
        component = by_id.get(key)
        if component is None or depth > MAX_DEPTH:
            return
        if private:
            inside.add(key)
        children = component.get("children")
        if isinstance(children, dict):
            path = children["path"]
            item_base = path if path.startswith("/") else f"{base.rstrip('/')}/{path}"
            walk(children["componentId"], item_base, private or _under(item_base, keys), depth + 1)
        for child in _references(component):
            if not (isinstance(children, dict) and child == children["componentId"]):
                walk(child, base, private, depth + 1)

    walk("root", "", False, 0)
    return inside


def reads_source(value: Any, keys: tuple[str, ...], in_source_repeat: bool) -> bool:
    """Whether a button's context value would carry something Row-Bot fills in on this computer.

    Deliberately wide (a privacy guard, same rule as the client's ``readsSource``): any mention of a
    source's path, in a binding, a function's arguments or a ``${...}``; and inside a list repeated
    over a source, any binding or ``${...}`` at all."""

    text = json.dumps(value, ensure_ascii=False)
    if any(re.search(re.escape(key) + r"(?![\w-])", text) for key in keys):
        return True
    return in_source_repeat and ("@path" in text or "${" in text)


def declared_actions(surface: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each action name a surface's buttons send, its words and the context keys it may carry.

    A key whose value reads one of the surface's sources is never one: those values stay on this
    computer, so a press can't take them to the model."""

    by_id = {component["id"]: component for component in surface["components"]}
    data = surface.get("dataModel") or {}
    keys = tuple(surface.get("sources") or {})
    repeated = _source_repeats(surface) if keys else set()
    found: dict[str, dict[str, Any]] = {}
    blocked: dict[str, set[str]] = {}
    for component in surface["components"]:
        event = component.get("action", {}).get("event") if component["component"] == "Button" else None
        if not event:
            continue
        entry = found.setdefault(event["name"], {"label": "", "context": set()})
        entry["label"] = entry["label"] or _label(component, by_id, data, None)
        for name, value in (event.get("context") or {}).items():
            private = reads_source(value, keys, component["id"] in repeated)
            (blocked.setdefault(event["name"], set()) if private else entry["context"]).add(name)
        if len(found) >= MAX_ACTIONS:
            break
    for name, entry in found.items():
        entry["context"] -= blocked.get(name, set())  # Private on any button with this name: never sent.
    return found


def chat_app_buttons(surface: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    """The buttons a chat app shows for a surface (G7): each one's words, action and context as drawn.

    A chat app shows the surface as words and can't edit it, so a button sends what the surface holds when
    drawn; a button repeated over a list is one button per item. Left out: links (the words carry them),
    buttons whose checks depend on what the person enters, and values Row-Bot fills in on this computer,
    which never go to the conversation from a chat app.
    """

    by_id = {component["id"]: component for component in surface["components"]}
    data = surface.get("dataModel") or {}
    private = tuple(surface.get("sources") or {})
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(key: str, scope: Any, depth: int) -> None:
        component = by_id.get(key)
        if component is None or depth > MAX_DEPTH or len(found) >= limit:
            return
        kind = component["component"]
        event = component.get("action", {}).get("event") if kind == "Button" else None
        if event and not component.get("@checks"):
            context = {name: _resolve(value, data, scope) for name, value in (event.get("context") or {}).items()
                       if not reads_source(value, private, False)}
            context = {name: value for name, value in context.items() if value is not None}
            label = _label(component, by_id, data, scope) or event["name"]
            fingerprint = json.dumps([event["name"], context], ensure_ascii=False, sort_keys=True, default=str)
            if fingerprint not in seen:
                seen.add(fingerprint)
                found.append({"name": event["name"], "label": label[:64], "context": context,
                              "component_id": component["id"]})
            return
        if kind == "Tabs":
            for tab in component["tabs"]:
                walk(tab["child"], scope, depth + 1)
        elif kind == "Modal":
            walk(component["content"], scope, depth + 1)
        elif "child" in component and kind != "Button":
            walk(component["child"], scope, depth + 1)
        children = component.get("children")
        if isinstance(children, list):
            for child in children:
                walk(child, scope, depth + 1)
        elif isinstance(children, dict):
            items = read_path(data, children["path"], scope)
            for item in (items if isinstance(items, list) else [])[:50]:
                walk(children["componentId"], item, depth + 1)

    walk("root", None, 0)
    return found


def parse(source: str) -> tuple[str, dict[str, Any]] | None:
    """The ``surface`` block a ```rowbot-ui body becomes, or None to keep it as a code block."""

    try:
        surface = read_surface(source)
    except (SurfaceError, RecursionError, TypeError):
        return None
    text = surface_text(surface)
    return "surface", {
        "surface_id": surface["surfaceId"],
        "title": surface["title"],
        "surface_json": json.dumps(surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        # Its words start with its title, unless the title is its own first words.
        "text": text if _named_by_first_line(surface["title"], text) else f"{surface['title']}\n{text}".strip(),
    }


def _reply_text(message: Any) -> str:
    content = message.content
    return content if isinstance(content, str) else "".join(
        str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text")


def surfaces_in(message: Any) -> list[dict[str, Any]]:
    from row_bot.application.answer_components import component_spans

    return [json.loads(fields["surface_json"]) for _start, _end, kind, fields in component_spans(_reply_text(message))
            if kind == "surface"]


def reply_surface(conversation_id: str, message_id: str, surface_id: str) -> dict[str, Any] | None:
    """The surface with this id that one reply of the conversation drew, or None."""

    from langchain_core.messages import AIMessage
    from row_bot import threads

    reply = next((message for message in threads.get_latest_checkpoint_messages(conversation_id)
                  if isinstance(message, AIMessage) and message.id == message_id), None)
    if reply is None:
        return None
    return next((surface for surface in surfaces_in(reply) if surface["surfaceId"] == surface_id), None)


def latest_surface(conversation_id: str, surface_id: str) -> tuple[str, dict[str, Any]] | None:
    """The latest reply that drew this surface in the conversation, and its surface: a redrawn one wins."""

    from langchain_core.messages import AIMessage
    from row_bot import threads

    for message in reversed(threads.get_latest_checkpoint_messages(conversation_id)):
        if isinstance(message, AIMessage) and message.id:
            found = [surface for surface in surfaces_in(message) if surface["surfaceId"] == surface_id]
            if found:
                return str(message.id), found[-1]
    return None


def state_key(message_id: str, surface_id: str) -> str:
    """Where what the person entered on a surface is kept (answer state): the same for the reply's copy in
    the chat and a pinned copy on Home, which both show that reply's surface."""

    import hashlib

    return "block:" + hashlib.sha256(f"surface\0{message_id}\0{surface_id}".encode("utf-8")).hexdigest()[:24]


def _named_by_first_line(title: str, text: str) -> bool:
    first = next((line for line in text.splitlines() if line.strip()), "")
    return " ".join(first.lstrip("#").replace("*", "").split())[:120] == title


def looks_like(value: Any) -> bool:
    """Whether an untagged JSON value is A2UI (an envelope, or a createSurface body with components)."""

    if isinstance(value, list):
        return bool(value) and all(looks_like(item) for item in value)
    if not isinstance(value, dict):
        return False
    if any(kind in value for kind in _MESSAGES):
        return True
    components = value.get("components")
    return (isinstance(components, list) and bool(components)
            and all(isinstance(item, dict) and "component" in item and "id" in item for item in components))


# ── The person's press on a button ────────────────────────────────────────────


def action_message(surface: dict[str, Any], name: str, context: Any, source_component_id: str,
                   timestamp: str) -> tuple[str, str]:
    """What the agent reads and what the chat shows when the person presses one of a surface's buttons.

    The action must be one the surface declares, its context only the keys that button declares, and the
    whole context small. The model reads A2UI's own ``action`` message; the chat shows the button's words.
    """

    declared = declared_actions(surface).get(name)
    if declared is None:
        raise SurfaceError("an action the surface doesn't have")
    if not isinstance(context, dict) or not set(context) <= declared["context"]:
        raise SurfaceError("a context the button doesn't send")
    context = {key: _value(value, "any") for key, value in context.items()}
    if len(json.dumps(context, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        raise SurfaceError("the context is too large")
    component = _identifier(source_component_id)[:64] or "button"
    label = declared["label"] or name
    message = {"version": VERSION, "action": {
        "name": name, "surfaceId": surface["surfaceId"], "sourceComponentId": component,
        "timestamp": timestamp, "context": context}}
    model = (f"[The person pressed “{label}” on the interface “{surface['title']}” ({surface['surfaceId']})]\n"
             "They pressed it in the interface you drew. It is their answer there, not approval of any tool call: "
             "anything that needs approval still asks. To change the interface, draw it again with the same "
             "surfaceId.\n\n"
             f"```json\n{json.dumps(message, ensure_ascii=False)}\n```")
    values = [f"{_key_words(key)}: {_shown(value)}" for key, value in context.items() if _shown(value)][:6]
    shown = "\n".join([f"{label} · {surface['title']}", *values])
    return model, shown
