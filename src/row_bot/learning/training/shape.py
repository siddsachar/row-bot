"""Training rows in the shape the personal model serves in (O71, L2w).

A captured row is the main model's request exactly as Row-Bot sent it. With a cloud main model
that request is Row-Bot's large-window envelope: every tool bound directly (about 18.5K Qwen
tokens of schemas), the full TASKS and MEMORY guidance in the system prompt, every tool guide in
full and Row-Bot's own state. The personal model never sees that envelope: it is served at its
recipe's ``serve_context`` (32K on the 8 GB tier), where Row-Bot gives it the small-context profile
(WP A5): the core tool groups bound, every other tool offered through ``tool_search`` /
``tool_invoke``, the tool guides as a one-line index and no state block. Rows in the large shape
are longer than the training length (so they are left out as ``too_long``), and the ones that fit
teach direct calls to tools the served model can only reach through the bridge.

:func:`render` re-renders one row into the serving envelope, using Row-Bot's own pieces for that
context (:func:`envelope_for`):

- **System prompt** (agent rows only; its sections joined as a system-first endpoint receives them,
  and as Row-Bot itself joins them for one): the root prompt leaves out what the small profile sends
  as guides; the static self-knowledge block goes; the full tool guides become the index, with the
  TASKS and MEMORY guidance for the groups still bound.
- **Turn context**: the "Current state:" block the small profile leaves out is removed.
- **Tools**: the tools the profile binds stay; the rest go behind ``tool_search`` /
  ``tool_invoke``, built by Row-Bot's own bridge over them (its catalog and its search).
- **Calls**: a call to a tool now behind the bridge becomes ``tool_invoke(name, arguments)`` with
  the same id, so its tool result is kept as it was. Where the conversation hasn't found that tool
  yet, a ``tool_search`` step comes first, answered by the bridge's own search (fenced as Row-Bot
  fences search results), so the row shows the model finding the tool before using it.

A rendered row is **derived** (E3, O11): it is what the served model would have had to write to do
what the main model did, never the main model's raw output. The dataset counts derived rows and
names the rendering (``serving-v<version>@<context>``) in its manifest and the release identity.
Rendering a row already in the serving shape (a local main model at 32K) changes nothing.

:func:`serve_problem` puts a replayed decision's prefix in the same shape for a model served at a
small context (pre-registration §4: an arm is asked as it serves, and arms that serve alike see the
problem rendered alike), and says which tools the served model must find before it can call them.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

RENDERING_VERSION = 1
SHAPES = ("serving", "captured")
Profile = Literal["small", "window", "full"]

#: Tools every serving profile binds when they're there: the bridges and the saved-output reader.
BRIDGES = ("tool_search", "tool_invoke")
ALWAYS_BOUND = frozenset({*BRIDGES, "skill_search", "skill_load", "read_tool_output"})
#: How many results a synthesized search asks for (``tool_search``'s default), then more.
SEARCH_LIMITS = (3, 5)
#: The live-state block the small profile leaves out of the turn context (``self_knowledge``).
STATE_HEADER = "Current state:\n"
_TURN_CONTEXT = re.compile(r"(<row_bot_turn_context>)(.*?)(</row_bot_turn_context>)", re.DOTALL)
_STATE_BLOCK = re.compile(r"\n\nCurrent state:\n.*?(?=\n\n|\n?$)", re.DOTALL)


class RenderError(ValueError):
    """A row that can't be put in the serving shape (a bridged tool the search never finds)."""


