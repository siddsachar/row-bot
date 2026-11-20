"""Dry-run migration planners for supported source providers.

Each planned item names the Row-Bot store it lands in (``details["store"]``, see
:mod:`row_bot.migration.stores`) or is a skill or key copied as before. What can't be
brought over safely is listed for manual review with the reason, never written to a
side file nothing reads.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from row_bot.migration import stores
from row_bot.migration.core import (
    ConflictPolicy,
    MigrationAction,
    MigrationCategory,
    MigrationItem,
    MigrationPlan,
    MigrationProvider,
    MigrationSensitivity,
    MigrationSource,
    MigrationStatus,
    make_item_id,
    normalize_provider,
)
from row_bot.migration.detection import MigrationScan, detect_hermes_source, detect_openclaw_source, detect_source

try:
    import yaml
except Exception:  # pragma: no cover - optional parser, planner falls back to file-only items
    yaml = None


DEFAULT_TARGET_ROOT = Path.home() / ".row-bot" / "migration-preview"
_SAFE_NAME_RE = re.compile(r"[^a-z0-9_.-]+")
# Import memories takes 20 files at a time; MEMORY.md and USER.md come first.
_DAILY_NOTES_KEPT = 18
_ELSEWHERE = "Leave Target folder empty to bring them into this Row-Bot."
# Scheduled jobs (Hermes's cron folder, OpenClaw's cron store) have no converter.
_CRON = frozenset({"cron", "cron-store.json"})
CRON_NOTE = "Cron jobs aren't converted: make a workflow for each one in Workflows."


@dataclass(frozen=True)
class _App:
    provider: MigrationProvider
    label: str
    source_root: Path
    target: Path
    live: bool  # the target is the data folder this Row-Bot runs on

    @property
    def id(self) -> str:
        return self.provider.value


def build_migration_plan(
    provider: str | MigrationProvider,
    root: str | Path | None = None,
    *,
    target_root: str | Path | None = None,
    include_secrets: bool = False,
) -> MigrationPlan:
    """Build a preview-only migration plan for *provider* without mutating anything."""
    normalized = normalize_provider(provider)
    if normalized == MigrationProvider.HERMES:
        return build_hermes_plan(root, target_root=target_root, include_secrets=include_secrets)
    if normalized == MigrationProvider.OPENCLAW:
        return build_openclaw_plan(root, target_root=target_root, include_secrets=include_secrets)

    scan = detect_source(normalized, root)
    return MigrationPlan.empty(scan.source, "Unsupported migration provider; nothing was planned.")


def build_hermes_plan(
    root: str | Path | None = None,
    *,
    target_root: str | Path | None = None,
    include_secrets: bool = False,
) -> MigrationPlan:
    scan = detect_hermes_source(root)
    target = _target_root(target_root)
    if not scan.source.found:
        return _empty_for_missing(scan.source, "Hermes state was not found; nothing was planned.")

    app = _App(MigrationProvider.HERMES, "Hermes", scan.source.root, target, stores.is_live_data_dir(target))
    warnings: list[str] = list(scan.source.warnings)
    config_path = app.source_root / "config.yaml"
    config = _parse_yaml_object(config_path, warnings, "Hermes config.yaml")
    servers = _hermes_model_servers(config)
    items: list[MigrationItem] = []

    items.extend(_model_items(app, config_path, _hermes_model(config), servers))
    items.extend(_model_server_items(app, config_path, servers))
    for server_name, server_config in _record_items(_child_record(config, "mcp_servers")):
        items.append(_mcp_item(app, server_name, server_config, config_path))
    items.extend(_personality_items(app, "SOUL.md"))
    items.extend(_instructions_items(app, "AGENTS.md"))
    items.extend(_memory_items(app, (
        ("memories/MEMORY.md", "Memories from MEMORY.md"),
        ("memories/USER.md", "About you, from USER.md"),
    )))
    chats = _chats_items(app, "state.db")
    items.extend(chats)
    items.extend(_skill_items(app.source_root / "skills", target / "skills", MigrationProvider.HERMES))
    items.extend(_secret_items(app.source_root / ".env", target, include_secrets=include_secrets))
    # A database copied into the report is no use to read; the chats item brings its chats instead.
    items.extend(_archive_items(scan, target / "archive" / MigrationProvider.HERMES.value,
                                leave_out=frozenset({"state.db"}) if chats else frozenset()))

    return _plan_from_items(scan, items, warnings, target, include_secrets)


def build_openclaw_plan(
    root: str | Path | None = None,
    *,
    target_root: str | Path | None = None,
    include_secrets: bool = False,
) -> MigrationPlan:
    scan = detect_openclaw_source(root)
    target = _target_root(target_root)
    if not scan.source.found:
        return _empty_for_missing(scan.source, "OpenClaw state was not found; nothing was planned.")

    app = _App(MigrationProvider.OPENCLAW, "OpenClaw", scan.source.root, target, stores.is_live_data_dir(target))
    warnings: list[str] = list(scan.source.warnings)
    config_path = app.source_root / "openclaw.json"
    config = _parse_json_object(config_path, warnings, "OpenClaw openclaw.json")
    servers = _openclaw_model_servers(config)
    items: list[MigrationItem] = []

    items.extend(_model_items(app, config_path, _openclaw_model(config), servers))
    items.extend(_model_server_items(app, config_path, servers))
    for server_name, server_config in _record_items(_child_record(_child_record(config, "mcp"), "servers")):
        items.append(_mcp_item(app, server_name, server_config, config_path))

    for channel_name, channel_config in _record_items(_child_record(config, "channels")):
        items.append(
            _manual_review_item(
                id=make_item_id(MigrationCategory.CHANNELS, channel_name),
                category=MigrationCategory.CHANNELS,
                source=config_path,
                target=target / f"config/channels.json#{channel_name}",
                label=f"Channel: {channel_name}",
                reason="Channel settings require manual review before activation.",
                details={"channel": channel_name, "keys": sorted(str(key) for key in _as_record(channel_config))},
                sensitivity=MigrationSensitivity.SENSITIVE,
            )
        )

    items.extend(_personality_items(app, "workspace/SOUL.md"))
    items.extend(_instructions_items(app, "workspace/AGENTS.md"))
    items.extend(_memory_items(app, (
        ("workspace/MEMORY.md", "Memories from MEMORY.md"),
        ("workspace/USER.md", "About you, from USER.md"),
    )))
    items.extend(_daily_notes_items(app, "workspace/memory"))
    items.extend(_skill_items(app.source_root / "workspace" / "skills", target / "skills", MigrationProvider.OPENCLAW))
    items.extend(_skill_items(app.source_root / "skills", target / "skills", MigrationProvider.OPENCLAW, prefix="shared-skill"))
    items.extend(_secret_items(app.source_root / ".env", target, include_secrets=include_secrets))

    for section in ("approvals", "browser", "tools", "cron", "hooks", "memory"):
        if section in config:
            items.append(
                _manual_review_item(
                    id=make_item_id(MigrationCategory.SETTINGS, section),
                    category=MigrationCategory.SETTINGS,
                    source=config_path,
                    target=target / f"config/{section}.json",
                    label=f"OpenClaw {section}",
                    reason=f"OpenClaw {section} settings require manual review before activation.",
                    details={"section": section},
                    sensitivity=MigrationSensitivity.RISKY if section in {"approvals", "tools", "hooks"} else MigrationSensitivity.SENSITIVE,
                )
            )

    items.extend(_archive_items(scan, target / "archive" / MigrationProvider.OPENCLAW.value))

    return _plan_from_items(scan, items, warnings, target, include_secrets)


def _target_root(value: str | Path | None) -> Path:
    return Path(value).expanduser() if value is not None else DEFAULT_TARGET_ROOT


def _empty_for_missing(source: MigrationSource, warning: str) -> MigrationPlan:
    warnings = tuple(dict.fromkeys((*source.warnings, warning)))
    return MigrationPlan(source=source, warnings=warnings, metadata={"mode": "dry_run", "read_only": True})


def _plan_from_items(
    scan: MigrationScan,
    items: list[MigrationItem],
    warnings: list[str],
    target_root: Path,
    include_secrets: bool,
) -> MigrationPlan:
    if any(entry.relative_path in _CRON for entry in scan.entries):
        warnings.append(CRON_NOTE)
    if any(item.status == MigrationStatus.CONFLICT for item in items):
        warnings.append("Some items would replace what you already have. Turn on Replace what you already have to bring them over.")
    return MigrationPlan(
        source=scan.source,
        items=tuple(items),
        warnings=tuple(dict.fromkeys(warnings)),
        metadata={
            "mode": "dry_run",
            "read_only": True,
            "target_root": str(target_root),
            "include_secrets": include_secrets,
            "scan_summary": scan.summary.to_dict(),
        },
    )


def _planned_item(
    *,
    id: str,
    category: MigrationCategory,
    action: MigrationAction,
    source: str | Path | None,
    target: str | Path | None,
    label: str,
    details: dict[str, Any] | None = None,
    sensitivity: MigrationSensitivity = MigrationSensitivity.NORMAL,
    selected: bool = True,
) -> MigrationItem:
    status = _target_status(target)
    return MigrationItem(
        id=id,
        category=category,
        action=action,
        status=status,
        source=source,
        target=target,
        label=label,
        reason="target already exists" if status == MigrationStatus.CONFLICT else "",
        details=details or {},
        sensitivity=sensitivity,
        conflict_policy=ConflictPolicy.REFUSE,
        selected=selected and status != MigrationStatus.CONFLICT,
    )


def _store_item(
    *,
    id: str,
    category: MigrationCategory,
    action: MigrationAction,
    status: MigrationStatus,
    source: Path,
    target: Path | str,
    label: str,
    reason: str,
    store: str,
    details: dict[str, Any] | None = None,
    sensitivity: MigrationSensitivity = MigrationSensitivity.NORMAL,
    selected: bool = True,
    requires_confirmation: bool = False,
) -> MigrationItem:
    """An item that lands in one of Row-Bot's own stores; only a planned one starts ticked."""
    return MigrationItem(
        id=id,
        category=category,
        action=action,
        status=status,
        source=source,
        target=target,
        label=label,
        reason=reason,
        details={**(details or {}), "store": store, "place": stores.PLACES[store]},
        sensitivity=sensitivity,
        conflict_policy=ConflictPolicy.REFUSE,
        selected=selected and status == MigrationStatus.PLANNED,
        requires_confirmation=requires_confirmation,
    )


