"""Safety classification and naming helpers for MCP tools."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# High impact: destroys, reaches other people, runs code, moves money or grants access. Always asks.
_DESTRUCTIVE_RE = re.compile(
    r"(^|_)(delete|remove|destroy|drop|purge|erase|wipe|truncate|revoke|uninstall|kill|terminate|cancel|refund|"
    r"send|post|reply|forward|comment|invite|share|publish|deploy|merge|commit|push|upload|"
    r"run|exec|execute|shell|command|payment|pay|charge|transfer|withdraw|trade|buy|sell|order|book|"
    r"grant|permission|permissions|reset|overwrite|eval)(_|$)",
    re.IGNORECASE,
)
# A change to these reaches other people, publishes, runs code or grants access: high impact. A repository
# is one too: a repository write (create_or_update_file) is a commit, as in Row-Bot's own git rules.
_SENSITIVE_RE = re.compile(
    r"(^|_)(emails?|messages?|releases?|scripts?|passwords?|roles?|members?|collaborators?|tokens?|secrets?|keys?|"
    r"admins?|owners?|webhooks?|pull_requests?|users?|visibility|memberships?|repository|repositories|repos?)(_|$)",
    re.IGNORECASE,
)
# A routine change the description places in a repository ("a file in a GitHub repository") is a commit too;
# the same verb on a file on this computer stays a routine change.
_REPOSITORY_RE = re.compile(r"(^|_)(repository|repositories|repo|repos)(_|$)", re.IGNORECASE)
# Routine changes inside the app: ask unless the user chose Full access for the tool.
_ROUTINE_RE = re.compile(
    r"(^|_)(create|add|update|edit|write|set|rename|move|put|patch|modify|insert|append|save|assign|duplicate)(_|$)",
    re.IGNORECASE,
)
# Words that are also nouns are changes only as the name's verb: tag_issue, not get_tag.
_ROUTINE_FIRST_RE = re.compile(r"^(tag|label|mark|archive|close|star|link|attach|copy)(_|$)", re.IGNORECASE)
_READ_RE = re.compile(r"^(read|get|list|search|find|inspect|describe|count|query|fetch|status|lookup)(_|$)", re.IGNORECASE)
# A read that says it runs a read ("Execute a SELECT query", "Run a search") is still a read (B306).
_RUNS_A_READ = re.compile(r"(^|_)(run|exec|execute)s?_(an?_|the_)?(select|search|read_only|readonly)(?!_into)(_|$)",
                          re.IGNORECASE)
# Words that are also nouns: a read describes them ("List orders", "Get posts"), so their -s form never counts.
_NOUNS = {"order", "post", "comment", "share", "invite", "book", "charge", "payment", "pay", "trade", "buy", "sell",
          "reply", "forward", "set", "add", "save", "edit", "move", "put", "patch", "command", "shell", "permission",
          "permissions", "eval"}


def _any_form(*patterns: re.Pattern, extra: tuple[str, ...] = ()) -> re.Pattern:
    """A description's verbs as it says them ("Executes", "Runs … inserts, updates and deletes"). Past and -ing
    forms stay out: reads describe what they return with them ("recently updated", "deleted items")."""
    verbs = [verb for pattern in patterns
             for verb in re.search(r"\(\^\|_\)\(([a-z|]+)\)\(_\|\$\)", pattern.pattern).group(1).split("|")]
    forms = {form for verb in (*verbs, *extra)
             for form in ((verb,) if verb in _NOUNS else (verb, verb + "s", verb + "es"))}
    return re.compile(r"(^|_)(" + "|".join(sorted(forms)) + r")(_|$)", re.IGNORECASE)


_SAYS_HIGH_IMPACT = _any_form(_DESTRUCTIVE_RE)
_DESTRUCTIVE_WORDS = set(re.search(r"\(\^\|_\)\(([a-z|]+)\)\(_\|\$\)", _DESTRUCTIVE_RE.pattern).group(1).split("|"))
# High-impact words that are also what a read returns: get_commit reads a commit, get_workflow_run a run,
# get_order an order. Only as the name's verb ("commit_changes", "run_query") are they changes.
_READ_OBJECTS = {"commit", "run", "order", "post", "comment", "share", "invite", "book", "charge", "payment", "reply",
                 "command", "merge", "push", "deploy", "upload", "permission"}
# Straight after the read verb, a singular object is a noun only last or before one of these ("get_commit_status");
# otherwise it may be a second verb ("search_book_flight"). After another word it is part of a noun ("workflow run").
_NOUN_AFTER = {"id", "ids", "detail", "details", "status", "statuses", "log", "logs", "info", "history", "items",
               "files", "metadata", "summary", "count", "artifacts", "usage", "jobs", "comments", "reviews"}
# In a description, an object is a noun after one of these ("for a commit", "a specific workflow run"), never
# straight after a joining word ("or post a new one").
_BEFORE_A_NOUN = {"a", "an", "the", "each", "this", "that", "its", "their", "your", "one", "of", "for", "by", "from",
                  "about", "specific", "given"}
_JOINING = {"and", "then", "or", "to"}
_SAYS_A_CHANGE = _any_form(_ROUTINE_RE, extra=("replace", "clear"))
HINTS = ("readOnlyHint", "destructiveHint")  # The annotations Row-Bot weighs; a saved catalog keeps them (B307).

_BROWSER_SESSION_SAFE_TOOLS = {
    "browser_click",
    "browser_close",
    "browser_console_messages",
    "browser_drag",
    "browser_fill_form",
    "browser_hover",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_requests",
    "browser_press_key",
    "browser_resize",
    "browser_select_option",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_wait_for",
}
_BROWSER_READS = {"browser_console_messages", "browser_network_requests", "browser_snapshot",
                  "browser_take_screenshot", "browser_wait_for"}


def sanitize_name_component(value: str) -> str:
    """Return a function-call safe identifier component."""
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text or "unnamed"


def prefixed_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp_{sanitize_name_component(server_name)}_{sanitize_name_component(tool_name)}"


def _annotation_value(tool: Any, key: str) -> Any:
    annotations = tool.get("annotations") if isinstance(tool, dict) else getattr(tool, "annotations", None)
    if annotations is None:
        return None
    if isinstance(annotations, dict):
        return annotations.get(key)
    return getattr(annotations, key, None)


def schema_digest(schema: Any) -> str:
    """What an accepted catalog keeps of a tool's input schema: enough to see any change, whatever its size
    (one of Notion's tools has an 83 KB schema)."""
    data = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def saved_schema_digest(row: dict) -> str:
    """A saved catalog row's schema digest; a catalog saved before digests kept the whole schema."""
    return row["input_schema_digest"] if "input_schema_digest" in row else schema_digest(row.get("input_schema"))


def hints(tool: Any) -> dict[str, bool]:
    """The annotations of ``tool`` that Row-Bot weighs, as its saved catalog keeps them."""
    return {key: value for key in HINTS if type(value := _annotation_value(tool, key)) is bool}


def saved_hints(value: Any) -> dict[str, bool] | None:
    """Saved annotations as :func:`hints` wrote them, or None when they aren't."""
    valid = type(value) is dict and set(value) <= set(HINTS) and all(type(hint) is bool for hint in value.values())
    return value if valid else None


def _object(word: str) -> tuple[str, bool] | None:
    """The read object a word names and whether it is plural ("commits": ("commit", True)), or None."""
    if word in _READ_OBJECTS:
        return word, False
    base = next((base for base in (word[:-1], word[:-2]) if base in _READ_OBJECTS and word in (base + "s", base + "es")),
                None)
    return (base, True) if base else None


def _read_objects(name: str) -> set[str] | None:
    """What a plain read names as its object ("get_commit": {"commit"}, "list_commits": {"commit"}), or None when the
    name is not a plain read: no read verb first, a joining word ("get_and_push", "fetch_to_upload"), a singular
    object word where a verb could stand ("search_book_flight"), or a high-impact word that is not something a read
    returns ("read_delete_log")."""
    words = name.split("_")
    if not _READ_RE.match(name) or _JOINING & set(words):
        return None
    objects = set()
    for index, word in enumerate(words[1:], 1):
        found = _object(word)
        if found is None:
            if word in _DESTRUCTIVE_WORDS:
                return None
            continue
        base, plural = found
        after = words[index + 1] if index + 1 < len(words) else ""
        if not plural and index == 1 and after and after not in _NOUN_AFTER:
            return None
        objects.add(base)
    return objects


def _without_objects(words: str, objects: set[str]) -> str:
    """A read's description without the objects it names as nouns ("Get details for a commit"), so only what it
    says it does is weighed; "or post a new one" keeps its verb."""
    kept = []
    parts = words.split("_")
    for index, word in enumerate(parts):
        found = _object(word)
        if found and found[0] in objects and (index == 0 or parts[index - 1] not in _JOINING) and (
                found[1] or _BEFORE_A_NOUN & set(parts[max(0, index - 3):index])
                or (index + 1 < len(parts) and parts[index + 1] in _NOUN_AFTER)):
            continue
        kept.append(word)
    return "_".join(kept)


def _classify(tool_name: str, description: str, tool: Any) -> str:
    """``high_impact``, ``mutation``, ``read_only``, ``interaction`` or ``unknown``.

    Trusted in this order (B307, B306): a ``destructiveHint``; a high-impact name; a change named as one (which
    its description can still make high impact); a ``readOnlyHint``; a read verb in the name; and only then the
    description. A read-only hint never outweighs a name, and a description never outweighs either of them,
    except that a read whose description says it changes something asks first."""
    name = sanitize_name_component(tool_name)
    words = sanitize_name_component(description or "")
    objects = _read_objects(name)
    if (_annotation_value(tool, "destructiveHint") is True or (_DESTRUCTIVE_RE.search(name) and objects is None)
            or _sensitive_change(name, description)):
        return "high_impact"
    if name in _BROWSER_READS:
        return "read_only"
    if name in _BROWSER_SESSION_SAFE_TOOLS:
        return "interaction"
    if _changes(name):
        # Its description can make a change high impact, unless the server says it isn't destructive: words such
        # as "share" or "comment" in a description are often nouns ("Create pages ... shared with you").
        said = _SAYS_HIGH_IMPACT.search(words) and _annotation_value(tool, "destructiveHint") is not False
        return "high_impact" if said else "mutation"
    read_only = _annotation_value(tool, "readOnlyHint")
    if read_only is True:
        return "read_only"
    if read_only is not False and _READ_RE.match(name):
        # Any other change or high-impact word in its description and it asks first (never runs on its own).
        said = _RUNS_A_READ.sub("_", words)
        if objects:  # What it reads ("Get details for a commit") is what it returns, not something it does.
            said = _without_objects(said, objects)
        return "unknown" if _SAYS_HIGH_IMPACT.search(said) or _SAYS_A_CHANGE.search(said) else "read_only"
    return "high_impact" if _SAYS_HIGH_IMPACT.search(words) else "unknown"


def is_destructive_tool(tool_name: str, description: str = "", tool_obj: Any = None) -> bool:
    """Whether a tool is destructive or high impact, so it asks in every access preset."""
    return _classify(tool_name, description, tool_obj) == "high_impact"


def _changes(name: str) -> bool:
    return bool(_ROUTINE_RE.search(name) or _ROUTINE_FIRST_RE.match(name))


def _sensitive_change(name: str, description: str = "") -> bool:
    """create_release, add_collaborator, set_password: a routine verb on something high impact, or on
    something its description puts in a repository."""
    return _changes(name) and bool(_SENSITIVE_RE.search(name) or _REPOSITORY_RE.search(sanitize_name_component(description)))


def tool_enabled_by_default(is_destructive: bool) -> bool:
    """Default selection rule after a successful server test/discovery."""
    return not is_destructive


def asks_first(name: str, destructive: bool, effect: str, approvals, allowed) -> bool:
    """The invocation gate. High-impact and unknown tools always ask; a routine change asks
    unless the user allowed it to run without asking (Full access, or that one tool)."""
    return bool(destructive or effect == "unknown" or name in approvals or (effect == "mutation" and name not in allowed))


def classify_tool_effect(tool_name: str, description: str = "", tool_obj: Any = None) -> str:
    """Describe known effects without treating an unrecognized name as read-only.

    Reviewed browser interaction retains its existing approval policy; its
    classification remains distinct from an observational/read-only operation.
    Server annotations are hints, never evidence of an operating-system sandbox.
    """
    found = _classify(tool_name, description, tool_obj)
    return "mutation" if found == "high_impact" else found