@dataclass(frozen=True)
class Envelope:
    """What the personal model is served with at ``serve_context`` (A5's profiles).

    ``small`` (32K or less): ``bound`` names the core groups' tools, the only ones bound with the
    bridges; everything else is deferred. ``window`` (up to 64K): only ``deferred`` (the rarely used
    groups) goes behind the bridge. ``full``: nothing changes.
    """

    serve_context: int
    profile: Profile
    bound: frozenset[str] = frozenset()
    deferred: frozenset[str] = frozenset()
    # The part of the large root prompt the small one leaves out (TASKS and MEMORY), and the text
    # that follows it in the large root (so only the root's own copy is taken out).
    root_removed: str = ""
    root_follows: str = ""
    # (group, its tools' names, the guidance text) that move from the root into the guides.
    root_guides: tuple[tuple[str, frozenset[str], str], ...] = ()
    # Every tool guide: (instructions, the tool groups it is for).
    guides: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # The small profile's guide index for some groups: (groups, loadable) -> text.
    guide_index: Callable[[Sequence[str], bool], str] = field(default=lambda groups, loadable: "", repr=False)
    # Texts the profile leaves out of the system prompt (the static self-knowledge block's parts).
    dropped_texts: tuple[str, ...] = ()
    # Where a deferred tool comes from, for its search result ("core" when unknown).
    sources: Mapping[str, str] = field(default_factory=dict)
    # The group a deferred built-in tool belongs to (its record's parent).
    parents: Mapping[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"serving-v{RENDERING_VERSION}@{self.serve_context}"

    def defers(self, tool_name: str) -> bool:
        if tool_name in ALWAYS_BOUND:
            return False
        if self.profile == "small":
            return tool_name not in self.bound
        if self.profile == "window":
            return tool_name in self.deferred
        return False

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "version": RENDERING_VERSION, "serve_context": self.serve_context,
                "profile": self.profile}


@dataclass
class Stats:
    """What rendering changed in one row (counts)."""

    tools_deferred: int = 0
    calls_bridged: int = 0
    searches_added: int = 0
    system_changed: bool = False
    state_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.tools_deferred or self.calls_bridged or self.searches_added or self.system_changed
                    or self.state_removed)


# ── Row-Bot's own envelope for a context ────────────────────────────────────


def _tool_names(group: str) -> set[str]:
    """The runtime names of one built-in group's tools (only groups that build locally)."""

    from row_bot.tools import registry

    tool = registry.get_tool(group)
    if tool is None:
        return set()
    try:
        return {str(getattr(item, "name", "") or "") for item in tool.as_langchain_tools()} - {""}
    except Exception:  # noqa: BLE001 - a group that can't build here (Developer without a folder)
        logger.debug("Learning couldn't list the %s tools for the serving shape", group, exc_info=True)
        return set()


def _sources() -> dict[str, str]:
    """Where external tools come from (MCP, plugins, custom tools), from local snapshots only."""

    found: dict[str, str] = {}
    try:
        from row_bot.plugins import registry as plugins

        for record in plugins.get_enabled_plugin_tool_records():
            name = str(record.get("runtime_name") or "")
            plugin = str(record.get("plugin_id") or "plugin")
            tags = {str(tag).strip().lower() for tag in (record.get("tags") or [])}
            kind = "custom" if "custom-tool" in tags or plugin.startswith("custom-tool-") else "plugin"
            if name:
                found[name] = f"{kind}:{plugin}"
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't read plugin tools for the serving shape", exc_info=True)
    try:
        from row_bot.mcp_client import runtime as mcp_runtime

        for tool in mcp_runtime.get_langchain_tools(refresh=False):
            name = str(getattr(tool, "name", "") or "")
            if name:
                found.setdefault(name, "mcp")
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't read MCP tools for the serving shape", exc_info=True)
    return found


def _difference(large: str, small: str) -> tuple[str, str]:
    """The part of ``large`` that ``small`` leaves out (one contiguous span) and up to 200 characters of
    what follows it, or ("", "")."""

    prefix = 0
    while prefix < min(len(large), len(small)) and large[prefix] == small[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < min(len(large), len(small)) - prefix
           and large[len(large) - 1 - suffix] == small[len(small) - 1 - suffix]):
        suffix += 1
    middle = large[prefix:len(large) - suffix]
    if not middle or len(large) - len(middle) != len(small):
        return "", ""
    return middle, large[len(large) - suffix:][:200]


_ENVELOPES: dict[tuple[int, str], tuple[float, Envelope]] = {}
ENVELOPE_SECONDS = 300.0


