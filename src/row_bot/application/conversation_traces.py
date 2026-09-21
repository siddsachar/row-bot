"""Pure, bounded semantics for public conversation tool traces.

This module intentionally has no UI, checkpoint, provider, or transport
dependency.  Callers supply durable identities and ordering; the helpers only
classify and reduce already-public result values into bounded display metadata.
Arbitrary persisted objects are never deserialized here.  The only structured
string format accepted is size-limited JSON.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal


TraceStatus = Literal[
    "pending",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "uncertain",
]
TraceGroupKind = Literal["generic", "browser", "computer"]
TraceSpecializationKind = Literal["skill_load", "delegated_agent", "media"]

TRACE_STATUSES: frozenset[str] = frozenset(
    {"pending", "succeeded", "failed", "blocked", "cancelled", "uncertain"}
)
MAX_TOOL_NAME_CHARS = 180
MAX_IDENTIFIER_CHARS = 256
MAX_SUMMARY_CHARS = 512
MAX_STRUCTURED_PAYLOAD_CHARS = 32 * 1024
MAX_STRUCTURED_NODES = 512
MAX_STRUCTURED_DEPTH = 8
MAX_AGENT_REFERENCES = 16
MAX_MEDIA_REFERENCES = 8

AGENT_TOOL_NAMES = {
    "agents",
    "delegate_work",
    "agent_status",
    "agent_wait",
    "agent_stop",
    "agent_message",
    "agent_retry",
}


@dataclass(frozen=True)
class DelegatedAgentReference:
    """The public identity/state needed to link one delegated Agent card."""

    run_id: str
    display_name: str
    status: str


@dataclass(frozen=True)
class MediaReference:
    """A reviewed resource reference, never inline media or marker payload."""

    media_ref: str
    mime_type: str


@dataclass(frozen=True)
class TraceSpecialization:
    """Closed specialized presentation metadata without the raw result body."""

    kind: TraceSpecializationKind
    skill_id: str = ""
    display_name: str = ""
    source: str = ""
    newly_active: bool | None = None
    evicted_skill_id: str = ""
    agent_runs: tuple[DelegatedAgentReference, ...] = ()
    media_kind: str = ""
    media: tuple[MediaReference, ...] = ()


@dataclass(frozen=True)
class TraceItem:
    """One identity-stable call/result projection supplied by its caller."""

    item_id: str
    group_id: str
    call_id: str
    result_message_id: str
    call_order: int
    group_order: int
    canonical_name: str
    group_name: str
    group_kind: TraceGroupKind
    status: TraceStatus
    safe_summary: str
    summary_truncated: bool
    content_ref: str
    specialization: TraceSpecialization | None


@dataclass(frozen=True)
class TraceGroup:
    """A caller-scoped group with explicit group and call order."""

    group_id: str
    name: str
    kind: TraceGroupKind
    group_order: int
    status: TraceStatus
    items: tuple[TraceItem, ...]

    @property
    def counts(self) -> dict[TraceStatus, int]:
        return {
            status: sum(item.status == status for item in self.items)
            for status in (
                "pending",
                "succeeded",
                "failed",
                "blocked",
                "cancelled",
                "uncertain",
            )
        }


@dataclass
class ToolResultGroup:
    """Legacy display group retained for the NiceGUI renderer."""

    name: str
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.results)

    @property
    def label(self) -> str:
        if is_browser_tool_name(self.name):
            suffix = "step" if self.count == 1 else "steps"
            return f"Browser activity · {self.count} {suffix}"
        if is_computer_tool_name(self.name):
            suffix = "step" if self.count == 1 else "steps"
            return f"Computer activity · {self.count} {suffix}"
        suffix = "call" if self.count == 1 else "calls"
        return f"{self.name} · {self.count} {suffix}"


def _clean_text(value: Any, maximum: int, *, fallback: str = "") -> str:
    text = str(value or "").strip()[:maximum]
    text = "".join(
        character
        for character in text
        if character in "\n\t" or ord(character) >= 32
    )
    return (text or fallback) if maximum > 0 else ""


def _identifier(
    value: Any,
    name: str,
    *,
    required: bool = True,
    maximum: int = MAX_IDENTIFIER_CHARS,
) -> str:
    clean = _clean_text(value, maximum)
    if required and not clean:
        raise ValueError(f"{name}_required")
    if clean != str(value or "").strip():
        raise ValueError(f"{name}_invalid")
    return clean


def canonical_tool_name(name: Any) -> str:
    """Return the bounded canonical display name shared by both clients."""

    clean = _clean_text(name, MAX_TOOL_NAME_CHARS, fallback="tool")
    if clean.startswith("browser_"):
        return clean.replace("browser_", "Browser ").replace("_", " ").title()
    if clean == "computer_use":
        return "Computer activity"
    return clean


def is_browser_tool_name(name: Any) -> bool:
    clean = str(name or "").strip().lower()
    return clean.startswith("browser_") or clean.startswith("browser ")


def is_computer_tool_name(name: Any) -> bool:
    clean = str(name or "").strip().lower()
    return clean in {"computer_use", "computer activity"} or clean.startswith(
        "computer ·"
    )


def canonical_group(name: Any) -> tuple[str, TraceGroupKind]:
    """Return the canonical grouping label/family without inventing identity."""

    canonical = canonical_tool_name(name)
    if is_browser_tool_name(canonical):
        return "Browser activity", "browser"
    if is_computer_tool_name(canonical):
        return "Computer activity", "computer"
    return canonical, "generic"


def group_tool_results(
    tool_results: list[dict[str, Any]] | None,
) -> list[ToolResultGroup]:
    """Keep the retained renderer's first-seen grouping behavior."""

    grouped: OrderedDict[str, ToolResultGroup] = OrderedDict()
    for result in tool_results or []:
        name = canonical_tool_name(
            result.get("name", "tool") if isinstance(result, dict) else "tool"
        )
        key, _ = canonical_group(name)
        if key not in grouped:
            grouped[key] = ToolResultGroup(name=key)
        grouped[key].results.append(
            result
            if isinstance(result, dict)
            else {"name": name, "content": str(result)}
        )
    return list(grouped.values())


