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

**The knowledge graph** is the pass's last stage (``graph`` holds its words, ``graph_store`` its SQL). With the
company's default chat model usable (the worker's own lookup, handed to the gateway as for embeddings), it claims
ready documents one at a time, oldest first, each at most once a pass, and reads their parts in order, one chat
call each (``GatewayService.complete``, as a call of the uploader's: metered and budgeted as theirs), at most the
first ``GRAPH_MAX_CHUNKS`` parts of a document. Each answer is read strictly (``graph.read_answer``: junk gives
nothing) and what it found is stored with the next part to read, in one version-checked transaction, so the stage
can stop between any two calls and the next pass carries on; a part whose answer can't be stored gives nothing, and
the next one is read. Then facts, one call each, as calls of their writers. Documents stop after
``GRAPH_CALLS_PER_PASS`` calls or ``GRAPH_DOCUMENT_SHARE`` of ``GRAPH_PASS_SECONDS``, facts after
``GRAPH_FACT_CALLS_PER_PASS`` calls or the rest of it; the stage also stops when the worker stops, or as soon as
something is queued (``wake``), so a long backlog never holds up a new upload's processing; when it stopped at a
limit, the thread starts the next pass at once. Each call waits at most ``GRAPH_CALL_TIMEOUT_SECONDS``. Without a
usable chat model, ready documents wait with ``CHAT_MODEL_NEEDED`` or the model's problem (one statement, no claim)
and are claimed once there is one. A failed call (a spent budget, the endpoint down or refusing) leaves the
document waiting with the reason, claimed again only after ``graph_store.RETRY_AFTER``, however often it happens;
when the model or its endpoint failed, the stage makes no call for ``GRAPH_BACKOFF_SECONDS``. Only a claim that
never finishes counts towards ``graph_store.MAX_CLAIMS``, after which the document's graph is failed until the
default chat model or its endpoint changes. A fact whose call fails, or that can't be read, waits the same way, and
its writer is skipped for the rest of the pass.

**Recall.** Text rank (Postgres full text, any of the query's words) and vector similarity (cosine, against the
query embedded as a call of the caller's) over chunks and facts, fused by reciprocal rank fusion. It runs in the
caller's own transaction, never an admin's: row-level security finds only what the caller may read, and the
shared rules (``row_bot.brain``: ``can_read_item``, ``recall_label``) check each hit again. Without an embeddings
model, or when the query can't be embedded, it is text only. A query is embedded with a short timeout
(``QUERY_TIMEOUT_SECONDS``), and after the model or its endpoint fails, recall is text only for
``QUERY_BACKOFF_SECONDS`` (a spent budget is the person's own, and backs nobody else off).

**The shared rules' terms.** Group names are Row-Bot's (any characters); the brain's ids aren't, so a group is
``g.<SHA-256 of its name>`` there. Everyone signed in is in ``EVERYONE`` (the company space's readers) and in
``p.<their id>``, so a space's owners (groups and principals) are its publishers. A connector's document is read
only by its mirrored readers (``brain_documents.readers``): its audience is their groups and ``e.<SHA-256 of an
email>``, which the caller holds for the email Row-Bot has for them; an empty list is ``ADMINS_ONLY``, which no
caller holds (recall never acts as an admin).

**Connectors** (``row_bot_org.connectors``) are the pass's first stage: each due connector's sync, within its
bounds (``connectors.sync``), adds and replaces documents that the same pass then processes.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from row_bot.brain import (
    Fact, Layer, Principal, Provenance, RecallHit, RecallQuery, SourceDoc, SourceKind, Space,
    SpaceAccess, Validity, can_read_item, can_write_space, recall_label, to_dict,
)
from row_bot_org.brain import graph, graph_store, parse, store
from row_bot_org.db.rls import principal_transaction
from row_bot_org.gateway import store as gateway_store
from row_bot_org.gateway.web import (
    ChatFailed, EmbeddingsFailed, NoEmbeddingsModel, chat_problem, embeddings_problem,
)
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
# The audience of a document only admins read: nobody's recall holds it.
ADMINS_ONLY = "admins-only"
# The knowledge graph: why documents wait or fail, and its bounds (the parts of a document it reads, and a pass's
# calls and time).
CHAT_MODEL_NEEDED = "Add a chat model in Models"
GRAPH_WORN_OUT = "This document's knowledge couldn't be read."
GRAPH_MAX_CHUNKS = 40
GRAPH_CALLS_PER_PASS = 12
GRAPH_FACT_CALLS_PER_PASS = 8
GRAPH_PASS_SECONDS = 120.0
# Documents stop at this share of a pass's time: facts have the rest.
GRAPH_DOCUMENT_SHARE = 0.75
# One graph call's longest wait (the endpoint's own timeout, when shorter, wins).
GRAPH_CALL_TIMEOUT_SECONDS = 90.0
# After the chat model or its endpoint failed, the graph calls again only after this long (per process).
GRAPH_BACKOFF_SECONDS = 300.0


class SpaceNotFound(Exception):
    """A space that doesn't exist or that the caller may not read: the two look the same."""


# -- the shared rules' terms ------------------------------------------------------------------------

def _group(name: str) -> str:
    return "g." + hashlib.sha256(name.encode("utf-8")).hexdigest()[:40]


def _person(principal_id: str) -> str:
    return "p." + principal_id


def _email(address: str) -> str:
    return "e." + hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()[:40]


def _entra(object_id: str) -> str:
    return "o." + hashlib.sha256(object_id.strip().lower().encode("utf-8")).hexdigest()[:40]


def brain_principal(principal: RequestPrincipal, email: str | None = None,
                    entra_oid: str | None = None) -> Principal:
    """``principal`` in the shared rules' terms (with ``email`` and ``entra_oid``, what Row-Bot has for them)."""
    terms = {EVERYONE, _person(principal.principal_id), *map(_group, principal.groups)}
    terms |= {_email(email)} if email else set()
    terms |= {_entra(entra_oid)} if entra_oid else set()
    return Principal(principal.principal_id, frozenset(terms))


def item_audience(readers: Sequence[str] | None) -> frozenset[str]:
    """A document's audience from its readers (``e:<email>``, ``o:<Entra object id>``, ``g:<group>``): empty (the space's readers) for
    None, ``ADMINS_ONLY`` for an empty list."""
    if readers is None:
        return frozenset()
    found = {_group(reader[2:]) if reader.startswith("g:") else _entra(reader[2:]) if reader.startswith("o:")
             else _email(reader[2:]) for reader in readers}
    return frozenset(found) or frozenset({ADMINS_ONLY})


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


def excerpt(text: str, *, near: str | None = None) -> str:
    """``text`` with its whitespace collapsed, at most ``EXCERPT_CHARACTERS`` ("…" where it is cut): its start, or
    around the first place it holds ``near`` (an entity's name)."""
    plain = " ".join(text.split())
    if len(plain) <= EXCERPT_CHARACTERS:
        return plain
    start = 0
    at = plain.lower().find(" ".join(near.split()).lower()) if near else -1
    if at > EXCERPT_CHARACTERS // 3:
        start = plain.rfind(" ", 0, at - EXCERPT_CHARACTERS // 3) + 1
    cut = plain[start:start + EXCERPT_CHARACTERS]
    if start + EXCERPT_CHARACTERS < len(plain):
        cut = cut.rsplit(" ", 1)[0] + "…"
    return ("…" if start else "") + cut


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


def _route_key(route: gateway_store.Route) -> str:
    """Which chat model and endpoint a document's graph failed with: another reads it again."""
    return "|".join((route.endpoint_id, route.model, route.upstream_model, route.base_url))


def _read(answer: str) -> graph.Found:
    """What a chat answer found; nothing for junk."""
    try:
        return graph.read_answer(answer)
    except graph.JunkAnswer as junk:
        logger.info("a chat answer gave nothing for the graph (%s)", junk)
        return graph.NOTHING


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
        # Whether the last pass's graph stage stopped at one of its limits (more is waiting), and until when
        # (time.monotonic) it makes no calls, after the chat model failed.
        self._graph_more = False
        self._graph_off_until = 0.0
        # The connectors' sync (row_bot_org.connectors.sync.ConnectorService), when the server has a key to open
        # their secrets with; and whether its last stage stopped at a limit.
        self.connectors: Any | None = None
        self._connectors_more = False

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
                self._graph_more = self._connectors_more = False
                logger.error("the company brain's worker pass failed (%s)", type(error).__name__)
            # A graph or connector stage stopped at its limits made that many calls: the next pass starts at once. (A
            # pass clears the wake at its start, so what is queued during one stops those stages and starts the next.)
            if not (self._graph_more or self._connectors_more):
                self._wake.wait(POLL_SECONDS)

    # -- processing ------------------------------------------------------------------------------

    def process_pending(self) -> int:
        """One pass: the connectors that are due, then every claimable document (each at most once), then facts to
        embed, then the knowledge graph's stage. How many documents it took to process. It stops early when the
        worker is stopped."""
        self._wake.clear()
        self._graph_more = self._connectors_more = False
        if self.connectors is not None and not self._stop.is_set():
            self._connectors_more = self.connectors.sync_due(lambda: self._stop.is_set() or self._wake.is_set())
        taken: list[str] = []
        while not self._stop.is_set() and (document_id := self._process_next(taken)) is not None:
            taken.append(document_id)
        if not self._stop.is_set():
            self._embed_facts()
        if not self._stop.is_set():
            self._graph_more = self._graph_stage()
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

    # -- the knowledge graph ---------------------------------------------------------------------

    def _chat(self, conn: Any) -> tuple[gateway_store.Route | None, str]:
        """The default chat model when it can be used, else None and why documents wait."""
        route = None if self.gateway is None else gateway_store.default_route(conn, "chat")
        if route is None:
            return None, CHAT_MODEL_NEEDED
        problem = chat_problem(route)
        return (route, "") if problem is None else (None, problem)

    def _graph_stage(self) -> bool:
        """Read documents, then facts, for the graph, within this pass's limits: documents stop at
        ``GRAPH_DOCUMENT_SHARE`` of the pass's time, so facts always have the rest. Whether it stopped at a limit
        (calls or time), so more may be waiting."""
        with store.worker_transaction(self.pool) as conn:
            route, waiting = self._chat(conn)
            if route is None:
                graph_store.wait_for_model(conn, waiting)
                return False
        if time.monotonic() < self._graph_off_until:
            return False  # The model or its endpoint failed a moment ago.
        started = time.monotonic()
        deadline = [started + GRAPH_PASS_SECONDS * GRAPH_DOCUMENT_SHARE]
        calls = [GRAPH_CALLS_PER_PASS]

        def go_on() -> bool:
            return (calls[0] > 0 and time.monotonic() < deadline[0] and not self._stop.is_set()
                    and not self._wake.is_set() and time.monotonic() >= self._graph_off_until)

        def limited() -> bool:
            return calls[0] <= 0 or time.monotonic() >= deadline[0]

        taken: list[str] = []
        while go_on():
            with store.worker_transaction(self.pool) as conn:
                claim = graph_store.claim_graph(conn, route=_route_key(route), skip=taken)
            if claim is None:
                break
            taken.append(claim.document_id)
            try:
                self._read_document(claim, route, calls, go_on)
            except Exception as error:  # noqa: BLE001 - its claim goes stale and is retried, MAX_CLAIMS times at most
                logger.error("a company document couldn't be read for the graph (%s)", type(error).__name__)
        stopped = limited()
        calls[0], deadline[0] = GRAPH_FACT_CALLS_PER_PASS, started + GRAPH_PASS_SECONDS
        if go_on():
            self._read_facts(route, calls, go_on)
            stopped = stopped or limited()
        return stopped and not self._stop.is_set() and time.monotonic() >= self._graph_off_until

    def _model_failed(self) -> None:
        """The chat model or its endpoint failed (not a person's budget): the graph waits ``GRAPH_BACKOFF_SECONDS``
        before it calls again, in this process."""
        self._graph_off_until = time.monotonic() + GRAPH_BACKOFF_SECONDS

    def _finish_graph(self, claim: graph_store.GraphClaim, status: str, reason: str | None, **options: Any) -> None:
        with store.worker_transaction(self.pool) as conn:
            graph_store.finish_graph(conn, claim, status, reason, **options)

    def _read_document(self, claim: graph_store.GraphClaim, route: gateway_store.Route, calls: list[int],
                       go_on: Callable[[], bool]) -> None:
        """Read the claimed document's parts from where its reading stopped, one call each, while ``go_on``. A call
        that fails leaves it waiting (``RETRY_AFTER``), never failed: only claims that never finish count towards
        ``MAX_CLAIMS``. A part whose answer can't be stored gives nothing, and the next part is read."""
        if claim.worn_out:
            logger.error("a company document's graph was failed: its reading stopped %s times", graph_store.MAX_CLAIMS)
            self._finish_graph(claim, "failed", GRAPH_WORN_OUT, failed_with=_route_key(route))
            return
        if claim.fresh:
            with store.worker_transaction(self.pool) as conn:
                if not graph_store.start_graph(conn, claim):
                    return
        uploader = RequestPrincipal(claim.uploaded_by, claim.uploader_groups, None)
        start = 0 if claim.fresh else claim.next
        while True:
            with store.worker_transaction(self.pool) as conn:
                part = graph_store.next_chunk(conn, claim.document_id, start, GRAPH_MAX_CHUNKS)
                count = graph_store.chunk_count(conn, claim.document_id) if part is None else 0
            if part is None:
                capped = None if count <= GRAPH_MAX_CHUNKS else (
                    f"Only the start of this document is in the knowledge graph (its first {GRAPH_MAX_CHUNKS:,}"
                    f" of {count:,} parts).")
                self._finish_graph(claim, "done", capped)
                return
            if not go_on():
                self._finish_graph(claim, "pending", None)  # Carried on next pass, from this part.
                return
            chunk_id, ordinal, text = part
            source = f'Part {ordinal + 1} of the document "{claim.title}"'
            try:
                answer = self.gateway.complete(uploader, graph.prompt(source, text), route=route,
                                               timeout=GRAPH_CALL_TIMEOUT_SECONDS)
            except ChatFailed as failed:
                if failed.code != "budget_exceeded":
                    self._model_failed()
                self._finish_graph(claim, "waiting", failed.message, retry=True)
                return
            calls[0] -= 1
            try:
                found = _read(answer)
                with store.worker_transaction(self.pool) as conn:
                    stored = graph_store.store_chunk_found(conn, claim, chunk_id, ordinal, found)
            except Exception as error:  # noqa: BLE001 - one part that can't be stored gives nothing; the rest go on
                logger.error("a part of a company document gave nothing for the graph (%s)", type(error).__name__)
                with store.worker_transaction(self.pool) as conn:
                    stored = graph_store.store_chunk_found(conn, claim, chunk_id, ordinal, graph.NOTHING)
            if not stored:
                return  # Replaced or deleted since the claim.
            start = ordinal + 1

    def _read_facts(self, route: gateway_store.Route, calls: list[int], go_on: Callable[[], bool]) -> None:
        """Facts in force not yet read, oldest first, each at most once a pass, as calls of their writers'. A
        writer whose call fails is left out for the rest of the pass, and the fact waits ``RETRY_AFTER``; when the
        model or its endpoint failed, the step ends. A fact whose answer can't be stored gives nothing (it is read);
        any other failure holds it back, like a failed call, so no fact can stop the ones after it."""
        tried: list[str] = []
        failed: set[str] = set()
        while go_on():
            with store.worker_transaction(self.pool) as conn:
                fact = graph_store.next_fact(conn, tried=tried, writers_failed=sorted(failed))
            if fact is None:
                return
            tried.append(fact.id)
            writer = RequestPrincipal(fact.written_by, fact.writer_groups, None)
            try:
                try:
                    answer = self.gateway.complete(writer, graph.prompt("A fact in the company knowledge base",
                                                                        fact.text),
                                                   route=route, timeout=GRAPH_CALL_TIMEOUT_SECONDS)
                except ChatFailed as refused:
                    if refused.code != "budget_exceeded":
                        self._model_failed()
                    raise
                calls[0] -= 1
                try:
                    found = _read(answer)
                    with store.worker_transaction(self.pool) as conn:
                        graph_store.store_fact_found(conn, fact, found)
                except Exception as error:  # noqa: BLE001 - an answer that can't be stored gives nothing
                    logger.error("a company fact gave nothing for the graph (%s)", type(error).__name__)
                    with store.worker_transaction(self.pool) as conn:
                        graph_store.store_fact_found(conn, fact, graph.NOTHING)
            except Exception as error:  # noqa: BLE001 - the fact waits, and its writer is left out of this pass
                if not isinstance(error, ChatFailed):
                    logger.error("a company fact couldn't be read for the graph (%s)", type(error).__name__)
                failed.add(fact.written_by)
                with store.worker_transaction(self.pool) as conn:
                    graph_store.hold_fact(conn, fact)

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
            checker = brain_principal(principal, store.own_email(conn, principal.principal_id),
                                      store.own_entra_oid(conn, principal.principal_id))
        hits = []
        for (kind, key), score in fused:
            found = chunks.get(key) if kind == "chunk" else facts.get(key)
            space = rows.get(found.space_id) if found is not None else None
            if space is None:
                continue  # Gone since it was ranked.
            access = space_access(space)
            if not can_read_item(checker, brain_space(space), item_audience(found.readers), access):
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
                        "chunk": found.ordinal, "excerpt": excerpt(found.text)}
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


__all__ = ["ADMINS_ONLY", "CHAT_MODEL_NEEDED", "EVERYONE", "GRAPH_BACKOFF_SECONDS", "GRAPH_CALLS_PER_PASS",
           "GRAPH_CALL_TIMEOUT_SECONDS", "GRAPH_DOCUMENT_SHARE", "GRAPH_FACT_CALLS_PER_PASS", "GRAPH_MAX_CHUNKS",
           "GRAPH_PASS_SECONDS", "GRAPH_WORN_OUT", "MODEL_NEEDED", "POLL_SECONDS", "QUERY_BACKOFF_SECONDS",
           "QUERY_TIMEOUT_SECONDS", "RRF_K", "UPLOADS_AT_ONCE", "WORN_OUT", "BrainService", "SpaceNotFound",
           "brain_principal", "brain_space", "excerpt", "fuse", "item_audience", "owns", "space_access"]
