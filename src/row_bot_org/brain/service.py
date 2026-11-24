"""The company brain's work: the worker that turns uploads into searchable chunks, and recall.

**The worker.** One background thread per server process (``start``/``stop``, from the app's lifespan). Each pass
(``process_pending``) claims queued documents one at a time with ``FOR UPDATE SKIP LOCKED`` (so several processes
never take the same one), parses and chunks the claimed version, stores its chunks (text search finds them from
then on), embeds them through the gateway in process, as calls of the uploader's (metered to their person and
groups, refused past their hard budgets), and marks the document ``ready``. A parse or embedding failure marks it
``failed`` with a plain reason. With no default embeddings model, or one that can't be used (``embeddings_problem``),
the chunks are stored and the document stays ``queued`` with ``MODEL_NEEDED`` or that problem, and is taken again
only once a usable model exists. Whether there is one is decided once per claim, from the worker's own lookup, and
that same model is handed to the gateway, so the two never disagree. A pass takes each document at most once and
stops when the worker stops. A document claimed more than ``store.MAX_CLAIMS`` times without a finish (its file
keeps stopping the worker) is failed with ``WORN_OUT`` and never parsed again. A replace or delete since the claim
leaves the worker's results unwritten (the version check). The pass then embeds facts that have no embedding, as
calls of their writers', each fact at most once a pass and skipping writers whose calls failed in it. An upload
wakes the thread; otherwise it looks every ``POLL_SECONDS``.

**Recall.** Text rank (Postgres full text, any of the query's words) and vector similarity (cosine, against the
query embedded as a call of the caller's) over chunks and facts, fused by reciprocal rank fusion. It runs in the
caller's own transaction, never an admin's: row-level security finds only what the caller may read, and the
shared rules (``row_bot.brain``: ``can_read_item``, ``recall_label``) check each hit again. Without an embeddings
model, or when the query can't be embedded, it is text only. A query is embedded with a short timeout
(``QUERY_TIMEOUT_SECONDS``), and after the model or its endpoint fails, recall is text only for
``QUERY_BACKOFF_SECONDS`` (a spent budget is the person's own, and backs nobody else off).

**The shared rules' terms.** Group names are Row-Bot's (any characters); the brain's ids aren't, so a group is
``g.<SHA-256 of its name>`` there. Everyone signed in is in ``EVERYONE`` (the company space's readers) and in
``p.<their id>``, so a space's owners (groups and principals) are its publishers.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from row_bot.brain import (
    Fact, Layer, Principal, Provenance, RecallHit, RecallQuery, SourceDoc, SourceKind, Space,
    SpaceAccess, Validity, can_read_item, can_write_space, recall_label, to_dict,
)
from row_bot_org.brain import parse, store
from row_bot_org.db.rls import principal_transaction
from row_bot_org.gateway import store as gateway_store
from row_bot_org.gateway.web import EmbeddingsFailed, NoEmbeddingsModel, embeddings_problem
from row_bot_org.identity.gate import RequestPrincipal
from row_bot_org.settings import OrgSettings

logger = logging.getLogger(__name__)
MODEL_NEEDED = "Add an embeddings model in Models"
# A document that failed for no reason of its own, or that keeps stopping the worker.
WORN_OUT = "This file couldn't be processed."
POLL_SECONDS = 30.0
# Texts in one embeddings call (and facts read at a time).
EMBED_BATCH = 32
# Recall's query embedding: its timeout, and how long recall is text only after the model failed.
QUERY_TIMEOUT_SECONDS = 5.0
QUERY_BACKOFF_SECONDS = 60.0
# Uploads read and stored at once, per process (each holds its file in memory).
UPLOADS_AT_ONCE = 4
# Reciprocal rank fusion's constant, and how many candidates each ranking brings per hit asked for.
RRF_K = 60
CANDIDATES_PER_HIT = 4
EXCERPT_CHARACTERS = 280
EVERYONE = "everyone-signed-in"


class SpaceNotFound(Exception):
    """A space that doesn't exist or that the caller may not read: the two look the same."""


# -- the shared rules' terms ------------------------------------------------------------------------

def _group(name: str) -> str:
    return "g." + hashlib.sha256(name.encode("utf-8")).hexdigest()[:40]


def _person(principal_id: str) -> str:
    return "p." + principal_id


