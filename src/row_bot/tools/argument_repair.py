"""What a model meant by two argument slips that a tool's own check refused (S5, S6, 18 Nov).

Opus on the Claude plan sent ``seconds: "null"`` to browser_wait_for and ``run_at: "null"`` to task_update
for "leave it out", and ``arguments`` to tool_invoke as JSON text instead of an object; each call failed its
validation and the model had to try again by hand. Before a call runs, an optional argument that is exactly
"null" is left out, and an object argument given as the JSON text of an object is that object.
"""
from __future__ import annotations

import json
from typing import Any


def _takes_object(field: dict[str, Any]) -> bool:
    return any(kind.get("type") == "object" for kind in [field, *(field.get("anyOf") or [])])


def repaired_arguments(tool: Any, arguments: Any) -> Any:
    """*arguments* with those two slips undone, by *tool*'s input schema; anything else as it came."""
    if not isinstance(arguments, dict):
        return arguments
    try:
        schema = tool.get_input_jsonschema()
    except Exception:
        return arguments
    fields = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    repaired: dict[str, Any] = {}
    for name, value in arguments.items():
        field = fields.get(name)
        if isinstance(field, dict) and value == "null" and name not in required:
            continue
        if isinstance(field, dict) and isinstance(value, str) and _takes_object(field):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                repaired[name] = parsed
                continue
        repaired[name] = value
    return repaired