def envelope_for(serve_context: int) -> Envelope:
    """Row-Bot's serving envelope at this context, from its own prompt, guides and tool groups
    (kept a few minutes: a count and the compile that follows it build it once)."""

    import time

    from row_bot import learning

    key = (int(serve_context), str(learning.data_dir()))
    found = _ENVELOPES.get(key)
    if found is not None and time.monotonic() - found[0] < ENVELOPE_SECONDS:
        return found[1]
    envelope = _build(int(serve_context))
    _ENVELOPES[key] = (time.monotonic(), envelope)
    return envelope


def _build(serve_context: int) -> Envelope:
    from row_bot import agent, prompts, skills

    context = int(serve_context)
    if 0 < context <= agent.SMALL_PROFILE_TOKENS:
        profile: Profile = "small"
    elif 0 < context <= agent.SMALL_CONTEXT_TOKENS:
        profile = "window"
    else:
        return Envelope(context, "full")
    parents: dict[str, str] = {}
    bound: set[str] = set()
    deferred: set[str] = set()
    if profile == "small":
        for group in sorted(agent.SMALL_PROFILE_CORE_GROUPS):
            names = _tool_names(group)
            bound |= names
    for group in sorted(agent.ON_DEMAND_TOOL_GROUPS):
        names = _tool_names(group)
        parents.update({name: group for name in names})
        if profile == "window":
            deferred |= names
    root_guides = []
    for group, text in prompts.ROOT_TOOL_GUIDES:
        root_guides.append((group, frozenset(_tool_names(group)), text))
    if not skills.skills_loaded():  # the app loads them at start; a script may not have
        skills.load_skills()
    guides = tuple((str(item.instructions or ""), tuple(str(tool) for tool in item.tools))
                   for item in skills.get_all_skills() if skills.is_tool_guide(item) and not item.on_demand
                   and str(item.instructions or "").strip())
    removed, follows = _difference(prompts.get_agent_system_prompt(), prompts.get_agent_system_prompt(small_context=True))
    dropped: list[str] = []
    try:
        from row_bot.self_knowledge import ABOUT_ROW_BOT, SKILL_CREATION_GUIDANCE

        dropped += [str(ABOUT_ROW_BOT).strip(), str(SKILL_CREATION_GUIDANCE).strip()]
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't read the self-knowledge block for the serving shape", exc_info=True)

    def index(groups: Sequence[str], loadable: bool) -> str:
        return skills.get_tool_guide_index(sorted(groups), loadable=loadable)

    return Envelope(
        serve_context=context, profile=profile, bound=frozenset(bound), deferred=frozenset(deferred),
        root_removed=removed if profile == "small" else "", root_follows=follows if profile == "small" else "",
        root_guides=tuple(root_guides) if profile == "small" else (), guides=guides if profile == "small" else (),
        guide_index=index, dropped_texts=tuple(item for item in dropped if item) if profile == "small" else (),
        sources=_sources(), parents=parents,
    )


# ── Rendering one row ───────────────────────────────────────────────────────


def _name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    return str(function.get("name") or "")


