"""A connector's sync: a stage of the company brain's worker (``BrainService.process_pending``).

**Who it runs as.** The brain's worker (no principal) takes a connector that is due (``store.claim_due``). The
sync then works in transactions of the connector's ``run_as`` admin (the last admin to save or resume it), acting as
an admin (``roles.enter_admin``): its documents are uploaded in their name, and the model calls that process them
are metered to them. When that person is no longer an active admin, the connector stops with a reason until another
admin saves or resumes it. Network calls are made between transactions, never inside one.

**What a turn does**, one step at a time, until it has caught up or used its share (``CALLS_PER_TURN`` calls, the
pass's ``PASS_SECONDS``), and as soon as the worker is stopped or something is queued:

1. The first time (and when Graph says its feed expired), it notes where the change feed stands now, then lists
   everything under the root folder, folder by folder, page by page (``store_children``). Once the listing is done,
   what it didn't see again is gone (``end_listing``).
2. Files whose content or permissions are to read come first: their permissions are read and mirrored
   (``mirror.mirror``), then a new or changed file of a type the brain reads, up to its upload limit, is downloaded
   and its document created or replaced, with its readers, in one transaction. A file of another type, too large,
   or that can't be fetched is skipped (with the reason) until it changes upstream, and its document goes.
3. Then folders still to list (one moved into scope), then the change feed, page by page from its cursor:
   removed files go, files moved out of scope go, a changed folder has every permission under it read again.

**Failures.** A 429 waits its Retry-After (at least ``MIN_WAIT_SECONDS``); a 5xx, a timeout or an answer that can't
be read waits longer each time in a row (``failure_wait``). A refusal (the sign-in, or access to the drive or
folder) is audited (``connector.changed``, ``auth_failed``) and waits too; after ``REFUSALS_TO_STOP`` in a row the
connector stops (``error``) with a plain reason. Every wait is stored on the connector, so nothing is called again
before it ends: no loop. A turn that stopped at a limit is due again at once, and the worker starts its next pass
straight away; one that caught up syncs again after ``SYNC_EVERY_SECONDS``. Results go to the log (counts and the
connector's id, never a file name, token, secret or URL).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from row_bot.audit import Action, Actor, ActorKind, AuditLayer, Outcome, Target, TargetKind
from row_bot_org import audit
from row_bot_org.brain import parse
from row_bot_org.brain import store as brain_store
from row_bot_org.connectors import mirror, secrets, store, upstream
from row_bot_org.db.rls import principal_transaction
from row_bot_org.identity import roles
from row_bot_org.identity.gate import RequestPrincipal
from row_bot_org.settings import OrgSettings

logger = logging.getLogger(__name__)

SYNC_EVERY_SECONDS = 15 * 60.0
PASS_SECONDS = 60.0
CALLS_PER_TURN = 100
MIN_WAIT_SECONDS = 30.0
MAX_WAIT_SECONDS = 3600.0
REFUSALS_TO_STOP = 3
NOT_AN_ADMIN = ("The admin this connector syncs as isn't an admin any more. An admin saves or resumes it to sync it "
                "as themselves.")
NO_SECRET = "This connector has no secret saved. Save its credentials again."
SECRET_UNAVAILABLE = ("This connector's secret can't be opened here (the server key changed?). Save its credentials "
                      "again.")
UNSUPPORTED = "The company brain doesn't read this type of file."
TOO_LARGE = "This file is larger than the company brain takes."
BAD_NAME = "This file's name can't be used."
NOT_FETCHED = "This file couldn't be fetched from its source."
# Who a refusal's audit event names: the server's own sync.
SERVER = Actor(ActorKind.SERVICE, "company-server")


def failure_wait(failures: int) -> float:
    """How long to wait after ``failures`` failures in a row (this one included): 1, 2, 4... minutes, at most an
    hour."""
    return min(MAX_WAIT_SECONDS, 60.0 * 2 ** max(0, min(failures - 1, 10)))


def refusal_wait(refusals: int) -> float:
    return min(MAX_WAIT_SECONDS, 300.0 * refusals)


def _file_name(name: str) -> str | None:
    """A file name the brain keeps (as an upload's): no path, no control character, 1-255 characters."""
    plain = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not 1 <= len(plain) <= 255 or any(ord(char) < 32 or ord(char) == 127 for char in plain):
        return None
    return plain


class NotAdmin(Exception):
    """The connector's admin can't act as one any more."""


@dataclass
class Tally:
    """What one turn did, for the log."""

    added: int = 0
    replaced: int = 0
    remirrored: int = 0
    removed: int = 0
    skipped: int = 0


@dataclass
class _Turn:
    service: ConnectorService
    admin: RequestPrincipal
    row: store.ConnectorRow
    source: Any
    stopping: Callable[[], bool]
    deadline: float
    tally: Tally = field(default_factory=Tally)
    calls_left: int = CALLS_PER_TURN

    @contextmanager
    def admin_transaction(self) -> Iterator[Any]:
        with self.service.as_admin(self.admin) as conn:
            yield conn

    def go_on(self) -> bool:
        return (self.calls_left > 0 and time.monotonic() < self.deadline and not self.stopping())

    def reload(self) -> None:
        with self.admin_transaction() as conn:
            row = store.get_connector(conn, self.row.id)
        if row is None:
            raise NotAdmin  # Deleted under us: nothing left to do.
        self.row = row

    def call(self, cost: int = 1) -> None:
        self.calls_left -= cost

    def run(self) -> bool:
        """Work until caught up (True) or out of its share (False)."""
        if self.row.feed_cursor is None:
            self.relist()
        caught_up = False
        while self.go_on():
            with self.admin_transaction() as conn:
                due = store.next_due(conn, self.row.id)
                unlisted = None if due is not None else store.next_unlisted(conn, self.row.id)
            if due is not None:
                self.read_file(due)
            elif unlisted is not None:
                self.list_folder(*unlisted)
            elif self.row.listing:
                with self.admin_transaction() as conn:
                    self.tally.removed += store.end_listing(conn, self.row)
                self.reload()
            elif not caught_up:
                caught_up = self.read_feed()
            else:
                return True
        return False

    def relist(self) -> None:
        """Note where the feed stands, then list everything again from the root."""
        self.call(2)
        cursor = self.source.latest_cursor()
        root = self.source.root()
        with self.admin_transaction() as conn:
            store.start_listing(conn, self.row, root_id=root, cursor=cursor)
        self.reload()

    def list_folder(self, folder_id: str, page: str | None) -> None:
        self.call()
        try:
            found = self.source.children(folder_id, page)
        except upstream.ItemGone:
            if folder_id == self.row.root_id:
                raise upstream.Refused(
                    f"{self.source.what} can't find the drive or folder this connector reads.") from None
            # A folder that went away since it was found: it goes, with everything under it.
            with self.admin_transaction() as conn:
                self.tally.removed += store.remove_items(conn, self.row.id, [folder_id])
            return
        with self.admin_transaction() as conn:
            store.store_children(conn, self.row, folder_id, found.items, found.next)

    def read_feed(self) -> bool:
        """One page of the change feed; whether it has caught up."""
        self.call()
        try:
            page = self.source.changes(self.row.feed_cursor)
        except upstream.FeedExpired:
            logger.info("connector %s: its change feed expired; listing everything again", self.row.id)
            self.relist()
            return False
        cursor = page.next or page.cursor
        with self.admin_transaction() as conn:
            self.tally.removed += store.apply_changes(conn, self.row, page.items, cursor)
        self.reload()
        return page.next is None

    def read_file(self, due: store.DueItem) -> None:
        item = due.item
        kind = parse.file_type(item.name)
        name = _file_name(item.name)
        if not due.content_due and due.document_id is None:
            # Skipped before and unchanged since: nothing to read.
            with self.admin_transaction() as conn:
                store.item_settled(conn, self.row.id, due, document_id=None, mirrored=None)
            return
        reason = (UNSUPPORTED if kind is None else BAD_NAME if name is None
                  else TOO_LARGE if item.size is not None and item.size > self.service.max_bytes else None)
        if reason is not None:
            self.skip(due, reason)
            return
        try:
            self.call()
            mirrored = mirror.mirror(self.source.grants(item), provider=self.row.kind, idp=self.service.idp,
                                     principal_map=self.row.principal_map)
            content = None
            if due.content_due or due.document_id is None:
                self.call(2)
                content = self.source.download(item, self.service.max_bytes)
        except upstream.TooLarge:
            self.skip(due, TOO_LARGE)
            return
        except upstream.ItemGone:
            self.skip(due, NOT_FETCHED)
            return
        with self.admin_transaction() as conn:
            document_id = self.store_document(conn, due, kind, name, content, mirrored)
            store.item_settled(conn, self.row.id, due, document_id=document_id, mirrored=mirrored)

    def skip(self, due: store.DueItem, reason: str) -> None:
        with self.admin_transaction() as conn:
            self.tally.removed += store.skip_item(conn, self.row.id, due, reason)
        self.tally.skipped += 1

    def store_document(self, conn: Any, due: store.DueItem, kind: parse.FileType, name: str, content: bytes | None,
                       mirrored: mirror.Mirrored) -> str | None:
        """The file's document, created or replaced (only when its content changed), and its readers."""
        current = None if due.document_id is None else store.document_digest(conn, self.row.id, due.document_id)
        if content is None or (current is not None and current == (hashlib.sha256(content).hexdigest(), name)):
            if current is None:
                return None  # Gone since it was read: its next change upstream brings it back.
            if store.set_readers(conn, self.row.id, due.document_id, mirrored.readers):
                self.tally.remirrored += 1
            return due.document_id
        digest = hashlib.sha256(content).hexdigest()
        title = (name.rpartition(".")[0] or name)[:300]
        if current is not None:
            # The readers first, so the new file is never readable by more than they are.
            store.set_readers(conn, self.row.id, due.document_id, mirrored.readers)
            brain_store.replace_document(conn, due.document_id, title=title, file_name=name,
                                         media_type=kind.media_type, content=content, sha256=digest,
                                         principal_id=self.admin.principal_id, uploader_groups=self.admin.groups)
            self.tally.replaced += 1
            return due.document_id
        document = brain_store.add_document(conn, space_id=self.row.space_id, title=title, file_name=name,
                                            media_type=kind.media_type, content=content, sha256=digest,
                                            uploader_groups=self.admin.groups, connector_id=self.row.id,
                                            readers=mirrored.readers)
        self.tally.added += 1
        return document["id"]


class ConnectorService:
    """The connectors of one server: its pool, the server key that opens their secrets, and the HTTP transport
    (None: the network; tests put a fake here)."""

    def __init__(self, settings: OrgSettings, pool: Any) -> None:
        self.settings = settings
        self.pool = pool
        self.idp = settings.idp
        self.server_key = settings.gateway_key
        self.max_bytes = settings.brain_max_upload_mb * 1024 * 1024
        self.transport: httpx.BaseTransport | None = None

    # -- who it acts as --------------------------------------------------------------------------

    def admin_of(self, principal_id: str) -> RequestPrincipal | None:
        """The connector's admin with the groups Row-Bot has for them now, when they are still an active admin."""
        with principal_transaction(self.pool, principal_id, ()) as conn:
            status = conn.execute("SELECT status FROM row_bot_org.principals WHERE id = %s",
                                  (principal_id,)).fetchone()
            groups = tuple(name for (name,) in conn.execute(
                "SELECT name FROM row_bot_org.principal_groups WHERE principal_id = %s ORDER BY name",
                (principal_id,)))
        if status is None or status[0] != "active":
            return None
        principal = RequestPrincipal(principal_id, groups, None)
        with principal_transaction(self.pool, principal_id, groups) as conn:
            return principal if roles.is_admin(conn, principal, self.settings) else None

    @contextmanager
    def as_admin(self, principal: RequestPrincipal) -> Iterator[Any]:
        with principal_transaction(self.pool, principal.principal_id, principal.groups) as conn:
            if not roles.enter_admin(conn, principal, self.settings):
                raise NotAdmin
            yield conn

    # -- the source ------------------------------------------------------------------------------

    def credentials(self, row: store.ConnectorRow, sealed: bytes | None) -> upstream.Credentials | str:
        """The connector's credentials with its secret opened, or why they can't be used."""
        if sealed is None:
            return NO_SECRET
        try:
            secret = secrets.unseal(self.server_key, row.id, row.kind, row.tenant_id, row.client_id, sealed)
        except secrets.SecretUnavailable:
            return SECRET_UNAVAILABLE
        return upstream.Credentials(row.kind, row.tenant_id, row.client_id, secret, row.subject)

    @staticmethod
    def source(client: httpx.Client, row: store.ConnectorRow, credentials: upstream.Credentials) -> Any:
        if row.kind == "microsoft":
            return upstream.MicrosoftSource(client, credentials, row.drive_id or "", row.folder_id)
        return upstream.GoogleSource(client, credentials, row.drive_id, row.folder_id)

    def check(self, row: store.ConnectorRow, sealed: bytes | None) -> str | None:
        """Test the connection (sign in, and look at the root folder): None when it works, else why not."""
        credentials = self.credentials(row, sealed)
        if isinstance(credentials, str):
            return credentials
        with upstream.http_client(self.transport) as client:
            source = self.source(client, row, credentials)
            try:
                source.sign_in()
                source.root()
            except upstream.SourceError as failed:
                return failed.reason
        return None

    # -- the stage -------------------------------------------------------------------------------

    def sync_due(self, stopping: Callable[[], bool]) -> bool:
        """Sync the connectors that are due, each at most once, within ``PASS_SECONDS``. Whether more is waiting
        (a turn, or the stage, stopped at a limit)."""
        deadline = time.monotonic() + PASS_SECONDS
        taken: list[str] = []
        more = False
        while not stopping():
            if time.monotonic() >= deadline:
                return True
            with brain_store.worker_transaction(self.pool) as conn:
                due = store.claim_due(conn, skip=taken)
            if due is None:
                break
            taken.append(due.id)
            try:
                more = self._turn(due, stopping, deadline) or more
            except Exception as error:  # noqa: BLE001 - one connector failing never stops the others
                logger.error("connector %s: its sync failed (%s)", due.id, type(error).__name__)
                self._release(due.id)
        return more

    def _release(self, connector_id: str) -> None:
        with brain_store.worker_transaction(self.pool) as conn:
            store.release(conn, connector_id)

    def _turn(self, due: store.Due, stopping: Callable[[], bool], deadline: float) -> bool:
        admin = self.admin_of(due.run_as)
        if admin is None:
            with brain_store.worker_transaction(self.pool) as conn:
                store.stop_without_admin(conn, due.id, NOT_AN_ADMIN)
            logger.warning("connector %s stopped: the admin it syncs as isn't one any more", due.id)
            return False
        try:
            with self.as_admin(admin) as conn:
                row = store.get_connector(conn, due.id)
                sealed = None if row is None else store.sealed_secret(conn, row.id)
        except NotAdmin:
            self._release(due.id)
            return False
        if row is None or row.state != "active":
            self._release(due.id)
            return False
        credentials = self.credentials(row, sealed)
        if isinstance(credentials, str):
            with self.as_admin(admin) as conn:
                store.refuse_turn(conn, row.id, reason=credentials, most=1, wait_seconds=MAX_WAIT_SECONDS)
            logger.warning("connector %s stopped: its secret can't be used", row.id)
            return False
        with upstream.http_client(self.transport) as client:
            turn = _Turn(self, admin, row, self.source(client, row, credentials), stopping, deadline)
            try:
                turn.call()
                turn.source.sign_in()
                caught_up = turn.run()
            except upstream.Refused as refused:
                self._refused(admin, turn.row, refused.reason)
                return False
            except upstream.Unavailable as down:
                self._unavailable(admin, turn.row, down)
                return False
            except NotAdmin:
                self._release(due.id)
                return False
        with self.as_admin(admin) as conn:
            store.finish_turn(conn, row.id, caught_up=caught_up,
                              wait_seconds=SYNC_EVERY_SECONDS if caught_up else 0.0)
        tally = turn.tally
        logger.info("connector %s synced%s: %d added, %d replaced, %d re-mirrored, %d removed, %d skipped",
                    row.id, "" if caught_up else " (more to do)", tally.added, tally.replaced, tally.remirrored,
                    tally.removed, tally.skipped)
        return not caught_up

    def _unavailable(self, admin: RequestPrincipal, row: store.ConnectorRow, down: upstream.Unavailable) -> None:
        wait = (failure_wait(row.failures + 1) if down.retry_after is None
                else max(MIN_WAIT_SECONDS, down.retry_after))
        with self.as_admin(admin) as conn:
            store.fail_turn(conn, row.id, wait_seconds=wait)
        logger.warning("connector %s waits %d s: %s", row.id, int(wait), down.reason)

    def _refused(self, admin: RequestPrincipal, row: store.ConnectorRow, reason: str) -> None:
        stopped_reason = (f"{reason} The connector stopped after {REFUSALS_TO_STOP} refusals in a row: put it right, "
                          "then save or resume the connector.")
        with self.as_admin(admin) as conn:
            refusals, stopped = store.refuse_turn(conn, row.id, reason=stopped_reason, most=REFUSALS_TO_STOP,
                                                  wait_seconds=refusal_wait(row.auth_failures + 1))
            space = brain_store.get_space(conn, row.space_id)
            layer = AuditLayer.COMPANY if space is not None and space.kind == brain_store.COMPANY else AuditLayer.TEAM
            audit.emit(conn, Action.CONNECTOR_CHANGED, actor=SERVER, target=Target(TargetKind.CONNECTOR, row.id),
                       outcome=Outcome.FAILED, layer=layer,
                       details={"change": "auth_failed", "kind": row.kind, "space_id": row.space_id,
                                "attempts": refusals})
        logger.warning("connector %s was refused (%d in a row%s)", row.id, refusals, ", stopped" if stopped else "")


__all__ = ["CALLS_PER_TURN", "MIN_WAIT_SECONDS", "PASS_SECONDS", "REFUSALS_TO_STOP", "SYNC_EVERY_SECONDS",
           "ConnectorService", "Tally", "failure_wait", "refusal_wait"]
