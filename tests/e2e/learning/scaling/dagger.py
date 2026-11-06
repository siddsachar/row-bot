"""DAgger's relabelling (§4, ``dagger-1000``: two rounds of 500 training targets, one a row, SA1): the teacher's
action at the student's own prefix.

The student (round 1: ``start``; round 2: the round-1 model) runs training-block requests through the real runtime
and acts on its own actions. Each of its model calls is captured as Row-Bot sent it; the teacher is asked, at that
prefix, for the action it would take. A row is **that prefix with the teacher's action**: the student's earlier
steps in the turn stay in it as context (weight 0) and the teacher's action is the only target (weight 1). The
student's prefix is already in the serving shape (it is served at the recipe's context); the row goes through the
product's renderer anyway (``shape.render``, which leaves a served row as it is and bridges a call the teacher made
to a tool only the bridge reaches), and its file paths are kept relative to the workspace (O33), as training rows are.

Asking after the turn instead of during it changes nothing: the teacher's answer never steers the student.

:func:`order` is the round's requests: seeded, stratified by person (``subsets.sequence``), those whose rows are not in
the 1,000-target subset first (SA1's longer training block leaves enough of them).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tests.e2e.learning.scaling import subsets


def order(requests: Mapping[str, Sequence[str]], in_subset: set[str], seed: int | str) -> list[str]:
    """Every training-block request once (``requests``: person → ids): first those not used in the subset, then
    those that are, each part stratified by person and seeded."""

    fresh = {person: [item for item in ids if item not in in_subset] for person, ids in requests.items()}
    used = {person: [item for item in ids if item in in_subset] for person, ids in requests.items()}
    return subsets.sequence(fresh, seed) + subsets.sequence(used, seed)


def row(request: Mapping[str, Any], action: Any, *, envelope: Any, roots: Sequence[str] = ()) -> dict[str, Any] | None:
    """A DAgger row from a captured student request (``capture.read_request``) and the teacher's reply message.
    None when the teacher's reply is empty (no call, no text: nothing to learn)."""

    from langchain_core.messages import message_to_dict

    from row_bot.learning.training import dataset, shape

    system: list[str] = []
    messages: list[dict[str, Any]] = []
    for item in request.get("messages") or ():
        converted = dataset.openai_message(item)
        if converted is None:
            continue
        if converted["role"] == "system":
            system.append(str(converted["content"]))
            continue
        if converted["role"] == "assistant":
            converted["weight"] = 0  # the student's own step: context, never a target
        messages.append(converted)
    target = dataset.openai_message(message_to_dict(action))
    if target is None or target["role"] != "assistant" or not (target.get("content") or target.get("tool_calls")):
        return None
    target["weight"] = 1
    dataset._relative_calls(target, roots)  # noqa: SLF001 - O33, as the product keeps a target's paths
    messages.append(target)
    tools = [dict(item) for item in request.get("tools") or ()] or None
    system, messages, tools, _ = shape.render(system, messages, tools, envelope, agent=True)
    if system:
        messages.insert(0, {"role": "system", "content": "\n\n".join(text for text in system if text)})
    found: dict[str, Any] = {"messages": messages}
    if tools:
        found["tools"] = tools
    return found


__all__ = ["order", "row"]
