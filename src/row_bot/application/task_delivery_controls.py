"""Revision-bound workflow delivery defaults for authenticated clients."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import re

from row_bot.application.client_platform import ClientPlatformError


def _snapshot() -> dict:
    from row_bot import tasks
    from row_bot.channels import registry

    selected = tasks.get_workflow_default_channels()
    channels = []
    for channel in registry.configured_channels()[:32]:
        identifier = getattr(channel, "name", "")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9:_-]{1,128}", identifier
        ):
            continue
        label = str(getattr(channel, "display_name", identifier))[:256]
        channels.append(
            {"id": identifier, "label": label, "selected": identifier in selected}
        )
    channels.sort(key=lambda item: (item["label"].casefold(), item["id"]))
    known = {item["id"] for item in channels}
    selected = [item for item in selected if item in known]
    revision = hashlib.sha256(
        json.dumps(
            {"channels": channels, "selected": selected},
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "revision": revision,
        "channels": channels,
        "web_app_always_on": True,
    }


def read_task_delivery_defaults(*, validate: Callable[[], None]) -> dict:
    validate()
    result = _snapshot()
    validate()
    return result


def update_task_delivery_defaults(
    channels: list[str],
    *,
    expected_revision: str,
    validate: Callable[[], None],
) -> dict:
    validate()
    current = _snapshot()
    requested = list(dict.fromkeys(channels))
    known = {item["id"] for item in current["channels"]}
    if (
        current["revision"] != expected_revision
        or len(requested) > 32
        or any(item not in known for item in requested)
    ):
        raise ClientPlatformError("task_delivery_revision_conflict")
    from row_bot import tasks

    validate()
    tasks.set_workflow_default_channels(requested)
    validate()
    result = _snapshot()
    if {item["id"] for item in result["channels"] if item["selected"]} != set(
        requested
    ):
        raise ClientPlatformError("task_delivery_unconfirmed")
    return result
