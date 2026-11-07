from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from row_bot.providers.selection import model_ref, parse_model_ref

logger = logging.getLogger(__name__)

PROVIDER_DEFAULT = "provider_default"
SELECTION_KINDS = frozenset({PROVIDER_DEFAULT, "effort", "on", "off", "budget"})
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_EFFORT_LABELS = {"xhigh": "XHigh"}


@dataclass(frozen=True)
class ReasoningCapabilities:
    supported_efforts: tuple[str, ...] = ()
    default_effort: str = ""
    default_enabled: bool | None = None
    can_disable: bool = False
    mandatory: bool = False
    supports_budget: bool = False
    budget_min: int = 1
    budget_max: int = 0
    thinking_mode: str = "none"
    source: str = ""
    revision: str = ""
    request_style: str = ""

    def __post_init__(self) -> None:
        efforts = []
        for value in self.supported_efforts:
            effort = str(value or "").strip().lower()
            if effort and effort not in efforts:
                efforts.append(effort)
        efforts.sort(key=lambda value: (_EFFORT_ORDER.index(value) if value in _EFFORT_ORDER else 999, value))
        object.__setattr__(self, "supported_efforts", tuple(efforts))
        object.__setattr__(self, "default_effort", str(self.default_effort or "").strip().lower())
        object.__setattr__(self, "budget_min", max(0, int(self.budget_min or 0)))
        object.__setattr__(self, "budget_max", max(0, int(self.budget_max or 0)))
        object.__setattr__(self, "thinking_mode", str(self.thinking_mode or "none").strip().lower())
        object.__setattr__(self, "request_style", str(self.request_style or "").strip().lower())

    @property
    def controllable(self) -> bool:
        return bool(
            self.supported_efforts
            or self.supports_budget
            or self.thinking_mode == "toggle"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "supported_efforts": list(self.supported_efforts),
            "default_effort": self.default_effort,
            "default_enabled": self.default_enabled,
            "can_disable": self.can_disable,
            "mandatory": self.mandatory,
            "supports_budget": self.supports_budget,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "thinking_mode": self.thinking_mode,
            "source": self.source,
            "revision": self.revision,
            "request_style": self.request_style,
        }

    @classmethod
    def from_json(cls, value: Any) -> ReasoningCapabilities | None:
        if not isinstance(value, Mapping):
            return None
        efforts = value.get("supported_efforts") or value.get("supportedEfforts") or ()
        if isinstance(efforts, str):
            efforts = (efforts,)
        elif not isinstance(efforts, (list, tuple, set, frozenset)):
            efforts = ()
        supports_budget = value.get("supports_budget")
        if supports_budget is None:
            supports_budget = value.get("supports_max_tokens")
        mandatory = bool(value.get("mandatory"))
        can_disable = value.get("can_disable")
        if can_disable is None:
            can_disable = value.get("default_enabled") is not None and not mandatory
        default_enabled = value.get("default_enabled") if isinstance(value.get("default_enabled"), bool) else None
        inferred_mode = "manual" if supports_budget else "toggle" if default_enabled is not None and not efforts else "none"
        caps = cls(
            supported_efforts=tuple(str(item) for item in efforts if str(item)),
            default_effort=str(value.get("default_effort") or value.get("defaultEffort") or ""),
            default_enabled=default_enabled,
            can_disable=bool(can_disable),
            mandatory=mandatory,
            supports_budget=bool(supports_budget),
            budget_min=_positive_int(value.get("budget_min"), default=1, allow_zero=True),
            budget_max=_positive_int(value.get("budget_max") or value.get("max_tokens"), default=0, allow_zero=True),
            thinking_mode=str(value.get("thinking_mode") or inferred_mode),
            source=str(value.get("source") or "catalog_metadata"),
            revision=str(value.get("revision") or ""),
            request_style=str(value.get("request_style") or ""),
        )
        return caps if caps.controllable else None