def _schema(tool: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    parameters = function.get("parameters")
    return (str(function.get("name") or ""), str(function.get("description") or ""),
            dict(parameters) if isinstance(parameters, Mapping) else {"type": "object", "properties": {}})


def _bridge(records: Sequence[Any], context: int) -> tuple[Any, Any]:
    from row_bot.tools.discovery import build_tool_discovery_tools

    return build_tool_discovery_tools(records, context_tokens=context)


def _openai_tool(tool: Any) -> dict[str, Any]:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    return dict(convert_to_openai_tool(tool))


def _record(name: str, description: str, schema: Mapping[str, Any], envelope: Envelope) -> Any:
    from row_bot.tools.discovery import ExternalToolRecord, normalize_catalog_text

    return ExternalToolRecord(
        name=normalize_catalog_text(name), source=normalize_catalog_text(envelope.sources.get(name, "core")),
        parent=normalize_catalog_text(envelope.parents.get(name, "")),
        description=normalize_catalog_text(description, description=True)[:180],
        input_schema=json.loads(json.dumps(dict(schema))), target=None,
    )


def _arguments(call: Mapping[str, Any]) -> Any:
    function = call.get("function") or {}
    raw = function.get("arguments")
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except ValueError:
            return {}
    return raw if raw is not None else {}


def _found_in(content: Any) -> set[str]:
    """Tool names a ``tool_search`` result found (its JSON, fenced or not)."""

    text = content if isinstance(content, str) else json.dumps(content, default=str)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return set()
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return set(re.findall(r'"name":\s*"([^"]+)"', text))
    results = value.get("results") if isinstance(value, Mapping) else None
    return {str(item.get("name") or "") for item in results or () if isinstance(item, Mapping)} - {""}


def _fenced_search(raw: str) -> str:
    from row_bot import external_content

    return external_content.wrap("tool_search", raw)


def _render_system(parts: list[str], envelope: Envelope, bound_names: set[str], loadable: bool,
                   stats: Stats) -> list[str]:
    """The agent's system prompt as the small profile sends it (root, self-knowledge, guides), on its
    sections joined (Row-Bot joins them the same way for an endpoint that takes one system message)."""

    if envelope.profile != "small" or not parts:
        return parts
    text = "\n\n".join(part for part in parts if part)
    out = text
    # The root's own TASKS and MEMORY guidance (the copy followed by the root's last part) goes.
    marker = envelope.root_removed + envelope.root_follows
    root_changed = bool(envelope.root_removed and envelope.root_follows and marker in out)
    if root_changed:
        out = out.replace(marker, envelope.root_follows, 1)
    for dropped in envelope.dropped_texts:
        if dropped and dropped in out:
            out = out.replace(dropped, "", 1)
    spans: list[tuple[int, int, tuple[str, ...]]] = []
    for instructions, groups in envelope.guides:
        body = instructions.strip()
        start = out.find(body) if body else -1
        if start >= 0 and not any(start < end and begin < start + len(body) for begin, end, _ in spans):
            spans.append((start, start + len(body), groups))
    spans.sort()
    if spans or root_changed:
        guides = [guidance for _group, names, guidance in envelope.root_guides if names & bound_names]
        index = envelope.guide_index(sorted({group for _, _, items in spans for group in items}), loadable) \
            if spans else ""
        small = "\n\n".join(item.strip() for item in [*guides, index] if item and item.strip())
        pieces, position = [], 0
        for number, (begin, end, _groups) in enumerate(spans):
            pieces.append(out[position:begin])
            if number == 0:
                pieces.append(small)
            position = end
        pieces.append(out[position:])
        out = "".join(pieces) if spans else f"{out}\n\n{small}"
    if out == text:
        return parts
    out = re.sub(r"\n{3,}", "\n\n", out).strip()  # where a removed part leaves a gap
    stats.system_changed = True
    return [out]


def _remove_state(messages: list[dict[str, Any]], stats: Stats) -> None:
    for message in messages:
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        if "<row_bot_turn_context>" not in content or STATE_HEADER not in content:
            continue

        def strip(match: re.Match[str]) -> str:
            body, count = _STATE_BLOCK.subn("", match.group(2))
            stats.state_removed += count
            return match.group(1) + body + match.group(3)

        message["content"] = _TURN_CONTEXT.sub(strip, content)


def _catalog(deferred: Sequence[Mapping[str, Any]], messages: Sequence[Mapping[str, Any]],
             envelope: Envelope) -> tuple[dict[str, Any], dict[str, str]]:
    """The bridge's records for the deferred tools (and tools the row's own searches showed), and the
    row's tool call ids with their tools' names."""

    records = {}
    for tool in deferred:
        name, description, schema = _schema(tool)
        records[name] = _record(name, description, schema, envelope)
    known_ids: dict[str, str] = {}
    for message in messages:
        for call in message.get("tool_calls") or ():
            known_ids[str(call.get("id") or "")] = str((call.get("function") or {}).get("name") or "")
    for message in messages:
        if message.get("role") == "tool" and known_ids.get(str(message.get("tool_call_id") or "")) == "tool_search":
            text = message.get("content")
            raw = text if isinstance(text, str) else json.dumps(text, default=str)
            start, end = raw.find("{"), raw.rfind("}")
            try:
                value = json.loads(raw[start:end + 1]) if 0 <= start < end else {}
            except ValueError:
                value = {}
            for item in (value.get("results") or ()) if isinstance(value, Mapping) else ():
                if isinstance(item, Mapping) and item.get("name") and str(item["name"]) not in records:
                    records[str(item["name"])] = _record(str(item["name"]), str(item.get("description") or ""),
                                                         item.get("input_schema") or {}, envelope)
    return records, known_ids


def render(system: Sequence[str], messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]] | None,
           envelope: Envelope, *, agent: bool = True) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]] | None,
                                                               Stats]:
    """One row (its system sections, messages and tools, OpenAI format) in the serving shape.

    Raises :class:`RenderError` when a call goes to a deferred tool the bridge's search can't
    find. ``agent``: the row is an Agent request (its system sections are Row-Bot's agent prompt).
    """

    stats = Stats()
    out_messages = [json.loads(json.dumps(dict(message))) for message in messages]
    out_system = list(system)
    if envelope.profile == "full":
        return out_system, out_messages, None if tools is None else [dict(tool) for tool in tools], stats
    captured = [dict(tool) for tool in tools or ()]
    names = [_name(tool) for tool in captured]
    deferred = [tool for tool in captured if envelope.defers(_name(tool))]
    bound_names = {name for name in names if not envelope.defers(name)}
    loadable = "skill_load" in names
    if agent:
        out_system = _render_system(out_system, envelope, bound_names, loadable, stats)
        if envelope.profile == "small":
            _remove_state(out_messages, stats)
    if not deferred:
        return out_system, out_messages, (captured if tools is not None else None), stats
    # Row-Bot's own bridge over the deferred tools: its schemas, its catalog, its search. Tools the
    # captured row already reached through a bridge are kept where its own searches showed them.
    records, known_ids = _catalog(deferred, out_messages, envelope)
    search_tool, invoke_tool = _bridge(list(records.values()), envelope.serve_context)
    stats.tools_deferred = len(deferred)
    kept = [tool for tool in captured if not envelope.defers(_name(tool)) and _name(tool) not in ALWAYS_BOUND]
    later = [tool for tool in captured if _name(tool) in ALWAYS_BOUND - set(BRIDGES)]
    out_tools = kept + [_openai_tool(search_tool), _openai_tool(invoke_tool)] + later

    def search(name: str) -> tuple[dict[str, Any], str]:
        for query in (name.replace("_", " "), name):
            for limit in SEARCH_LIMITS:
                result = search_tool.invoke({"query": query, "limit": limit})
                if name in _found_in(result):
                    asked: dict[str, Any] = {"query": query}
                    if limit != SEARCH_LIMITS[0]:
                        asked["limit"] = limit
                    return asked, str(result)
        raise RenderError(f"the bridge's search can't find {name}")

    found: set[str] = set()
    rendered: list[dict[str, Any]] = []
    for message in out_messages:
        if message.get("role") == "tool" and known_ids.get(str(message.get("tool_call_id") or "")) == "tool_search":
            found |= _found_in(message.get("content"))
        calls = message.get("tool_calls") or []
        targets = [call for call in calls if envelope.defers(str((call.get("function") or {}).get("name") or ""))]
        if message.get("role") != "assistant" or not targets:
            rendered.append(message)
            continue
        needed = []
        for call in targets:
            name = str((call.get("function") or {}).get("name") or "")
            if name not in found and name not in {item[0] for item in needed}:
                needed.append((name, str(call.get("id") or "")))
        if needed:
            search_calls, results = [], []
            for name, call_id in needed:
                asked, result = search(name)
                search_id = f"{call_id or name}_search"
                search_calls.append({"id": search_id, "type": "function", "function": {
                    "name": "tool_search", "arguments": json.dumps(asked, ensure_ascii=False)}})
                results.append({"role": "tool", "content": _fenced_search(result), "tool_call_id": search_id})
                found |= _found_in(result)
                stats.searches_added += 1
            step = {"role": "assistant", "content": message.get("content") or "", "tool_calls": search_calls}
            if "weight" in message:
                step["weight"] = message["weight"]
            rendered.append(step)
            rendered.extend(results)
            message = {**message, "content": ""}
        rewritten = []
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if envelope.defers(name):
                arguments = {"name": name, "arguments": _arguments(call)}
                call = {**call, "function": {"name": "tool_invoke",
                                             "arguments": json.dumps(arguments, ensure_ascii=False)}}
                stats.calls_bridged += 1
            rewritten.append(call)
        rendered.append({**message, "tool_calls": rewritten})
    return out_system, rendered, out_tools, stats