def _manual_review_item(
    *,
    id: str,
    category: MigrationCategory,
    source: str | Path | None,
    target: str | Path | None,
    label: str,
    reason: str,
    details: dict[str, Any],
    sensitivity: MigrationSensitivity,
) -> MigrationItem:
    return MigrationItem(
        id=id,
        category=category,
        action=MigrationAction.MANUAL_REVIEW,
        status=MigrationStatus.SKIPPED,
        source=source,
        target=target,
        label=label,
        reason=reason,
        details=details,
        sensitivity=sensitivity,
        selected=False,
        requires_confirmation=True,
    )


def _not_brought_over(id: str, category: MigrationCategory, source: Path, label: str, reason: str) -> MigrationItem:
    return _manual_review_item(id=id, category=category, source=source, target=None, label=label, reason=reason,
                               details={}, sensitivity=MigrationSensitivity.NORMAL)


# Default model and model servers ----------------------------------------------------------

def _model_items(
    app: _App,
    config_path: Path,
    model: tuple[str | None, str] | None,
    servers: list[tuple[str, dict[str, Any]]],
) -> list[MigrationItem]:
    if model is None:
        return []
    provider_name, model_id = model
    shown = f"{provider_name} › {model_id}" if provider_name else model_id
    label = f"Default model: {shown}"
    provider_id = stores.row_bot_provider(provider_name)
    names = {name.lower(): name for name, _record in servers}
    if provider_id is None and provider_name and provider_name.lower() in names:
        provider_id = stores.endpoint_provider(names[provider_name.lower()])
    if provider_id is None:
        why = (f"Row-Bot doesn't connect to {provider_name}" if provider_name
               else "it doesn't say which provider it uses")
        return [_not_brought_over("model:default", MigrationCategory.MODEL, config_path, label,
                                  f"Not brought over: {why}. Choose a model in "
                                  f"{stores.PLACES[stores.DEFAULT_MODEL]}.")]
    reference = stores.model_reference(provider_id, model_id)
    current = stores.current_default_model(app.target)
    if current == reference:
        status, reason = MigrationStatus.SKIPPED, "Already your main model."
    elif current:
        status, reason = MigrationStatus.CONFLICT, "Replaces your current main model (a backup is kept first)."
    else:
        status, reason = MigrationStatus.PLANNED, "Becomes the main model for new chats once its provider is connected."
    return [_store_item(id="model:default", category=MigrationCategory.MODEL, action=MigrationAction.UPDATE,
                        status=status, source=config_path, target=app.target / "model_settings.json#model",
                        label=label, reason=reason, store=stores.DEFAULT_MODEL, details={"model": reference})]