@dataclass(frozen=True)
class ReasoningSelection:
    kind: str = PROVIDER_DEFAULT
    effort: str = ""
    budget: int = 0

    def __post_init__(self) -> None:
        kind = str(self.kind or PROVIDER_DEFAULT).strip().lower()
        if kind not in SELECTION_KINDS:
            raise ValueError(f"Unknown reasoning selection kind: {kind}")
        effort = str(self.effort or "").strip().lower()
        budget = int(self.budget or 0)
        if kind == "effort" and not effort:
            raise ValueError("An effort selection requires an effort value.")
        if kind == "budget" and budget <= 0:
            raise ValueError("A reasoning budget must be a positive integer.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "effort", effort if kind == "effort" else "")
        object.__setattr__(self, "budget", budget if kind == "budget" else 0)

    @property
    def is_default(self) -> bool:
        return self.kind == PROVIDER_DEFAULT

    @property
    def label(self) -> str:
        if self.is_default:
            return "Provider default"
        if self.kind == "effort":
            return _EFFORT_LABELS.get(self.effort, self.effort.title())
        if self.kind == "budget":
            return f"Budget {self.budget}"
        return self.kind.title()

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.kind == "effort":
            payload["effort"] = self.effort
        elif self.kind == "budget":
            payload["budget"] = self.budget
        return payload

    @classmethod
    def from_json(cls, value: Any) -> ReasoningSelection:
        if value in (None, "", PROVIDER_DEFAULT, "default", "auto"):
            return cls()
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw in {"on", "off"}:
                return cls(kind=raw)
            return cls(kind="effort", effort=raw)
        if not isinstance(value, Mapping):
            raise ValueError("Reasoning selection must be an object.")
        return cls(
            kind=str(value.get("kind") or PROVIDER_DEFAULT),
            effort=str(value.get("effort") or ""),
            budget=int(value.get("budget") or 0),
        )


BACKGROUND_SCOPE = "background"


@dataclass(frozen=True)
class ReasoningRequestPlan:
    model_ref: str
    selection: ReasoningSelection = ReasoningSelection()
    capabilities: ReasoningCapabilities | None = None
    # "background": chosen automatically for a background call, never the person's
    # choice, so a rejection never resets or reports the conversation's setting.
    scope: str = ""

    @property
    def is_default(self) -> bool:
        return self.selection.is_default

    @property
    def is_background(self) -> bool:
        return self.scope == BACKGROUND_SCOPE

    @property
    def fingerprint(self) -> str:
        if self.is_default:
            return "reasoning:provider-default"
        payload: dict[str, Any] = {"model_ref": self.model_ref, "selection": self.selection.to_json()}
        if self.scope:
            # A background plan may build different request fields for the same selection.
            payload["scope"] = self.scope
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "reasoning:" + sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate_reasoning_selection(
    selection: ReasoningSelection,
    capabilities: ReasoningCapabilities | None,
) -> ReasoningSelection:
    if selection.is_default:
        return selection
    if capabilities is None or not capabilities.controllable:
        raise ValueError("Reasoning controls are not available for this model.")
    if selection.kind == "effort":
        if selection.effort not in capabilities.supported_efforts:
            raise ValueError(f"Reasoning effort '{selection.effort}' is not supported by this model.")
    elif selection.kind == "off":
        if capabilities.mandatory or not capabilities.can_disable:
            raise ValueError("Reasoning cannot be disabled for this model.")
    elif selection.kind == "on":
        if capabilities.thinking_mode != "toggle":
            raise ValueError("This model does not expose an On/Off reasoning control.")
    elif selection.kind == "budget":
        if not capabilities.supports_budget:
            raise ValueError("This model does not support a reasoning budget.")
        if selection.budget < capabilities.budget_min:
            raise ValueError(f"Reasoning budget must be at least {capabilities.budget_min}.")
        if capabilities.budget_max and selection.budget > capabilities.budget_max:
            raise ValueError(f"Reasoning budget must be at most {capabilities.budget_max}.")
    return selection


def reasoning_choices(capabilities: ReasoningCapabilities | None) -> tuple[ReasoningSelection, ...]:
    if capabilities is None or not capabilities.controllable:
        return ()
    choices = [ReasoningSelection()]
    if capabilities.thinking_mode == "toggle":
        choices.append(ReasoningSelection(kind="on"))
    choices.extend(ReasoningSelection(kind="effort", effort=value) for value in capabilities.supported_efforts)
    if capabilities.can_disable and not capabilities.mandatory:
        choices.append(ReasoningSelection(kind="off"))
    return tuple(choices)


def resolve_reasoning_capabilities(provider_id: str, model_id: str) -> ReasoningCapabilities | None:
    provider = "ollama" if str(provider_id or "") == "local" else str(provider_id or "").strip()
    model = str(model_id or "").strip()
    if not provider or not model:
        return None
    resolved_source = ""
    try:
        from row_bot.providers.capability_resolution import resolve_capability_metadata

        resolved = resolve_capability_metadata(provider, model)
        resolved_source = resolved.source
        live = ReasoningCapabilities.from_json(resolved.snapshot.get("reasoning"))
        if live:
            if not live.source:
                live = ReasoningCapabilities(**{**live.to_json(), "source": resolved.source})
            live = _with_default_request_style(live, provider)
            if provider.startswith("custom_openai_") or provider in {"requesty", "atlascloud", "minimax"}:
                return live if live.request_style else None
            return live
    except Exception:
        logger.debug("Reasoning capability cache lookup failed for %s/%s", provider, model, exc_info=True)
    fallback = _EXACT_REASONING_CAPABILITIES.get((provider, model))
    if fallback:
        return fallback
    if provider in {"opencode_zen", "opencode_go"}:
        return _opencode_fallback_capabilities(provider, model)
    if provider in {"ollama", "ollama_cloud"}:
        if resolved_source == "ollama_catalog_cache":
            return None  # the catalog already chose, from Ollama's own answer where it gave one (F12)
        return _ollama_fallback_capabilities(model)
    return None


def reasoning_metadata_for_catalog(
    provider_id: str,
    model_id: str,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    raw = metadata or {}
    nested = raw.get("reasoning")
    caps = ReasoningCapabilities.from_json(nested)
    if caps:
        return _with_default_request_style(caps, provider_id).to_json()
    fallback = _EXACT_REASONING_CAPABILITIES.get((provider_id, model_id))
    if fallback:
        return fallback.to_json()
    if provider_id in {"opencode_zen", "opencode_go"}:
        fallback = _opencode_fallback_capabilities(provider_id, model_id)
    elif provider_id in {"ollama", "ollama_cloud"}:
        from row_bot.providers.ollama import ollama_runtime_capabilities

        # Ollama listed its capabilities without "thinking": no family-list guess (F12).
        if ollama_runtime_capabilities(raw) is None:
            fallback = _ollama_fallback_capabilities(model_id)
    return fallback.to_json() if fallback else None


def resolve_reasoning_capabilities_for_ref(value: str | None) -> ReasoningCapabilities | None:
    parsed = parse_model_ref(str(value or ""))
    return resolve_reasoning_capabilities(*parsed) if parsed else None


def request_plan_for(
    thread_id: str | None,
    canonical_model_ref: str,
    *,
    use_snapshot: bool = True,
) -> ReasoningRequestPlan:
    snapshot = _active_reasoning_snapshot.get() if use_snapshot else None
    if (snapshot is not None and snapshot.get("conversation_id") == str(thread_id or "")
            and snapshot.get("model_ref") == canonical_model_ref):
        return ReasoningRequestPlan(
            canonical_model_ref,
            ReasoningSelection.from_json(snapshot.get("selection")),
            ReasoningCapabilities.from_json(snapshot.get("capabilities")),
        )
    caps = resolve_reasoning_capabilities_for_ref(canonical_model_ref)
    selection = ReasoningSelection()
    if thread_id and caps:
        try:
            from row_bot.threads import get_thread_reasoning_selection, set_thread_reasoning_selection

            saved = get_thread_reasoning_selection(thread_id, canonical_model_ref)
            if saved is not None:
                selection = ReasoningSelection.from_json(saved)
                try:
                    validate_reasoning_selection(selection, caps)
                except ValueError:
                    set_thread_reasoning_selection(thread_id, canonical_model_ref, None)
                    forget_reasoning_choice(canonical_model_ref, selection)
                    queue_reasoning_notice(
                        thread_id,
                        {
                            "kind": "stale_selection",
                            "model_ref": canonical_model_ref,
                            "message": "The saved reasoning setting is no longer supported; Provider default is active.",
                        },
                    )
                    selection = ReasoningSelection()
        except Exception:
            logger.warning("Could not load reasoning selection for thread %s", thread_id, exc_info=True)
    key = (str(thread_id or ""), canonical_model_ref)
    if key in _suppressed_overrides:
        selection = ReasoningSelection()
    return ReasoningRequestPlan(canonical_model_ref, selection, caps)


_active_reasoning_snapshot: ContextVar[dict[str, Any] | None] = ContextVar(
    "row_bot_reasoning_snapshot", default=None)


def activate_reasoning_snapshot(snapshot: dict[str, Any] | None = None) -> None:
    """Set or clear the immutable request cut at the existing invocation boundary."""
    _active_reasoning_snapshot.set(deepcopy(snapshot) if isinstance(snapshot, dict) else None)


def canonical_reasoning_model_ref(provider_id: str, model_id: str) -> str:
    return model_ref(str(provider_id or "").strip(), str(model_id or "").strip())


def selection_from_command(argument: str) -> ReasoningSelection:
    parts = str(argument or "").strip().lower().split()
    if not parts:
        raise ValueError("missing")
    if parts[0] in {"default", "auto"} and len(parts) == 1:
        return ReasoningSelection()
    if parts[0] in {"on", "off"} and len(parts) == 1:
        return ReasoningSelection(kind=parts[0])
    if parts[0] == "budget" and len(parts) == 2:
        try:
            return ReasoningSelection(kind="budget", budget=int(parts[1]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Reasoning budget must be a positive integer.") from exc
    if len(parts) == 1:
        return ReasoningSelection(kind="effort", effort=parts[0])
    raise ValueError("Use /reasoning default, an effort, on, off, or budget <tokens>.")


def format_reasoning_choices(capabilities: ReasoningCapabilities) -> str:
    values = ["default"]
    if capabilities.thinking_mode == "toggle":
        values.append("on")
    values.extend(capabilities.supported_efforts)
    if capabilities.can_disable and not capabilities.mandatory:
        values.append("off")
    if capabilities.supports_budget:
        values.append("budget <tokens>")
    return ", ".join(values)


def apply_reasoning_command(thread_id: str, canonical_model_ref: str, argument: str) -> str:
    caps = resolve_reasoning_capabilities_for_ref(canonical_model_ref)
    if caps is None or not caps.controllable:
        return f"Reasoning controls are not available for {canonical_model_ref}."
    from row_bot.threads import get_thread_reasoning_selection, set_thread_reasoning_selection

    if not str(argument or "").strip():
        saved = get_thread_reasoning_selection(thread_id, canonical_model_ref)
        try:
            selection = validate_reasoning_selection(ReasoningSelection.from_json(saved), caps)
        except ValueError:
            set_thread_reasoning_selection(thread_id, canonical_model_ref, None)
            selection = ReasoningSelection()
        return (
            f"Reasoning for {canonical_model_ref}: {selection.label}. "
            f"Valid choices: {format_reasoning_choices(caps)}."
        )
    try:
        selection = validate_reasoning_selection(selection_from_command(argument), caps)
    except ValueError as exc:
        return f"{exc} Valid choices for {canonical_model_ref}: {format_reasoning_choices(caps)}."
    set_thread_reasoning_selection(
        thread_id,
        canonical_model_ref,
        None if selection.is_default else selection.to_json(),
    )
    clear_reasoning_suppression(thread_id, canonical_model_ref)
    remember_reasoning_choice(canonical_model_ref, selection)
    return f"Reasoning for {canonical_model_ref}: {selection.label}."


@dataclass(frozen=True)
class HelperReasoning:
    """What a delegated helper thinks at, chosen once when it starts (B386)."""

    selection: ReasoningSelection = ReasoningSelection()
    # Set when the conversation's level could not carry over to another model.
    not_offered: str = ""


def helper_reasoning(
    parent_thread_id: str,
    parent_model_ref: str,
    child_model_ref: str,
    requested: str = "",
) -> HelperReasoning:
    """A helper follows the conversation's reasoning unless it is given its own.

    ``requested`` uses the /reasoning words; a level the helper's model does not
    offer raises ValueError naming the valid choices. Without one, the helper
    keeps the conversation's level when its model offers it, else the model's
    default, saying which level did not carry over.
    """
    caps = resolve_reasoning_capabilities_for_ref(child_model_ref) if child_model_ref else None
    if str(requested or "").strip():
        if caps is None or not caps.controllable:
            selection = selection_from_command(requested)
            if selection.is_default:
                return HelperReasoning(selection)
            raise ValueError(f"Reasoning controls are not available for {child_model_ref or 'this model'}.")
        try:
            return HelperReasoning(validate_reasoning_selection(selection_from_command(requested), caps))
        except ValueError as exc:
            raise ValueError(
                f"{exc} Valid choices for {child_model_ref}: {format_reasoning_choices(caps)}."
            ) from exc
    if not parent_model_ref:
        return HelperReasoning()
    inherited = request_plan_for(parent_thread_id or None, parent_model_ref).selection
    if inherited.is_default or child_model_ref == parent_model_ref:
        return HelperReasoning(inherited)
    try:
        return HelperReasoning(validate_reasoning_selection(inherited, caps))
    except ValueError:
        return HelperReasoning(ReasoningSelection(), not_offered=inherited.label)


_reasoning_notices: dict[str, list[dict[str, Any]]] = {}
_suppressed_overrides: set[tuple[str, str]] = set()


def queue_reasoning_notice(thread_id: str | None, payload: Mapping[str, Any]) -> None:
    if not thread_id:
        return
    _reasoning_notices.setdefault(str(thread_id), []).append(dict(payload))


def consume_reasoning_notices(thread_id: str | None) -> list[dict[str, Any]]:
    if not thread_id:
        return []
    return _reasoning_notices.pop(str(thread_id), [])


def suppress_reasoning_override(thread_id: str | None, canonical_model_ref: str) -> None:
    _suppressed_overrides.add((str(thread_id or ""), canonical_model_ref))


# Models whose provider refused a lowered background reasoning setting in this
# process: later background calls use the provider default straight away.
_background_rejections: set[str] = set()


def remember_background_rejection(canonical_model_ref: str) -> None:
    _background_rejections.add(str(canonical_model_ref or ""))


def background_reasoning_rejected(canonical_model_ref: str) -> bool:
    return str(canonical_model_ref or "") in _background_rejections


def clear_reasoning_suppression(thread_id: str | None, canonical_model_ref: str) -> None:
    _suppressed_overrides.discard((str(thread_id or ""), canonical_model_ref))


# A new chat starts on the Thinking choice the person last made for each model (#80), as it
# starts on the approval level they last chose (#687). Chats they already have keep theirs.
# Kept in model_settings.json beside the starred models.
REMEMBERED_REASONING_KEY = "reasoning_last"


def _model_settings_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / "model_settings.json"


def remembered_reasoning_choices() -> dict[str, dict[str, Any]]:
    """The Thinking choice the person last made for each model, by canonical model ref."""
    from row_bot.providers.saved_model_settings import read_saved_model_settings

    try:
        raw, _revision, _exists = read_saved_model_settings(_model_settings_path())
    except Exception:
        logger.warning("Remembered Thinking choices could not be read; new chats start on Default")
        return {}
    saved = raw.get(REMEMBERED_REASONING_KEY)
    found: dict[str, dict[str, Any]] = {}
    for ref, value in saved.items() if isinstance(saved, dict) else ():
        try:
            selection = ReasoningSelection.from_json(value)
        except (TypeError, ValueError):
            continue
        if parse_model_ref(str(ref)) is not None and not selection.is_default:
            found[str(ref)] = selection.to_json()
    return found


def remember_reasoning_choice(canonical_model_ref: str, selection: ReasoningSelection) -> None:
    """The person chose *selection* for this model: new chats with it start there. Default forgets it."""
    _update_remembered_choice(canonical_model_ref, selection)


def forget_reasoning_choice(canonical_model_ref: str, selection: ReasoningSelection) -> None:
    """The model refused or no longer offers *selection*: new chats stop starting there."""
    _update_remembered_choice(canonical_model_ref, ReasoningSelection(), only_if=selection)


def _update_remembered_choice(
    canonical_model_ref: str,
    selection: ReasoningSelection,
    *,
    only_if: ReasoningSelection | None = None,
) -> None:
    from row_bot.providers.saved_model_settings import update_saved_model_settings

    current = remembered_reasoning_choices().get(canonical_model_ref)
    if only_if is not None and current != only_if.to_json():
        return
    if current == (None if selection.is_default else selection.to_json()):
        return

    def merge(raw: dict) -> dict:
        saved = raw.get(REMEMBERED_REASONING_KEY)
        choices = dict(saved) if isinstance(saved, dict) else {}
        if only_if is not None and choices.get(canonical_model_ref) != only_if.to_json():
            return raw
        if selection.is_default:
            choices.pop(canonical_model_ref, None)
        else:
            choices[canonical_model_ref] = selection.to_json()
        merged = {key: value for key, value in raw.items() if key != REMEMBERED_REASONING_KEY}
        if choices:
            merged[REMEMBERED_REASONING_KEY] = choices
        return merged

    try:
        update_saved_model_settings(merge, path=_model_settings_path())
    except Exception:
        # The chat keeps the choice either way; only new chats miss it.
        logger.warning("The Thinking choice could not be remembered for new chats", exc_info=True)


def _positive_int(value: Any, *, default: int, allow_zero: bool = False) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed > 0 or (allow_zero and parsed == 0):
        return parsed
    return default


def _caps(
    efforts: tuple[str, ...] = (),
    *,
    style: str,
    can_disable: bool = False,
    mandatory: bool = False,
    thinking_mode: str = "none",
    supports_budget: bool = False,
    budget_min: int = 1,
    budget_max: int = 0,
) -> ReasoningCapabilities:
    return ReasoningCapabilities(
        supported_efforts=efforts,
        can_disable=can_disable,
        mandatory=mandatory,
        supports_budget=supports_budget,
        budget_min=budget_min,
        budget_max=budget_max,
        thinking_mode=thinking_mode,
        source="documented_exact_fallback",
        revision="2026-08-19",
        request_style=style,
    )


_OPENAI_EFFORTS = ("minimal", "low", "medium", "high")
_CODEX_EFFORTS = ("low", "medium", "high", "xhigh")
_ANTHROPIC_EFFORTS = ("low", "medium", "high", "max")

_EXACT_REASONING_CAPABILITIES: dict[tuple[str, str], ReasoningCapabilities] = {}

for _provider in ("openai",):
    for _model in ("gpt-5", "gpt-5-mini", "gpt-5-nano"):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(_OPENAI_EFFORTS, style="openai")
    for _model in ("o3", "o3-mini", "o4-mini"):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(("low", "medium", "high"), style="openai")

for _provider in ("codex",):
    for _model in (
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4",
        "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2",
    ):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(_CODEX_EFFORTS, style="openai_responses")

for _provider in ("anthropic", "claude_subscription"):
    for _model in ("claude-sonnet-4-6", "claude-opus-4-8"):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(
            _ANTHROPIC_EFFORTS,
            style="anthropic",
            thinking_mode="adaptive",
        )

for _provider in ("xai", "xai_oauth"):
    for _model in ("grok-4.5",):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(("low", "medium", "high"), style="xai", mandatory=True)
    for _model in ("grok-4.6",):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(("low", "medium", "high", "xhigh"), style="xai", mandatory=True)
    for _model in ("grok-3-mini", "grok-3-mini-fast"):
        _EXACT_REASONING_CAPABILITIES[(_provider, _model)] = _caps(("low", "high"), style="xai", mandatory=True)

for _model, _minimum, _maximum, _disable in (
    ("gemini-2.5-pro", 128, 32768, False),
    ("gemini-2.5-flash", 0, 24576, True),
    ("gemini-2.5-flash-lite", 0, 24576, True),
):
    _EXACT_REASONING_CAPABILITIES[("google", _model)] = _caps(
        style="google_budget",
        can_disable=_disable,
        thinking_mode="manual",
        supports_budget=True,
        budget_min=_minimum,
        budget_max=_maximum,
    )
for _model in ("gemini-3-flash-preview", "gemini-3.1-pro-preview"):
    _EXACT_REASONING_CAPABILITIES[("google", _model)] = _caps(("low", "high"), style="google_level")


def _with_default_request_style(caps: ReasoningCapabilities, provider_id: str) -> ReasoningCapabilities:
    if caps.request_style:
        return caps
    style = {
        "openrouter": "openrouter",
        "openai": "openai",
        "codex": "openai_responses",
        "xai": "xai",
        "xai_oauth": "xai_responses",
        "anthropic": "anthropic",
        "claude_subscription": "anthropic",
        "google": "google_level",
        "ollama": "ollama",
        "ollama_cloud": "ollama",
    }.get(provider_id, "")
    return ReasoningCapabilities(**{**caps.to_json(), "request_style": style})


def ollama_thinking_capabilities(model_id: str, *, source: str = "") -> ReasoningCapabilities:
    """How a thinking Ollama model is controlled: gpt-oss takes levels, the others turn thinking on or off."""
    from row_bot.providers.ollama import normalize_ollama_family

    if normalize_ollama_family(model_id) == "gpt-oss":
        caps = _caps(("low", "medium", "high"), style="ollama", mandatory=True)
    else:
        caps = _caps(style="ollama", can_disable=True, thinking_mode="toggle")
    return ReasoningCapabilities(**{**caps.to_json(), "source": source}) if source else caps


def _ollama_fallback_capabilities(model_id: str) -> ReasoningCapabilities | None:
    try:
        from row_bot.providers.ollama import is_ollama_reasoning_model

        if is_ollama_reasoning_model(model_id):
            return ollama_thinking_capabilities(model_id)
    except Exception:
        return None
    return None


def _opencode_fallback_capabilities(provider_id: str, model_id: str) -> ReasoningCapabilities | None:
    try:
        from row_bot.providers.models import TransportMode
        from row_bot.providers.opencode import opencode_known_route

        route = opencode_known_route(provider_id, model_id)
        if route is None:
            return None
        if route.transport == TransportMode.OPENAI_RESPONSES:
            base = _EXACT_REASONING_CAPABILITIES.get(("codex", model_id)) or _EXACT_REASONING_CAPABILITIES.get(("openai", model_id))
        elif route.transport == TransportMode.ANTHROPIC_MESSAGES:
            base = _EXACT_REASONING_CAPABILITIES.get(("anthropic", model_id))
        else:
            base = None
        if base:
            return ReasoningCapabilities(**{**base.to_json(), "source": "opencode_exact_route"})
    except Exception:
        return None
    return None
