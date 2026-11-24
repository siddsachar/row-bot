"""The company's models as a provider, ``company`` (Row-Bot for Teams, C2).

- The catalog is the company server's ``GET /api/models``: the models the person may use, read with the
  device's bearer and kept in memory for ``FRESH_SECONDS``. A model list read after that (the model picker
  opening, Settings › Models) asks for it again in the background, once at a time, and the lists hear the
  answer through the ``models`` live topic. Nothing polls. Settings › Company's check and a finished join read
  it at once.
- Model refs are provider-qualified, ``model:company:<catalog id>``, so a company model never collides with a
  personal provider's model of the same name.
- Joined: the catalog's chat models are listed under the company's name, ready. Disconnected: the ones already
  read stay listed, unavailable, with the way back (``SIGN_IN_AGAIN``). Not joined: nothing, no network.
- Embedding models are not offered: Row-Bot's embeddings choose from a fixed list of providers
  (``embedding_providers``), which takes no provider from outside it.

The calls to the models are ``gateway``. Nothing here imports the HTTP client until a company is joined.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from row_bot.company import policy, store
from row_bot.providers.models import ModelInfo, TransportMode

logger = logging.getLogger(__name__)

PROVIDER_ID = "company"
# How long a catalog read is used before a model list read asks for it again.
FRESH_SECONDS = 120.0
MAX_MODELS = 512
SIGN_IN_AGAIN = "Sign in again in Settings › Company."
WIRES = {"openai": TransportMode.OPENAI_CHAT, "anthropic": TransportMode.ANTHROPIC_MESSAGES}

# Seconds on a clock that only moves forward; tests move it.
monotonic = time.monotonic


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    name: str
    # The gateway wire it is called through: "openai" or "anthropic".
    api: str
    # 0 when the company didn't say.
    context_window: int
    # The company's suggestion for a person who hasn't chosen a model.
    default: bool

    @property
    def ref(self) -> str:
        return f"model:{PROVIDER_ID}:{self.model_id}"


_lock = threading.Lock()
_models: tuple[CatalogModel, ...] = ()
# Whose catalog _models is, and when it was last asked for (None: never, in this process).
_origin: str | None = None
_read_at: float | None = None
_reading: threading.Thread | None = None


def _plain(value: Any, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()
            and value == value.strip())


def _parse(data: Any) -> tuple[CatalogModel, ...]:
    items = data.get("models") if isinstance(data, dict) else None
    found: list[CatalogModel] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict) or item.get("kind") != "chat" or item.get("api") not in WIRES:
            continue
        model_id, name, context = item.get("id"), item.get("display_name"), item.get("context_window")
        if not _plain(model_id, 256) or model_id in seen:
            continue
        seen.add(model_id)
        found.append(CatalogModel(model_id, name if _plain(name, 256) else model_id, item["api"],
                                  context if type(context) is int and 0 < context <= 2**31 else 0,
                                  item.get("default") is True))
        if len(found) == MAX_MODELS:
            break
    return tuple(found)


def _publish() -> None:
    from row_bot.application.live_updates import publish

    publish("models")


def _keep(origin: str, models: tuple[CatalogModel, ...]) -> None:
    global _models, _origin, _read_at
    with _lock:
        changed = (_origin, _models) != (origin, models)
        _models, _origin, _read_at = models, origin, monotonic()
    if changed:
        _publish()


def refresh() -> tuple[CatalogModel, ...]:
    """Ask the company for its catalog now, and keep it.

    Raises the client's Disconnected or Unreachable (Busy for a 429), or store.StorageUnavailable."""
    record = store.read_record()
    if record is None or record.disconnected:
        return ()
    from row_bot.company import client

    response = client.call(record.origin, "GET", "/api/models")
    if response.status_code == 404:
        models: tuple[CatalogModel, ...] = ()  # A server without a model gateway offers none.
    elif response.status_code != 200:
        raise client.Unreachable(str(response.status_code))
    else:
        try:
            models = _parse(response.json())
        except ValueError:
            raise client.Unreachable("not json") from None
    _keep(record.origin, models)
    return models


def _read_in_background(origin: str) -> None:
    global _reading, _origin, _read_at, _models
    try:
        refresh()
    except Exception as error:  # noqa: BLE001 - the lists keep what they had; a later read asks again
        logger.info("The company's models couldn't be read (%s)", type(error).__name__)
        with _lock:
            if _origin != origin:
                _models, _origin = (), origin
            _read_at = monotonic()  # Not asked again on every list read while it fails.
    finally:
        with _lock:
            _reading = None


