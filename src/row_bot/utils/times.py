"""Times as the client reads them: UTC with an offset.

Most saved times are this computer's wall clock without an offset (``datetime.now().isoformat()``).
A browser in another zone (a phone over remote access, a hosted Row-Bot) reads an offset-less time
as its own, so an API sends every time through ``utc_text`` (M11).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utc_text(value: str | float | int | datetime | None) -> str:
    """*value* as ISO 8601 in UTC with its offset ("2026-11-16T17:01:58+00:00"), or "" when it isn't a time.

    A string or datetime without an offset is this computer's wall clock; a number is epoch seconds.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            moment = datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
    elif isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return ""
    if moment.tzinfo is None:
        from row_bot.schedule_zones import local_zone_name

        moment = moment.replace(tzinfo=ZoneInfo(local_zone_name()))
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