def _model_server_items(app: _App, config_path: Path, servers: list[tuple[str, dict[str, Any]]]) -> list[MigrationItem]:
    existing = stores.custom_endpoint_ids(app.target)
    items: list[MigrationItem] = []
    for name, record in servers:
        endpoint_id = stores.endpoint_id(name)
        item_id = make_item_id("providers", endpoint_id)
        label = f"Model server: {name}"
        address = stores.endpoint_address(record.get("base_url") or record.get("baseUrl") or record.get("url"))
        if address is None:
            items.append(_not_brought_over(item_id, MigrationCategory.MODEL, config_path, label,
                                           "Not brought over: its address isn't one Row-Bot can save. Add it in "
                                           f"{stores.PLACES[stores.CUSTOM_ENDPOINT]}."))
            continue
        url, local = address
        needs_key = any(record.get(key) for key in ("api_key", "api_key_env", "apiKey", "key_env"))
        if endpoint_id in existing:
            status, reason = MigrationStatus.SKIPPED, "A custom endpoint with this name is already set up; yours stays as it is."
        else:
            status = MigrationStatus.PLANNED
            reason = (f"Added as a custom endpoint at {url}{' (this computer)' if local else ''}. "
                      "Row-Bot contacts it only when you use it.")
            if needs_key:
                reason += " Add its key there; keys come over only with Include API keys."
        items.append(_store_item(
            id=item_id, category=MigrationCategory.MODEL, action=MigrationAction.CREATE, status=status,
            source=config_path, target=app.target / f"providers.json#{endpoint_id}", label=label, reason=reason,
            store=stores.CUSTOM_ENDPOINT, sensitivity=MigrationSensitivity.SENSITIVE,
            details={"endpoint": {"id": endpoint_id, "name": name, "base_url": url, "auth_required": needs_key,
                                  "execution_location": "local" if local else "remote"}},
        ))
    return items