def brain_principal(principal: RequestPrincipal) -> Principal:
    """``principal`` in the shared rules' terms."""
    return Principal(principal.principal_id,
                     frozenset({EVERYONE, _person(principal.principal_id), *map(_group, principal.groups)}))


def brain_space(space: store.SpaceRow) -> Space:
    return Space(Layer.COMPANY if space.kind == store.COMPANY else Layer.TEAM, space.id)


def space_access(space: store.SpaceRow) -> SpaceAccess:
    """A space's access list in the shared rules' terms: readers, and its owners as publishers."""
    readers = {EVERYONE} if space.kind == store.COMPANY else set(map(_group, space.reader_groups))
    publishers = {*map(_group, space.owner_groups), *map(_person, space.owner_principals)}
    return SpaceAccess(brain_space(space), space.name, frozenset(readers), frozenset(publishers))


def owns(principal: RequestPrincipal, space: store.SpaceRow) -> bool:
    """Whether ``principal`` owns ``space`` (and so manages its documents and facts)."""
    return can_write_space(brain_principal(principal), brain_space(space), space_access(space))


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _excerpt(text: str) -> str:
    plain = " ".join(text.split())
    if len(plain) <= EXCERPT_CHARACTERS:
        return plain
    cut = plain[:EXCERPT_CHARACTERS].rsplit(" ", 1)[0]
    return cut + "…"


def fuse(rankings: Sequence[Sequence[tuple[str, str]]], limit: int) -> list[tuple[tuple[str, str], float]]:
    """Reciprocal rank fusion of ``rankings`` (each best first), scaled to [0, 1]: 1 for a candidate first in
    every ranking. The ``limit`` best, ties broken by kind and id."""
    scores: dict[tuple[str, str], float] = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] += 1 / (RRF_K + rank)
    best = len(rankings) / (RRF_K + 1)
    ordered = sorted(scores.items(), key=lambda entry: (-entry[1], entry[0]))
    return [(key, min(1.0, score / best)) for key, score in ordered[:limit]]


