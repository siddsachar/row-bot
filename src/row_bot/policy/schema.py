"""The JSON Schemas (draft 2020-12) of a company policy and a group override, and the settings table, generated
from ``row_bot.policy.model`` so they can't drift from what the parser accepts."""

from __future__ import annotations

from typing import Any

from .model import (
    DEFAULT_POLICY, MAX_LIST_ITEMS, MAX_VERSION, SECTIONS, Spec, document_key, lockdown_policy, specs,
)

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_WIDEN = {
    "any": "on if the company or any of the person's groups turns it on",
    "max": "the most permissive of the company's and the groups' values",
    "union": "every entry the company or any of the person's groups allows",
    "remove": "the company's list, less every entry any of the person's groups gives back",
}


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, frozenset):
        return ", ".join(sorted(value)) if value else "[]"
    return str(value)


def _default(section: str, name: str) -> Any:
    return getattr(getattr(DEFAULT_POLICY, section), name)


def _lockdown(section: str, name: str) -> Any:
    return getattr(getattr(lockdown_policy(), section), name)


def settings_table() -> str:
    """Every setting with its default, its group-override rule and its lockdown value, as a Markdown table."""
    rows = ["| Setting | Default | In a group override | Lockdown |", "| --- | --- | --- | --- |"]
    for section in SECTIONS:
        for name, spec in specs(section).items():
            key = document_key(spec, name, override=True)
            rule = (f"`{section}.{key}`: " if key != name else "") + _WIDEN[spec.widen]
            rows.append(f"| `{section}.{name}` | {_show(_default(section, name))} | {rule} | "
                        f"{_show(_lockdown(section, name))} |")
    return "\n".join(rows)


def _property(section: str, name: str, spec: Spec, *, override: bool) -> dict[str, Any]:
    default = _default(section, name)
    if override:
        description = f"{spec.help} Merged with the company policy: {_WIDEN[spec.widen]}."
        if spec.override_key:
            description = (f"Entries of the company's {section}.{name} given back to this group. {spec.help}")
    else:
        description = f"{spec.help} Default: {_show(default)}."
    schema: dict[str, Any] = {"title": spec.title, "description": description}
    if not override:
        schema["default"] = sorted(default) if isinstance(default, frozenset) else default
    if spec.kind == "bool":
        schema["type"] = "boolean"
    elif spec.kind == "int":
        schema.update(type="integer", minimum=0, maximum=spec.maximum)
    elif spec.kind == "enum":
        schema.update(type="string", enum=list(spec.choices))
    else:
        schema.update(type="array", maxItems=MAX_LIST_ITEMS,
                      items={"type": "string", "pattern": f"^(?:{spec.pattern})$"})
    return schema


def json_schema(*, override: bool = False) -> dict[str, Any]:
    """The JSON Schema of a company policy (and of the effective policy, which sets every setting), or of a
    group override."""
    properties: dict[str, Any] = {
        "version": {"title": "Version", "description": "The server's version of this document. On a write, "
                                                        "the version it replaces (refused when that is no "
                                                        "longer the newest).",
                    "type": "integer", "minimum": 0, "maximum": MAX_VERSION},
    }
    for section, cls in SECTIONS.items():
        properties[section] = {
            "title": section.capitalize(),
            "description": (cls.__doc__ or "").strip(),
            "type": "object",
            "additionalProperties": False,
            "properties": {document_key(spec, name, override=override): _property(section, name, spec,
                                                                                    override=override)
                           for name, spec in specs(section).items()},
        }
    if override:
        title = "Row-Bot group override"
        description = ("What one company group gets on top of the company policy. An override can only widen: "
                       "each setting it sets is merged with the company's, and the more permissive value wins. "
                       "A person in several groups gets the union.\n\n" + settings_table())
    else:
        title = "Row-Bot company policy"
        description = ("The company's policy for every connected Row-Bot. A setting it leaves out takes its "
                       "default. Unknown keys are refused.\n\n" + settings_table())
    return {
        "$schema": SCHEMA_DIALECT,
        "title": title,
        "description": description,
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }


__all__ = ["SCHEMA_DIALECT", "json_schema", "settings_table"]