def _read_soon(record: store.Record) -> None:
    """A model list was read: when the catalog is older than FRESH_SECONDS, read it again in the background."""
    global _reading
    with _lock:
        fresh = _origin == record.origin and _read_at is not None and monotonic() - _read_at < FRESH_SECONDS
        if fresh or _reading is not None:
            return
        _reading = thread = threading.Thread(target=_read_in_background, args=(record.origin,), daemon=True,
                                             name="row-bot-company-models")
    thread.start()


def settle(timeout: float = 5.0) -> None:
    """Wait for a background read to end (a test's way to see its outcome)."""
    with _lock:
        thread = _reading
    if thread is not None:
        thread.join(timeout)


def after_join() -> None:
    """A join or Sign in again finished: read the catalog (best effort), and let the lists read again."""
    from row_bot.company import client

    try:
        refresh()
    except (client.Unreachable, client.Disconnected, store.StorageUnavailable) as error:
        logger.info("The company's models couldn't be read after joining (%s)", type(error).__name__)
    _publish()


def forget() -> None:
    """Leave: the company's catalog goes with it."""
    global _models, _origin, _read_at
    with _lock:
        _models, _origin, _read_at = (), None, None
    _publish()


def models(record: store.Record | None) -> tuple[CatalogModel, ...]:
    """The catalog last read from ``record``'s company (nothing for another company, or none)."""
    with _lock:
        return _models if record is not None and _origin == record.origin else ()


def find(model_id: str) -> CatalogModel | None:
    return next((model for model in models(store.read_record()) if model.model_id == model_id), None)


def model_count(record: store.Record | None) -> int | None:
    """How many models the company offers this person; None while its catalog hasn't been read."""
    with _lock:
        known = record is not None and _origin == record.origin and _read_at is not None
        return len(_models) if known else None


def suggestion(record: store.Record | None) -> CatalogModel | None:
    """The model the company suggests to a person who hasn't chosen one."""
    return next((model for model in models(record) if model.default), None)


def display_name() -> str:
    """The company's name in model lists (its server's host)."""
    record = store.read_record()
    return record.company if record else "Your company"


def context_window(model_id: str) -> int:
    """The model's context window as the company gave it; 0 when unknown."""
    found = find(model_id)
    return found.context_window if found else 0


def provider_status() -> dict[str, Any]:
    """Readiness's view: joined (or disconnected, whose call then says how to sign in again)."""
    record = store.read_record()
    return {"provider_id": PROVIDER_ID, "configured": record is not None,
            "runtime_enabled": record is not None and not record.disconnected, "source": "company"}


def _row(model: CatalogModel, record: store.Record) -> Any:
    from row_bot.providers.model_catalog import _catalog_row, categories_for_snapshot

    transport = WIRES[model.api]
    snapshot = ModelInfo(
        provider_id=PROVIDER_ID, model_id=model.model_id, display_name=model.name,
        context_window=model.context_window, transport=transport,
        capabilities=frozenset({"text", "chat", "tool_calling", "streaming"}), tool_calling=True, streaming=True,
        endpoint_compatibility=frozenset({transport}), source_confidence="company_catalog", risk_label="company",
        source="company_catalog",
    ).capability_snapshot()
    # The catalog's own readiness rules (context, transport, tools), as for any provider's model.
    row = _catalog_row(
        provider_id=PROVIDER_ID, model_id=model.model_id, display_name=model.name,
        categories=categories_for_snapshot(snapshot), capabilities_snapshot=snapshot,
        provider_status={PROVIDER_ID: {"configured": True}}, context_window=model.context_window,
        installed=True, downloadable=False, source="company_catalog", risk_label="company",
    )
    row = replace(row, provider_display_name=record.company)
    if record.disconnected:
        row = replace(row, runtime_ready=False, status_reason=SIGN_IN_AGAIN)
    return row


def add_rows(rows: dict[str, Any]) -> None:
    """Add the company's models to the app's model rows (``build_saved_model_catalog_rows``), keyed by ref.

    Rows carry their readiness (``project_saved_catalog_readiness`` keeps it). With no company joined this
    reads one file's absence and nothing else."""
    if policy.hides_personal_providers():
        rows.clear()
    record = store.read_record()
    if record is None:
        return
    if not record.disconnected:
        _read_soon(record)
    for model in models(record):
        rows[model.ref] = _row(model, record)