class BrainService:
    """The company brain for one server: its pool, the gateway it embeds through (None without one) and the
    worker thread."""

    def __init__(self, settings: OrgSettings, pool: Any, gateway: Any | None) -> None:
        self.pool = pool
        self.gateway = gateway
        self.max_upload_bytes = settings.brain_max_upload_mb * 1024 * 1024
        self.uploads = threading.BoundedSemaphore(UPLOADS_AT_ONCE)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Until when (time.monotonic) recall leaves the vector half out, after the model failed.
        self._vectors_off_until = 0.0

    # -- the worker thread -----------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="row-bot-org-brain", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def wake(self) -> None:
        """Something was queued: the worker looks now rather than at its next poll."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_pending()
            except Exception as error:  # noqa: BLE001 - one pass failing (the database away) never ends the worker
                logger.error("the company brain's worker pass failed (%s)", type(error).__name__)
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    # -- processing ------------------------------------------------------------------------------

    def process_pending(self) -> int:
        """One pass: every claimable document (each at most once), then facts to embed. How many documents it
        took. It stops early when the worker is stopped."""
        taken: list[str] = []
        while not self._stop.is_set() and (document_id := self._process_next(taken)) is not None:
            taken.append(document_id)
        if not self._stop.is_set():
            self._embed_facts()
        return len(taken)

    def _embeddings(self, conn: Any) -> tuple[gateway_store.Route | None, str]:
        """The default embeddings model when it can be used, else None and why documents wait."""
        route = None if self.gateway is None else gateway_store.default_route(conn, "embeddings")
        if route is None:
            return None, MODEL_NEEDED
        problem = embeddings_problem(route)
        return (route, "") if problem is None else (None, problem)

    def _process_next(self, taken: Sequence[str]) -> str | None:
        with store.worker_transaction(self.pool) as conn:
            route, waiting = self._embeddings(conn)
            claim = store.claim_document(conn, embeddings=route is not None, skip=taken)
        if claim is None:
            return None
        if claim.worn_out:
            logger.error("a company document was failed: its processing stopped %s times", store.MAX_CLAIMS)
            self._finish(claim, "failed", WORN_OUT)
            return claim.document_id
        try:
            self._process(claim, route, waiting)
        except Exception as error:  # noqa: BLE001 - the document is marked failed, the pass goes on
            logger.error("a company document couldn't be processed (%s)", type(error).__name__)
            self._finish(claim, "failed", WORN_OUT)
        return claim.document_id

    def _finish(self, claim: store.Claim, status: str, reason: str | None) -> None:
        with store.worker_transaction(self.pool) as conn:
            store.finish_document(conn, claim, status, reason)

    def _process(self, claim: store.Claim, route: gateway_store.Route | None, waiting: str) -> None:
        if not claim.chunked:
            try:
                kind = parse.BY_MEDIA_TYPE[claim.media_type]
                chunks = parse.chunk_text(parse.extract_text(kind, claim.content or b""))
            except parse.ParseFailed as failed:
                self._finish(claim, "failed", failed.reason)
                return
            with store.worker_transaction(self.pool) as conn:
                if not store.store_chunks(conn, claim, chunks):
                    return
        if route is None:
            # Taken again only once a usable model exists (claim_document), so this never loops.
            self._finish(claim, "queued", waiting)
            return
        uploader = RequestPrincipal(claim.uploaded_by, claim.uploader_groups, None)
        with store.worker_transaction(self.pool) as conn:
            pending = store.chunks_to_embed(conn, claim.document_id, route.model)
        for start in range(0, len(pending), EMBED_BATCH):
            if self._stop.is_set():
                return  # Still processing: taken again once its claim is stale.
            batch = pending[start:start + EMBED_BATCH]
            try:
                used, vectors = self.gateway.embed(uploader, [text for _, text in batch], route=route)
            except EmbeddingsFailed as failed:
                self._finish(claim, "failed", failed.message)
                return
            with store.worker_transaction(self.pool) as conn:
                if not store.store_chunk_embeddings(conn, claim, used,
                                                    [(chunk_id, vector) for (chunk_id, _), vector in zip(batch, vectors)]):
                    return
        self._finish(claim, "ready", None)

    def _embed_facts(self) -> None:
        """Embed the facts that have no embedding, as calls of their writers'. Each fact is tried at most once a
        pass; a writer whose call fails (their budget spent, the endpoint down) is left out for the rest of it, so
        nobody else's facts wait behind theirs."""
        tried: list[str] = []
        failed: set[str] = set()
        while not self._stop.is_set():
            with store.worker_transaction(self.pool) as conn:
                route, _ = self._embeddings(conn)
                if route is None:
                    return
                facts = store.facts_to_embed(conn, EMBED_BATCH, tried=tried, writers_failed=sorted(failed))
            if not facts:
                return
            tried.extend(fact.id for fact in facts)
            by_writer = sorted(facts, key=lambda fact: (fact.written_by, fact.writer_groups))
            for (writer, groups), written in itertools.groupby(by_writer, key=lambda fact: (fact.written_by,
                                                                                            fact.writer_groups)):
                batch = list(written)
                try:
                    used, vectors = self.gateway.embed(RequestPrincipal(writer, groups, None),
                                                       [fact.text for fact in batch], route=route)
                except EmbeddingsFailed:
                    failed.add(writer)
                    continue
                with store.worker_transaction(self.pool) as conn:
                    store.store_fact_embeddings(conn, used, list(zip(batch, vectors)))

    # -- recall ----------------------------------------------------------------------------------

    def recall(self, principal: RequestPrincipal, text: str, limit: int,
               space_ids: Sequence[str] | None) -> dict[str, Any]:
        """``{"search": "hybrid" | "text", "hits": [{"hit": RecallHit, "citation": {...}}]}`` for ``text``, best
        first, over ``space_ids`` (each readable, or ``SpaceNotFound``) or every space the caller reads.
        ``row_bot.brain.InvalidRecord`` for a query the contract refuses."""
        asker = brain_principal(principal)
        query = RecallQuery(text=text, principal=asker, limit=limit)
        if space_ids is not None:
            with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
                named = store.get_spaces(conn, space_ids)
            if len(named) != len(set(space_ids)):
                raise SpaceNotFound
            query = RecallQuery(text=text, principal=asker, limit=limit,
                                spaces=frozenset(brain_space(space) for space in named.values()))
        vector = self._embed_query(principal, query.text)
        candidates = max(CANDIDATES_PER_HIT * query.limit, 20)
        spaces = None if query.spaces is None else sorted(space.space_id for space in query.spaces)
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            rankings = [store.text_candidates(conn, query.text, spaces, candidates)]
            if vector is not None:
                rankings.append(store.vector_candidates(conn, vector[1], vector[0], spaces, candidates))
            fused = fuse(rankings, query.limit)
            chunks = store.chunk_hits(conn, [key for (kind, key), _ in fused if kind == "chunk"])
            facts = store.fact_hits(conn, [key for (kind, key), _ in fused if kind == "fact"])
            rows = store.get_spaces(conn, sorted({hit.space_id for hit in (*chunks.values(), *facts.values())}))
        hits = []
        for (kind, key), score in fused:
            found = chunks.get(key) if kind == "chunk" else facts.get(key)
            space = rows.get(found.space_id) if found is not None else None
            if space is None:
                continue  # Gone since it was ranked.
            access = space_access(space)
            if not can_read_item(asker, brain_space(space), frozenset(), access):
                logger.error("recall left out a hit the access rules refuse")
                continue
            hits.append(self._hit(found, space, access, score))
        return {"search": "hybrid" if vector is not None else "text", "hits": hits}

    def _embed_query(self, principal: RequestPrincipal, text: str) -> tuple[str, list[float]] | None:
        if self.gateway is None or time.monotonic() < self._vectors_off_until:
            return None
        try:
            model, (vector,) = self.gateway.embed(principal, [text], timeout=QUERY_TIMEOUT_SECONDS)
        except NoEmbeddingsModel:
            return None
        except EmbeddingsFailed as failed:
            if failed.code != "budget_exceeded":
                self._vectors_off_until = time.monotonic() + QUERY_BACKOFF_SECONDS
            logger.info("a recall searched text only: its query couldn't be embedded (%s)", failed.code)
            return None
        return model, vector

    @staticmethod
    def _hit(found: store.ChunkHit | store.FactHit, space: store.SpaceRow, access: SpaceAccess,
             score: float) -> dict[str, Any]:
        where = brain_space(space)
        about = {"id": space.id, "name": space.name}
        if isinstance(found, store.ChunkHit):
            uploaded = _utc(found.uploaded_at)
            item: Any = SourceDoc(
                space=where, id=f"{found.document_id}.{found.ordinal}",
                provenance=Provenance(SourceKind.DOCUMENT, f"brain/documents/{found.document_id}", uploaded,
                                      found.uploaded_by),
                validity=Validity(valid_from=uploaded, recorded_at=_utc(found.processed_at or found.uploaded_at)),
                uri=f"row-bot-org:brain/documents/{found.document_id}#chunk-{found.ordinal}", title=found.title,
                content_hash="sha256:" + found.sha256, fetched_at=uploaded)
            citation = {"type": "document", "space": about,
                        "document": {"id": found.document_id, "title": found.title, "file_name": found.file_name},
                        "chunk": found.ordinal, "excerpt": _excerpt(found.text)}
            owner_principal = None
        else:
            created = _utc(found.created_at)
            item = Fact(
                space=where, id=found.id,
                provenance=Provenance(SourceKind.STATEMENT, f"brain/facts/{found.id}", created, found.written_by),
                validity=Validity(valid_from=created, recorded_at=_utc(found.updated_at)),
                subject=" ".join(found.text.split())[:200], entity_type="statement", text=found.text,
                owner_principal=found.written_by)
            source = None if found.source_document_id is None else {
                "id": found.source_document_id, "title": found.source_title, "file_name": found.source_file_name}
            citation = {"type": "fact", "space": about, "fact": {"id": found.id, "text": found.text},
                        "document": source}
            owner_principal = found.written_by
        hit = RecallHit(item=item, score=score, layer=where.layer, owner=where.owner,
                        label=recall_label(where, access), owner_principal=owner_principal)
        return {"hit": to_dict(hit), "citation": citation}


__all__ = ["EVERYONE", "MODEL_NEEDED", "POLL_SECONDS", "QUERY_BACKOFF_SECONDS", "QUERY_TIMEOUT_SECONDS", "RRF_K",
           "UPLOADS_AT_ONCE", "WORN_OUT", "BrainService", "SpaceNotFound",
           "brain_principal", "brain_space", "fuse", "owns", "space_access"]
