"""Build the ignored Settings control ledger from paired DOM evidence.

The generated ledger is deliberately conservative: a NiceGUI control is only
marked ``matched`` when a semantic peer is present in the selected React DOM.
Per-control source/callback details can be supplied through an ignored JSON
override file without putting configured data in the repository.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / ".local/evidence/unified-client-platform/phase-4-settings-deep-parity"
DEFAULT_DOM = EVIDENCE / "metrics/before/nicegui"
DEFAULT_REACT_DOM = EVIDENCE / "metrics/before/react"
DEFAULT_OUTPUT = EVIDENCE / "control-inventory.md"
DEFAULT_OVERRIDES = EVIDENCE / "control-inventory-overrides.json"

OWNERS = {
    "Providers": (
        "src/row_bot/ui/settings.py",
        2930,
        "_build_cloud_tab",
        "ProviderStatus.tsx + ProviderSettingsPanel.tsx",
    ),
    "Models": (
        "src/row_bot/ui/settings.py",
        2859,
        "_build_models_tab",
        "Catalog.tsx + DefaultModelSettings.tsx",
    ),
    "Knowledge": (
        "src/row_bot/ui/settings.py",
        4649,
        "_build_knowledge_tab",
        "KnowledgeCatalog.tsx + KnowledgeEditors.tsx",
    ),
    "Buddy": (
        "src/row_bot/ui/buddy.py",
        1,
        "build_buddy_settings_tab",
        "BuddyControls.tsx + BuddyHatch.tsx",
    ),
    "Voice": (
        "src/row_bot/ui/settings.py",
        5488,
        "_build_voice_tab",
        "VoiceSettings.tsx",
    ),
    "System": (
        "src/row_bot/ui/settings.py",
        3531,
        "_build_system_access_tab",
        "SystemSettings.tsx",
    ),
    "Tracker": (
        "src/row_bot/ui/settings.py",
        4547,
        "_build_tracker_tab",
        "TrackerSettings.tsx",
    ),
    "Documents": (
        "src/row_bot/ui/settings.py",
        1056,
        "_build_documents_tab",
        "DocumentSettings.tsx",
    ),
    "Tools": (
        "src/row_bot/ui/settings.py",
        3365,
        "_build_tools_tab",
        "ToolSettings.tsx",
    ),
    "Skills": (
        "src/row_bot/ui/settings.py",
        2945,
        "_build_skills_tab",
        "SkillsSettings.tsx",
    ),
    "Accounts": (
        "src/row_bot/ui/settings.py",
        4248,
        "_build_accounts_tab",
        "AccountSettings.tsx",
    ),
    "Channels": (
        "src/row_bot/ui/settings.py",
        5846,
        "_build_channels_tab",
        "ChannelSettings.tsx",
    ),
    "Utilities": (
        "src/row_bot/ui/settings.py",
        4507,
        "_build_utilities_tab",
        "UtilitySettings.tsx",
    ),
    "MCP": (
        "src/row_bot/ui/mcp_settings.py",
        1,
        "build_mcp_settings_tab",
        "McpSettings.tsx",
    ),
    "Plugins": (
        "src/row_bot/ui/settings.py",
        6184,
        "_build_plugins_tab",
        "PluginSettings.tsx",
    ),
    "Preferences": (
        "src/row_bot/ui/settings.py",
        6223,
        "_build_preferences_tab",
        "Preferences.tsx",
    ),
}

TESTS = {
    page: f"settings-deep-parity.spec.ts::{page}; focused {page} component/API tests"
    for page in OWNERS
}


def _cell(value: object) -> str:
    return (
        str(value if value is not None else "")
        .replace("|", "\\|")
        .replace("\n", " ")
        .strip()
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:64] or "unnamed"


def _control_kind(control: dict[str, Any]) -> str:
    role = str(control.get("role") or "").strip()
    kind = str(control.get("type") or "").strip()
    tag = str(control.get("tag") or "").strip()
    if role in {"switch", "checkbox", "radio", "button", "combobox"}:
        return role
    if tag == "details":
        return "disclosure"
    if tag == "summary":
        return "disclosure action"
    if tag == "a":
        return "link"
    if tag in {"select", "textarea"}:
        return tag
    return kind or tag or "control"


def _semantic_text(control: dict[str, Any], ordinal: int) -> str:
    return str(
        control.get("label")
        or control.get("tooltip")
        or control.get("icon")
        or control.get("name")
        or f"unnamed {ordinal}"
    ).strip()


def _semantic_signature(control: dict[str, Any], ordinal: int) -> tuple[str, str]:
    text = _semantic_text(control, ordinal)
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    return _control_kind(control), normalized


def _behavior(control: dict[str, Any]) -> tuple[str, str]:
    kind = _control_kind(control)
    label = _semantic_text(control, int(control.get("index") or 0) + 1).casefold()
    if kind in {"switch", "checkbox", "radio"}:
        return (
            "persisted setting or runtime policy",
            "toggle; persist/review semantics follow the frozen owner callback",
        )
    if kind in {"text", "password", "number", "select", "combobox", "textarea"}:
        return (
            "persisted configuration or local draft",
            "edit local draft; explicit owner action commits where required",
        )
    if "close" in label:
        return (
            "Settings navigation",
            "close NiceGUI dialog; React uses routed back/close navigation",
        )
    if any(
        word in label
        for word in (
            "save",
            "apply",
            "authenticate",
            "install",
            "delete",
            "clear",
            "remove",
            "rebuild",
            "download",
        )
    ):
        return (
            "owner callback and durable receipt where required",
            "consequential owner action; exercise with synthetic fixture only",
        )
    if any(
        word in label
        for word in (
            "refresh",
            "check",
            "browse",
            "open",
            "show",
            "copy",
            "review",
            "test",
        )
    ):
        return (
            "cached state or explicit probe result",
            "explicit observation/action callback; probe and OS paths use synthetic fixtures",
        )
    return (
        "owner state or cached runtime truth",
        "owner callback, disclosure, or navigation; no callback on render",
    )


def _load_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _react_signatures(path: Path) -> Counter[tuple[str, str]]:
    payload = _load_json(path, {})
    return Counter(
        _semantic_signature(control, ordinal)
        for ordinal, control in enumerate(payload.get("controls") or [], 1)
    )


def _evidence_paths(page: str, react_dom_dir: Path) -> str:
    slug = page.casefold()
    try:
        react_metric = (
            react_dom_dir.resolve().relative_to(EVIDENCE.resolve()).as_posix()
        )
    except ValueError:
        react_metric = str(react_dom_dir)
    return (
        f"reference/nicegui/{slug}-desktop-full.png; metrics/before/nicegui/{slug}-desktop-dom.json; "
        f"{react_metric}/{slug}-desktop-dom.json; paired desktop/phone gallery"
    )


def build(
    dom_dir: Path,
    react_dom_dir: Path,
    output: Path,
    overrides_path: Path | None = None,
) -> int:
    overrides: dict[str, dict[str, Any]] = _load_json(overrides_path, {})
    rows: list[str] = []
    page_counts: list[tuple[str, int]] = []
    dispositions: Counter[str] = Counter()
    for page, (source, line, owner, react_owner) in OWNERS.items():
        path = dom_dir / f"{page.casefold()}-desktop-dom.json"
        payload = _load_json(path, {})
        controls = payload.get("controls") or []
        page_counts.append((page, len(controls)))
        semantic_counts: Counter[str] = Counter()
        react_signatures = _react_signatures(
            react_dom_dir / f"{page.casefold()}-desktop-dom.json"
        )
        for ordinal, control in enumerate(controls, 1):
            label = _semantic_text(control, ordinal)
            kind, normalized = _semantic_signature(control, ordinal)
            semantic_base = f"{_slug(label)}-{_slug(kind)}"
            semantic_counts[semantic_base] += 1
            stable_key = f"{page}-{semantic_base}-{semantic_counts[semantic_base]:02d}"
            state, behavior = _behavior(control)
            if react_signatures[(kind, normalized)] > 0:
                react_signatures[(kind, normalized)] -= 1
                disposition = "matched"
                missing_note = ""
            elif "close" in normalized:
                disposition = "matched-with-accessibility-adaptation"
                missing_note = "routed React back/close affordance"
            else:
                disposition = "blocked"
                missing_note = "no semantic control peer in selected React DOM evidence"
            section = control.get("section") or "Settings pane"
            rect = control.get("rect") or {}
            style = control.get("style") or {}
            context = (
                f"{section}; DOM order {ordinal}; "
                f"{'visible' if control.get('visible') else 'hidden/conditional'}; "
                f"{rect.get('width', 0):.0f}x{rect.get('height', 0):.0f}; "
                f"font {style.get('fontSize', 'unmeasured')}, radius {style.get('borderRadius', 'unmeasured')}; "
                "desktop row/section placement with phone reflow measured in paired capture"
            )
            reference = label
            if control.get("icon"):
                reference += f"; icon `{control['icon']}` in-control"
            if control.get("tooltip"):
                reference += f"; tooltip `{control['tooltip']}`"
            row: dict[str, Any] = {
                "page_key": stable_key,
                "owner": f"{source}:{line} `{owner}` (frozen page builder)",
                "reference": reference,
                "kind": kind,
                "context": context,
                "state": state,
                "behavior": behavior,
                "react_owner": react_owner
                + (f"; {missing_note}" if missing_note else ""),
                "disposition": disposition,
                "tests": TESTS[page],
                "evidence": _evidence_paths(page, react_dom_dir),
            }
            row.update(overrides.get(stable_key, {}))
            final_disposition = str(row["disposition"])
            if final_disposition not in {
                "matched",
                "matched-with-accessibility-adaptation",
                "mapped-to-existing-automatic-owner",
                "blocked",
            }:
                raise ValueError(
                    f"invalid disposition for {stable_key}: {final_disposition}"
                )
            dispositions[final_disposition] += 1
            rows.append(
                "| " + " | ".join(_cell(value) for value in row.values()) + " |"
            )

    total = len(rows)
    summary = "\n".join(f"- {page}: {count}" for page, count in page_counts)
    disposition_summary = ", ".join(
        f"{name}={count}" for name, count in sorted(dispositions.items())
    )
    body = f"""# NiceGUI Settings control inventory

Generated from authorized real-data NiceGUI desktop DOM evidence and the selected React DOM evidence. Dynamic element IDs and field values are excluded; secret-shaped text is masked by the capture runner. Hidden and conditional controls present in the rendered DOM remain inventoried. Page-builder locations identify the frozen reference owner; exact helper/callback refinements belong in `{DEFAULT_OVERRIDES.relative_to(ROOT).as_posix()}`.

Inventory status: **{total} rendered controls inventoried** ({disposition_summary}). A `blocked` row names the missing semantic React peer and is not a parity claim; zero-unreviewed status is reached only when every final disposition and override has been inspected.

{summary}

| Page and stable control key | NiceGUI owner | Reference label and icon | Control type | Visual context | State source | Behaviour | React owner | Disposition | Tests | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
{"\n".join(rows)}
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dom-dir", type=Path, default=DEFAULT_DOM)
    parser.add_argument("--react-dom-dir", type=Path, default=DEFAULT_REACT_DOM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    args = parser.parse_args()
    count = build(args.dom_dir, args.react_dom_dir, args.output, args.overrides)
    print(f"wrote {count} controls to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