def _hermes_model(config: Mapping[str, Any]) -> tuple[str | None, str] | None:
    model = config.get("model")
    record = _as_record(model)
    if isinstance(model, str) and model.strip():
        model_id, provider = model.strip(), _string(config.get("provider"))
    else:
        model_id = _string(record.get("default")) or _string(record.get("model"))
        provider = _string(record.get("provider")) or _string(config.get("provider"))
        if not model_id:
            model_id = _string(config.get("default_model")) or _string(config.get("model_name"))
    return _split_model(provider, model_id)


def _openclaw_model(config: Mapping[str, Any]) -> tuple[str | None, str] | None:
    defaults = _child_record(_child_record(config, "agents"), "defaults")
    model = defaults.get("model") or config.get("model")
    if isinstance(model, str):
        return _split_model(None, _string(model))
    record = _as_record(model)
    return _split_model(None, _string(record.get("primary")) or _string(record.get("default")) or _string(record.get("model")))


def _split_model(provider: str | None, model_id: str | None) -> tuple[str | None, str] | None:
    """``(provider, model)``; a model written ``provider/model`` names its provider first."""
    if not model_id:
        return None
    if not provider and "/" in model_id:
        provider, model_id = model_id.split("/", 1)
    return provider, model_id


def _hermes_model_servers(config: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Hermes's own model servers: ``custom_providers``, and ``providers`` Row-Bot has no provider for."""
    servers: dict[str, dict[str, Any]] = {}
    for name, record in _record_items(_child_record(config, "providers")):
        if stores.row_bot_provider(name) is None and isinstance(record, Mapping):
            servers.setdefault(str(name), dict(record))
    custom = config.get("custom_providers")
    for entry in custom if isinstance(custom, list) else []:
        record = _as_record(entry)
        name = _string(record.get("name")) or _string(record.get("id"))
        if name:
            servers.setdefault(name, record)
    return sorted(servers.items())


def _openclaw_model_servers(config: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    providers = _child_record(_child_record(config, "models"), "providers")
    return [(str(name), dict(record)) for name, record in _record_items(providers)
            if stores.row_bot_provider(name) is None and isinstance(record, Mapping)]


# MCP servers ---------------------------------------------------------------------------------

def _mcp_item(app: _App, name: str, raw: Any, config_path: Path) -> MigrationItem:
    item_id = make_item_id(MigrationCategory.MCP, name)
    label = f"MCP server: {name}"
    server, needs = stores.mcp_server(raw)
    if server is None:
        return _not_brought_over(item_id, MigrationCategory.MCP, config_path, label,
                                 "Not brought over: it has no command or address Row-Bot can run.")
    if not app.live:
        return _not_brought_over(item_id, MigrationCategory.MCP, config_path, label,
                                 f"MCP servers are added only to this Row-Bot's own data folder. {_ELSEWHERE}")
    if name in stores.mcp_server_names(app.target):
        status, reason = MigrationStatus.SKIPPED, f"An app named {name} is already set up; it stays as it is."
    else:
        status = MigrationStatus.PLANNED
        reason = f"Added turned off. Check it in {stores.PLACES[stores.MCP_SERVER]}, then turn it on."
        if needs:
            reason += f" Fill in {', '.join(needs)} there first; their values aren't copied."
    return _store_item(
        id=item_id, category=MigrationCategory.MCP, action=MigrationAction.CREATE, status=status,
        source=config_path, target=app.target / f"mcp_servers.json#{name}", label=label, reason=reason,
        store=stores.MCP_SERVER, details={"server": server, "needs": needs},
        sensitivity=MigrationSensitivity.SENSITIVE, selected=False, requires_confirmation=True,
    )


# Persona and agent instructions --------------------------------------------------------------

def _personality_items(app: _App, relative: str) -> list[MigrationItem]:
    path = app.source_root / relative
    if not path.is_file():
        return []
    label = f"Personality from {path.name}"
    text, shortened = stores.personality_from(_read_text(path))
    if not text:
        return [_not_brought_over("identity:personality", MigrationCategory.IDENTITY, path, label,
                                  f"Not brought over: {path.name} has no lines Row-Bot can use as a personality.")]
    current = stores.current_personality(app.target)
    quoted = f"“{text}”" + (" (shortened to fit)" if shortened else "")
    if current == text:
        status, reason = MigrationStatus.SKIPPED, "Already your personality."
    elif current:
        status, reason = MigrationStatus.CONFLICT, f"Replaces your current personality with {quoted}"
    else:
        status, reason = MigrationStatus.PLANNED, f"Becomes Row-Bot's personality: {quoted}"
    return [_store_item(id="identity:personality", category=MigrationCategory.IDENTITY, action=MigrationAction.UPDATE,
                        status=status, source=path, target=app.target / "user_config.json#identity.personality",
                        label=label, reason=reason, store=stores.PERSONALITY, details={"personality": text})]


def _instructions_items(app: _App, relative: str) -> list[MigrationItem]:
    path = app.source_root / relative
    if not path.is_file() or not stores.instructions_skill(_read_text(path), app.id, app.label, path.name):
        return []
    name = stores.instructions_skill_name(app.id)
    target = app.target / "skills" / name / "SKILL.md"
    exists = target.exists()
    return [_store_item(
        id=make_item_id(MigrationCategory.SKILLS, name), category=MigrationCategory.SKILLS,
        action=MigrationAction.CREATE, status=MigrationStatus.CONFLICT if exists else MigrationStatus.PLANNED,
        source=path, target=target, label=f"{path.name} as a skill: Instructions from {app.label}",
        reason=("A skill with this name already exists." if exists else
                "A skill you use when you choose; it isn't pinned, so chats don't follow it on their own."),
        store=stores.INSTRUCTIONS_SKILL, details={"skill": name, "app": app.id, "app_label": app.label},
    )]


# Memories ------------------------------------------------------------------------------------

def _memory_item(app: _App, item_id: str, source: Path, files: list[Path], label: str) -> MigrationItem | None:
    try:
        count = stores.count_memories(files)
    except Exception:
        count = 0
    if count == 0:
        return None
    if not app.live:
        return _not_brought_over(item_id, MigrationCategory.MEMORIES, source, label,
                                 f"Memories are reviewed into this Row-Bot's own memory. {_ELSEWHERE}")
    entries = f"{count} {'entry' if count == 1 else 'entries'}"
    return _store_item(
        id=item_id, category=MigrationCategory.MEMORIES, action=MigrationAction.CREATE, status=MigrationStatus.PLANNED,
        source=source, target=app.target / "memory_import",
        label=f"{label}: {entries}, you'll review them before they're saved",
        reason=f"They open in {stores.PLACES[stores.MEMORY_REVIEW]} to check; nothing is saved to memory until you do.",
        store=stores.MEMORY_REVIEW,
        details={"files": [str(path.relative_to(app.source_root)).replace("\\", "/") for path in files], "count": count},
    )


def _memory_items(app: _App, mappings: tuple[tuple[str, str], ...]) -> list[MigrationItem]:
    items = []
    for relative, label in mappings:
        path = app.source_root / relative
        if path.is_file():
            item = _memory_item(app, make_item_id(MigrationCategory.MEMORIES, path.name), path, [path], label)
            if item is not None:
                items.append(item)
    return items


def _chats_items(app: _App, relative: str) -> list[MigrationItem]:
    """Past chats, offered unticked: in Import memories the person may look in what they wrote for memories."""
    path = app.source_root / relative
    count = stores.count_chats(path) if path.is_file() and not path.is_symlink() else 0
    if count == 0:
        return []
    item_id = make_item_id(MigrationCategory.MEMORIES, "past-chats")
    label = f"Past chats from {path.name}: {count} {'conversation' if count == 1 else 'conversations'}"
    if not app.live:
        return [_not_brought_over(item_id, MigrationCategory.MEMORIES, path, label,
                                  f"Past chats are read in this Row-Bot's own Import memories. {_ELSEWHERE}")]
    return [_store_item(
        id=item_id, category=MigrationCategory.MEMORIES, action=MigrationAction.CREATE, status=MigrationStatus.PLANNED,
        source=path, target=app.target / "memory_import", label=label,
        reason=(f"They wait in {stores.PLACES[stores.MEMORY_REVIEW]}, where Row-Bot can look in what you wrote for "
                "memories if you turn that on. Nothing is saved until you check it."),
        store=stores.MEMORY_REVIEW, details={"files": [relative], "chats": count},
        sensitivity=MigrationSensitivity.SENSITIVE, selected=False,
    )]


def _daily_notes_items(app: _App, relative: str) -> list[MigrationItem]:
    folder = app.source_root / relative
    if not folder.is_dir():
        return []
    notes = sorted((path for path in folder.glob("*.md") if path.is_file()), key=lambda path: path.name)
    if not notes:
        return []
    kept = notes[-_DAILY_NOTES_KEPT:]
    label = (f"Daily notes (the newest {len(kept)} of {len(notes)})" if len(kept) < len(notes)
             else f"Daily notes ({len(notes)} {'file' if len(notes) == 1 else 'files'})")
    item = _memory_item(app, "memories:daily-notes", folder, kept, label)
    return [item] if item is not None else []


# Skills, keys and archives ------------------------------------------------------------------------

def _skill_items(source_dir: Path, target_dir: Path, provider: MigrationProvider, *, prefix: str = "skill") -> list[MigrationItem]:
    if not source_dir.is_dir():
        return []
    items: list[MigrationItem] = []
    for skill_dir in sorted(path for path in source_dir.iterdir() if path.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        skill_name = _safe_name(skill_dir.name)
        items.append(
            _planned_item(
                id=make_item_id(MigrationCategory.SKILLS, f"{prefix}:{skill_name}"),
                category=MigrationCategory.SKILLS,
                action=MigrationAction.COPY,
                source=skill_file,
                target=target_dir / skill_name / "SKILL.md",
                label=f"{provider.value.title()} skill: {skill_dir.name}",
                details={"skill": skill_dir.name},
            )
        )
    return items


def _secret_items(env_path: Path, target_root: Path, *, include_secrets: bool) -> list[MigrationItem]:
    env_keys = _env_keys(env_path)
    items: list[MigrationItem] = []
    for env_key in env_keys:
        items.append(
            MigrationItem(
                id=make_item_id(MigrationCategory.API_KEYS, env_key),
                category=MigrationCategory.API_KEYS,
                action=MigrationAction.UPDATE,
                status=MigrationStatus.SENSITIVE if include_secrets else MigrationStatus.SKIPPED,
                source=env_path,
                target=target_root / f"api_keys.json#{env_key}",
                label=f"API key: {env_key}",
                reason="secret import disabled by default" if not include_secrets else "requires explicit confirmation before apply",
                details={"env_var": env_key},
                sensitivity=MigrationSensitivity.SECRET,
                selected=include_secrets,
                requires_confirmation=True,
            )
        )
    return items


def _archive_items(scan: MigrationScan, archive_root: Path, *, leave_out: frozenset[str] = frozenset()) -> list[MigrationItem]:
    items: list[MigrationItem] = []
    for entry in scan.entries:
        if not entry.archive_only or entry.relative_path in leave_out:
            continue
        items.append(
            MigrationItem(
                id=make_item_id(MigrationCategory.ARCHIVE, entry.relative_path),
                category=MigrationCategory.ARCHIVE,
                action=MigrationAction.ARCHIVE,
                status=MigrationStatus.ARCHIVE_ONLY,
                source=scan.source.root / entry.relative_path,
                target=archive_root / entry.relative_path,
                label=f"Archive: {entry.relative_path}",
                reason=f"{CRON_NOTE} Tick this to copy them into the report to look at." if entry.relative_path in _CRON
                else entry.reason,
                details={"relative_path": entry.relative_path, "size_bytes": entry.size_bytes},
                sensitivity=MigrationSensitivity.RISKY,
                selected=False,
            )
        )
    return items


def _target_status(target: str | Path | None) -> MigrationStatus:
    if target is None:
        return MigrationStatus.PLANNED
    target_path = Path(str(target).split("#", 1)[0])
    return MigrationStatus.CONFLICT if target_path.exists() else MigrationStatus.PLANNED


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def _parse_json_object(path: Path, warnings: list[str], label: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not parse {label}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _parse_yaml_object(path: Path, warnings: list[str], label: str) -> dict[str, Any]:
    if not path.is_file() or yaml is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not parse {label}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _record_items(value: Mapping[str, Any]) -> list[tuple[str, Any]]:
    return sorted(value.items(), key=lambda item: item[0])


def _child_record(root: Mapping[str, Any], key: str) -> dict[str, Any]:
    return _as_record(root.get(key))


def _as_record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _env_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    keys: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key:
            keys.append(key)
    return sorted(dict.fromkeys(keys))


def _safe_name(name: str) -> str:
    safe = _SAFE_NAME_RE.sub("-", name.strip().lower()).strip("-_.")
    return safe or "skill"