@dataclass(frozen=True)
class ServedProblem:
    """A replayed decision's prefix as a model served at the envelope's context is asked it."""

    system: list[str]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    #: Deferred tools the reference calls that the prefix hasn't found: the served model's first
    #: move is a search that finds them (a rendered training row shows the same step).
    search_first: frozenset[str] = frozenset()
    #: Runs a ``tool_search`` call's arguments through the bridge's own search: the names it finds.
    search: Callable[[Mapping[str, Any]], set[str]] | None = field(default=None, repr=False)


def serve_problem(system: Sequence[str], messages: Sequence[Mapping[str, Any]],
                  tools: Sequence[Mapping[str, Any]] | None, reference_calls: Iterable[str], envelope: Envelope, *,
                  agent: bool = True) -> ServedProblem:
    """A replayed decision's prefix (system sections, OpenAI-format messages and tools) in the
    serving shape, with the tools its reference calls (``reference_calls``, direct names) that the
    served model must first find. Raises :class:`RenderError` as :func:`render` does."""

    out_system, out_messages, out_tools, _ = render(system, messages, tools, envelope, agent=agent)
    deferred = [dict(tool) for tool in tools or () if envelope.defers(_name(tool))]
    if envelope.profile == "full" or not deferred:
        return ServedProblem(out_system, out_messages, out_tools)
    records, _ = _catalog(deferred, [dict(message) for message in messages], envelope)
    search_tool, _invoke = _bridge(list(records.values()), envelope.serve_context)
    ids = {str(call.get("id") or ""): str((call.get("function") or {}).get("name") or "")
           for message in out_messages for call in message.get("tool_calls") or ()}
    found: set[str] = set()
    for message in out_messages:
        if message.get("role") == "tool" and ids.get(str(message.get("tool_call_id") or "")) == "tool_search":
            found |= _found_in(message.get("content"))
    need = frozenset(name for name in reference_calls if envelope.defers(name) and name not in found)

    def search(arguments: Mapping[str, Any]) -> set[str]:
        try:
            return _found_in(search_tool.invoke(dict(arguments)))
        except Exception:  # noqa: BLE001 - a search the bridge refuses finds nothing
            return set()

    return ServedProblem(out_system, out_messages, out_tools, need, search)


def describe(envelope: Envelope | None) -> dict[str, Any]:
    """The rendering a dataset's manifest names ("captured" when rows are kept as captured)."""

    if envelope is None:
        return {"name": "captured", "version": 0}
    return envelope.describe()


def bound_tool_names(tools: Iterable[Mapping[str, Any]]) -> list[str]:
    return [_name(tool) for tool in tools]


__all__ = [
    "ALWAYS_BOUND",
    "Envelope",
    "RENDERING_VERSION",
    "RenderError",
    "SHAPES",
    "ServedProblem",
    "Stats",
    "bound_tool_names",
    "describe",
    "envelope_for",
    "render",
    "serve_problem",
]
