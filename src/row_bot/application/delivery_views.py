"""The client's views of files sent to the person's devices or a chat app (F7).

Row-Bot has one owner: every signed-in device is theirs, so each sees every delivery and can
download what was sent to the devices while its copy lasts. A delivery that couldn't go to a chat
app can be sent to the devices instead by the person's own click, with no approval.
"""

from __future__ import annotations

from typing import Any

from row_bot import file_deliveries


def read_deliveries(conversation_id: str | None = None) -> dict[str, Any]:
    """Newest first."""

    items = file_deliveries.list_deliveries(conversation_id or None)[:200]
    return {"items": [file_deliveries.view(item) for item in items]}


def read_delivery(delivery_id: str) -> dict[str, Any] | None:
    delivery = file_deliveries.get(delivery_id)
    return None if delivery is None else file_deliveries.view(delivery)


def send_to_devices_instead(delivery_id: str) -> dict[str, Any]:
    """The devices delivery made from one that didn't go (or the one made already)."""

    return file_deliveries.view(file_deliveries.send_instead_to_devices(delivery_id))