def _bounded_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        text = value.strip()
        if (
            not text.startswith("{")
            or not text.endswith("}")
            or len(text) > MAX_STRUCTURED_PAYLOAD_CHARS
        ):
            return None
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    else:
        return None
    if not isinstance(payload, dict):
        return None

    nodes = 0

    def bounded(item: Any, depth: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_STRUCTURED_NODES or depth > MAX_STRUCTURED_DEPTH:
            return False
        if isinstance(item, dict):
            return all(
                isinstance(key, str)
                and len(key) <= 128
                and bounded(child, depth + 1)
                for key, child in item.items()
            )
        if isinstance(item, list):
            return len(item) <= 128 and all(
                bounded(child, depth + 1) for child in item
            )
        return not isinstance(item, str) or len(item) <= MAX_STRUCTURED_PAYLOAD_CHARS

    return payload if bounded(payload, 0) else None


def _content(value: Any) -> Any:
    if isinstance(value, dict) and "content" in value:
        return value.get("content")
    return value


def classify_tool_result(
    result_or_content: Any,
    *,
    pending: bool = False,
    external_outcome: str = "",
) -> TraceStatus:
    """Classify one result into the closed public trace state set."""

    if pending:
        return "pending"
    if str(external_outcome or "").strip().casefold() == "uncertain":
        return "uncertain"

    wrapper = result_or_content if isinstance(result_or_content, dict) else {}
    content = _content(result_or_content)
    payload = _bounded_json_object(content)
    candidates = [wrapper]
    if payload is not None and payload is not wrapper:
        candidates.append(payload)

    aliases: dict[str, TraceStatus] = {
        "pending": "pending",
        "running": "pending",
        "success": "succeeded",
        "succeeded": "succeeded",
        "complete": "succeeded",
        "completed": "succeeded",
        "done": "succeeded",
        "error": "failed",
        "failed": "failed",
        "blocked": "blocked",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "uncertain": "uncertain",
        "unknown": "uncertain",
    }
    for candidate in candidates:
        if candidate.get("uncertain") is True:
            return "uncertain"
        if candidate.get("blocked") is True:
            return "blocked"
        if candidate.get("cancelled") is True or candidate.get("canceled") is True:
            return "cancelled"
        status = str(candidate.get("status") or "").strip().casefold()
        if status in aliases:
            return aliases[status]
        if candidate.get("error") is True or candidate.get("ok") is False:
            return "failed"

    text = str(content or "").strip().casefold()
    if text.startswith(("uncertain:", "outcome uncertain:")):
        return "uncertain"
    if text.startswith(("blocked:", "tool blocked:")):
        return "blocked"
    if text.startswith(("cancelled:", "canceled:", "tool cancelled:")):
        return "cancelled"
    if text.startswith(("tool error:", "error:")) or (
        "traceback (most recent call last)" in text
    ):
        return "failed"
    return "succeeded"


def tool_result_failed(result_or_content: Any) -> bool:
    """Whether a settled result needs attention in the retained UI."""

    return classify_tool_result(result_or_content) in {
        "failed",
        "blocked",
        "cancelled",
        "uncertain",
    }


def _summary_text(result_or_content: Any) -> str:
    content = _content(result_or_content)
    payload = _bounded_json_object(content)
    if payload is not None:
        if payload.get("display_summary"):
            return str(payload.get("display_summary") or "")
        # Structured result bodies can contain private fields.  The expanded
        # public body remains available through its separately authorized page.
        return ""
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            # Invalid or over-bound structured data is never downgraded into a
            # raw preview, which could expose fields the safe parser rejected.
            return ""
        if content.startswith("__IMAGE__:"):
            return "Image generated"
        if content.startswith("__CHART__:"):
            return "Chart created"
        if content.startswith("__HTML__:"):
            return "Interactive result available"
        return content
    if isinstance(content, list):
        parts: list[str] = []
        size = 0
        for part in content[:64]:
            text = (
                str(part.get("text") or "")
                if isinstance(part, dict) and part.get("type") == "text"
                else ""
            )
            if not text:
                continue
            parts.append(text)
            size += len(text)
            if size > MAX_SUMMARY_CHARS:
                break
        return " ".join(parts)
    return ""


def bounded_safe_summary(
    result_or_content: Any, *, maximum: int = MAX_SUMMARY_CHARS
) -> tuple[str, bool]:
    """Return escaped-at-render-time public text under an explicit char bound."""

    maximum = min(MAX_SUMMARY_CHARS, max(0, int(maximum)))
    raw = _summary_text(result_or_content)
    clean = _clean_text(raw, maximum + 1)
    truncated = len(clean) > maximum
    return clean[:maximum], truncated


def _skill_specialization(name: str, payload: dict[str, Any] | None) -> TraceSpecialization | None:
    if name != "skill_load" or not payload:
        return None
    if payload.get("ok") is not True or payload.get("kind") != "skill_loaded":
        return None
    newly_active = payload.get("newly_active")
    if not isinstance(newly_active, bool):
        return None
    skill_id = _clean_text(payload.get("skill_id"), 180)
    display_name = _clean_text(payload.get("display_name"), 180)
    source = _clean_text(payload.get("source"), 180)
    evicted = _clean_text(payload.get("evicted_skill_id"), 180)
    if not skill_id or not display_name:
        return None
    return TraceSpecialization(
        kind="skill_load",
        skill_id=skill_id,
        display_name=display_name,
        source=source,
        newly_active=newly_active,
        evicted_skill_id=evicted,
    )


def _agent_specialization(name: str, payload: dict[str, Any] | None) -> TraceSpecialization | None:
    if not payload or (
        name.casefold() not in AGENT_TOOL_NAMES
        and not (
            isinstance(payload.get("run"), dict)
            or isinstance(payload.get("runs"), list)
        )
    ):
        return None
    raw_runs: list[Any] = []
    if isinstance(payload.get("run"), dict):
        raw_runs.append(payload["run"])
    if isinstance(payload.get("runs"), list):
        raw_runs.extend(payload["runs"])
    runs: list[DelegatedAgentReference] = []
    seen: set[str] = set()
    for raw in raw_runs[:MAX_STRUCTURED_NODES]:
        if len(runs) >= MAX_AGENT_REFERENCES:
            break
        if not isinstance(raw, dict):
            continue
        run_id = _clean_text(raw.get("id"), 256)
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        runs.append(
            DelegatedAgentReference(
                run_id=run_id,
                display_name=_clean_text(raw.get("display_name"), 256, fallback="Delegated task"),
                status=_clean_text(raw.get("status"), 64, fallback="unknown"),
            )
        )
    return (
        TraceSpecialization(kind="delegated_agent", agent_runs=tuple(runs))
        if runs
        else None
    )


def _media_specialization(result: Any) -> TraceSpecialization | None:
    wrapper = result if isinstance(result, dict) else {}
    content = _content(result)
    kind = ""
    if isinstance(content, str):
        marker = re.match(r"^__(IMAGE|CHART|HTML)__:", content)
        if marker:
            kind = marker.group(1).casefold()
    references: list[MediaReference] = []
    raw_media = wrapper.get("media")
    if isinstance(raw_media, list):
        for raw in raw_media[:MAX_MEDIA_REFERENCES]:
            if not isinstance(raw, dict):
                continue
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
            media_ref = _clean_text(payload.get("media_ref"), 256)
            mime_type = _clean_text(payload.get("mime_type"), 128)
            if media_ref and mime_type:
                references.append(MediaReference(media_ref=media_ref, mime_type=mime_type))
    if references and not kind:
        kind = "attachment"
    return (
        TraceSpecialization(kind="media", media_kind=kind, media=tuple(references))
        if kind
        else None
    )


def specialize_tool_result(result: Any) -> TraceSpecialization | None:
    """Return only reviewed specialization metadata, never the raw payload."""

    wrapper = result if isinstance(result, dict) else {}
    name = _clean_text(wrapper.get("name"), MAX_TOOL_NAME_CHARS)
    payload = _bounded_json_object(_content(result))
    return (
        _skill_specialization(name, payload)
        or _agent_specialization(name, payload)
        or _media_specialization(result)
    )


def build_trace_item(
    *,
    item_id: str,
    group_id: str,
    call_id: str,
    call_order: int,
    group_order: int,
    tool_name: str,
    result: Any = None,
    result_message_id: str = "",
    pending: bool = False,
    external_outcome: str = "",
    content_ref: str = "",
) -> TraceItem:
    """Build one bounded item while retaining caller-owned identity/order."""

    if type(call_order) is not int or call_order < 0:
        raise ValueError("call_order_invalid")
    if type(group_order) is not int or group_order < 0:
        raise ValueError("group_order_invalid")
    canonical = canonical_tool_name(tool_name)
    group_name, group_kind = canonical_group(canonical)
    summary, truncated = bounded_safe_summary(result)
    return TraceItem(
        item_id=_identifier(item_id, "item_id"),
        group_id=_identifier(group_id, "group_id"),
        call_id=_identifier(call_id, "call_id"),
        result_message_id=_identifier(
            result_message_id, "result_message_id", required=False
        ),
        call_order=call_order,
        group_order=group_order,
        canonical_name=canonical,
        group_name=group_name,
        group_kind=group_kind,
        status=classify_tool_result(
            result, pending=pending, external_outcome=external_outcome
        ),
        safe_summary=summary,
        summary_truncated=truncated,
        content_ref=_identifier(content_ref, "content_ref", required=False),
        specialization=specialize_tool_result(result),
    )


def _aggregate_status(items: tuple[TraceItem, ...]) -> TraceStatus:
    statuses = {item.status for item in items}
    for status in ("uncertain", "blocked", "failed", "cancelled", "pending"):
        if status in statuses:
            return status  # type: ignore[return-value]
    return "succeeded"


def group_trace_items(items: list[TraceItem] | tuple[TraceItem, ...]) -> list[TraceGroup]:
    """Validate and order caller-scoped groups without grouping by display text."""

    item_ids: set[str] = set()
    call_ids: set[str] = set()
    grouped: dict[str, list[TraceItem]] = {}
    for item in items:
        if item.item_id in item_ids:
            raise ValueError("duplicate_trace_item_id")
        if item.call_id in call_ids:
            raise ValueError("duplicate_trace_call_id")
        item_ids.add(item.item_id)
        call_ids.add(item.call_id)
        grouped.setdefault(item.group_id, []).append(item)

    projected: list[TraceGroup] = []
    for group_id, members in grouped.items():
        first = members[0]
        if any(
            item.group_name != first.group_name
            or item.group_kind != first.group_kind
            or item.group_order != first.group_order
            for item in members[1:]
        ):
            raise ValueError("trace_group_conflict")
        ordered = tuple(sorted(members, key=lambda item: item.call_order))
        if len({item.call_order for item in ordered}) != len(ordered):
            raise ValueError("duplicate_trace_call_order")
        projected.append(
            TraceGroup(
                group_id=group_id,
                name=first.group_name,
                kind=first.group_kind,
                group_order=first.group_order,
                status=_aggregate_status(ordered),
                items=ordered,
            )
        )
    if len({group.group_order for group in projected}) != len(projected):
        raise ValueError("duplicate_trace_group_order")
    return sorted(projected, key=lambda group: group.group_order)


def _public_specialization(
    specialization: TraceSpecialization | None,
) -> dict[str, Any] | None:
    if specialization is None:
        return None
    return {
        "kind": specialization.kind,
        "skill_id": specialization.skill_id,
        "display_name": specialization.display_name,
        "source": specialization.source,
        "newly_active": specialization.newly_active,
        "evicted_skill_id": specialization.evicted_skill_id,
        "agent_runs": [
            {
                "run_id": run.run_id,
                "display_name": run.display_name,
                "status": run.status,
            }
            for run in specialization.agent_runs
        ],
        "media_kind": specialization.media_kind,
        "media": [
            {"media_ref": item.media_ref, "mime_type": item.mime_type}
            for item in specialization.media
        ],
    }


def public_trace_group(group: TraceGroup) -> dict[str, Any]:
    """Serialize one trace group into JSON-compatible bounded public data."""

    return {
        "group_id": group.group_id,
        "name": group.name,
        "kind": group.kind,
        "group_order": group.group_order,
        "status": group.status,
        "counts": dict(group.counts),
        "items": [
            {
                "item_id": item.item_id,
                "group_id": item.group_id,
                "call_id": item.call_id,
                "result_message_id": item.result_message_id,
                "call_order": item.call_order,
                "group_order": item.group_order,
                "canonical_name": item.canonical_name,
                "group_name": item.group_name,
                "group_kind": item.group_kind,
                "status": item.status,
                "safe_summary": item.safe_summary,
                "summary_truncated": item.summary_truncated,
                "content_ref": item.content_ref,
                "specialization": _public_specialization(item.specialization),
            }
            for item in group.items
        ],
    }


def _row_text(row: dict[str, Any]) -> str:
    blocks = row.get("blocks")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    retained = 0
    for block in blocks[:256]:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "")
        if retained >= MAX_STRUCTURED_PAYLOAD_CHARS:
            break
        take = text[: MAX_STRUCTURED_PAYLOAD_CHARS - retained]
        parts.append(take)
        retained += len(take)
    return "\n".join(parts)


