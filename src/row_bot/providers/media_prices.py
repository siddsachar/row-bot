"""What one picture or video on an API key costs, roughly: the price its approval card shows (D4).

Prices come from the provider's own model list when it carries them (xAI lists a price for each
quality and resolution of a picture model), else from ``media_prices.json``, a small table read
from each provider's public pricing page, with the date each row was checked. Nothing here calls
the network. A model with neither is "price unknown": the card still asks, it just can't say how much.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TABLE_PATH = Path(__file__).with_name("media_prices.json")
# The keys a row may be priced by; a call names the ones it knows ("" or "auto": not chosen).
CHOICE_KEYS = ("quality", "size", "resolution")
UNITS = {"picture", "second"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@lru_cache(maxsize=1)
def table_rows() -> tuple[dict[str, Any], ...]:
    """The shipped table's rows, as written."""
    try:
        rows = json.loads(TABLE_PATH.read_text(encoding="utf-8")).get("rows")
    except (OSError, ValueError, AttributeError):
        logger.warning("The media price table could not be read; prices show as unknown")
        return ()
    return tuple(row for row in rows if isinstance(row, dict)) if isinstance(rows, list) else ()


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return "" if text == "auto" else text


def _checked(value: str) -> str:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return ""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _catalog_rows(provider_id: str, model: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The prices the provider's own model list gives this model, and its defaults for unset choices."""
    from row_bot.providers.capability_resolution import resolve_capability_snapshot

    try:
        parameters = resolve_capability_snapshot(provider_id, model, include_static_fallback=False).get(
            "generation_parameters")
    except Exception:
        logger.debug("No catalog entry to price %s/%s from", provider_id, model, exc_info=True)
        return [], {}
    if not isinstance(parameters, Mapping) or not isinstance(parameters.get("prices"), list):
        return [], {}
    defaults = parameters.get("defaults") if isinstance(parameters.get("defaults"), Mapping) else {}
    rows = [{**{key: row[key] for key in CHOICE_KEYS if key in row}, "unit": "picture", "usd": row["usd"]}
            for row in parameters["prices"] if isinstance(row, Mapping) and isinstance(row.get("usd"), (int, float))]
    return rows, {str(key): _norm(value) for key, value in defaults.items()}


def _matching(rows: list[dict[str, Any]], unit: str, choices: Mapping[str, str]) -> list[float]:
    """The prices of the rows this call could be billed at: every key a row is priced by matches, or
    the call left it unchosen (then each row it could be is a possible price)."""
    return [float(row["usd"]) for row in rows if row.get("unit") == unit and all(
        _norm(choices.get(key)) in ("", _norm(row[key])) for key in CHOICE_KEYS if key in row)]


def price_for(provider_id: str, provider_label: str, model: str, *, unit: str = "picture", count: float = 1,
              choices: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The price an approval card shows for *count* pictures or seconds of video on *model*.

    ``{"status": "known", "low_usd", "high_usd", "basis"}`` (a range when the call leaves a choice the
    price depends on to the provider, like OpenAI's "auto" quality), or ``{"status": "unknown"}``.
    """
    choices = dict(choices or {})
    catalog, defaults = _catalog_rows(provider_id, model)
    if catalog:
        filled = {key: _norm(choices.get(key)) or defaults.get(key, "") for key in CHOICE_KEYS}
        prices, basis = _matching(catalog, unit, filled), f"from {provider_label}'s model list"
    else:
        rows = [row for row in table_rows() if row.get("provider") == provider_id and row.get("model") == model]
        prices = _matching(rows, unit, choices)
        dates = sorted({str(row.get("checked") or "") for row in rows})
        checked = _checked(dates[0]) if dates else ""
        basis = f"{provider_label}'s list price" + (f", checked {checked}" if checked else "")
    if not prices or count <= 0:
        return {"status": "unknown"}
    return {"status": "known", "low_usd": round(min(prices) * count, 4), "high_usd": round(max(prices) * count, 4),
            "basis": basis}