def project_assistant_row_traces(
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Attach bounded traces to already-public ordered transcript rows.

    Input records have the shape ``{"row": <public row>, "tool_calls":
    [{"id": ..., "name": ...}]}``.  ``tool_calls`` is used only for an
    assistant row.  A later tool row identifies its call with
    ``row["tool_call_id"]``.  The returned list contains copied public rows:
    initiating assistant rows gain ``traces`` and matched tool rows gain
    ``trace_parent_id``.  Calls without a result remain pending; orphan result
    rows stay untouched.

    Caller-supplied call IDs are the stable item identities.  A canonical
    group uses the first matching call ID as its stable group identity unless
    that call supplies ``group_id`` explicitly.  Numeric call/group order is
    derived from the supplied list order and emitted explicitly.
    """

    output: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}
    parent_calls: dict[str, list[dict[str, Any]]] = {}

    for record_index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("row"), dict):
            raise ValueError("trace_record_invalid")
        row = dict(record["row"])
        output.append(row)
        if str(row.get("role") or "") != "assistant":
            continue
        parent_id = _identifier(
            row.get("id"), "trace_parent_id", maximum=1024
        )
        raw_calls = record.get("tool_calls") or []
        if not isinstance(raw_calls, list) or len(raw_calls) > 256:
            raise ValueError("trace_tool_calls_invalid")
        grouped_ids: dict[tuple[str, TraceGroupKind], str] = {}
        grouped_orders: dict[tuple[str, TraceGroupKind], int] = {}
        call_order = 0
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise ValueError("trace_tool_call_invalid")
            call_id = _identifier(raw_call.get("id"), "call_id")
            if call_id in calls:
                raise ValueError("duplicate_trace_call_id")
            name = canonical_tool_name(raw_call.get("name"))
            group_name, group_kind = canonical_group(name)
            group_key = (group_name, group_kind)
            supplied_group = _identifier(
                raw_call.get("group_id"), "group_id", required=False
            )
            if group_key not in grouped_ids:
                grouped_ids[group_key] = supplied_group or call_id
                grouped_orders[group_key] = len(grouped_orders)
            elif supplied_group and supplied_group != grouped_ids[group_key]:
                raise ValueError("trace_group_conflict")
            projected = {
                "call_id": call_id,
                "item_id": _identifier(
                    raw_call.get("item_id"), "item_id", required=False
                )
                or call_id,
                "group_id": grouped_ids[group_key],
                "call_order": call_order,
                "group_order": grouped_orders[group_key],
                "tool_name": name,
                "parent_id": parent_id,
                "parent_output_index": len(output) - 1,
                "record_index": record_index,
                "result": None,
                "result_row": None,
            }
            calls[call_id] = projected
            parent_calls.setdefault(parent_id, []).append(projected)
            call_order += 1

    matched_results: set[str] = set()
    for record_index, (record, row) in enumerate(zip(records, output)):
        if str(row.get("role") or "") != "tool":
            continue
        call_id = str(row.get("tool_call_id") or "").strip()
        call = calls.get(call_id)
        if call is None or int(call["record_index"]) >= record_index:
            continue
        if call_id in matched_results:
            raise ValueError("duplicate_trace_result")
        matched_results.add(call_id)
        row["trace_parent_id"] = call["parent_id"]
        result: dict[str, Any] = {
            "name": call["tool_name"],
            "content": _row_text(row),
        }
        for field_name in ("status", "error", "ok", "media"):
            if field_name in row:
                result[field_name] = row[field_name]
        call["result"] = result
        call["result_row"] = row

    for parent_id, projected_calls in parent_calls.items():
        items: list[TraceItem] = []
        for call in projected_calls:
            result_row = call["result_row"] or {}
            result_message_id = str(result_row.get("message_id") or "").strip()
            content_ref = str(result_row.get("content_ref") or result_message_id).strip()
            items.append(
                build_trace_item(
                    item_id=call["item_id"],
                    group_id=call["group_id"],
                    call_id=call["call_id"],
                    call_order=call["call_order"],
                    group_order=call["group_order"],
                    tool_name=call["tool_name"],
                    result=call["result"],
                    result_message_id=result_message_id,
                    pending=call["result"] is None,
                    external_outcome=str(result_row.get("external_outcome") or ""),
                    content_ref=content_ref,
                )
            )
        parent_index = int(projected_calls[0]["parent_output_index"])
        if output[parent_index].get("id") != parent_id:
            raise ValueError("trace_parent_conflict")
        output[parent_index]["traces"] = [
            public_trace_group(group) for group in group_trace_items(items)
        ]
    return output
