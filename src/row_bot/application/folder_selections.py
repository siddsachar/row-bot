"""Ephemeral exact-directory grants created by an explicit local-owner picker."""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder

from row_bot.application.client_platform import ClientPlatformError


def select_existing_folder() -> Path | None:
    """Headless hosts have no picker until their composition root supplies one."""
    raise ClientPlatformError("capability_unavailable")


@dataclass(frozen=True)
class _Selection:
    session_id: str
    path: Path
    expires: float


@dataclass(frozen=True)
class FolderSelectionScope:
    """Exact authority captured before a native folder chooser is opened.

    ``authority_grant`` is the current authenticated client/native grant.  It
    is intentionally distinct from the folder grant minted after selection.
    Callers must construct this value from authenticated server state, never
    directly from renderer claims.
    """

    session_id: str
    instance_id: str
    window_id: str
    window_epoch: int
    intent: str
    conversation_id: str | None
    destination: str
    authority_grant: str
    policy_revision: str


@dataclass(frozen=True)
class _NativeIntent:
    scope: FolderSelectionScope
    expires: float


@dataclass(frozen=True)
class _ExactSelection:
    scope: FolderSelectionScope
    path: Path
    expires: float


def _valid_scope(scope: FolderSelectionScope) -> bool:
    values = (
        scope.session_id,
        scope.instance_id,
        scope.window_id,
        scope.intent,
        scope.destination,
        scope.authority_grant,
        scope.policy_revision,
    )
    return (
        type(scope.window_epoch) is int
        and 0 <= scope.window_epoch <= 2**63 - 1
        and all(isinstance(value, str) and 0 < len(value) <= 256 for value in values)
        and (scope.conversation_id is None
             or isinstance(scope.conversation_id, str) and 0 < len(scope.conversation_id) <= 256)
    )


class FolderSelections:
    def __init__(self, *, picker: Callable[[], Path | None] = select_existing_folder,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.picker = picker
        self.clock = clock
        self._lock = threading.RLock()
        self._values: dict[str, _Selection] = {}
        self._native_intents: dict[str, _NativeIntent] = {}
        self._exact_values: dict[str, _ExactSelection] = {}
        self._inflight_intents: set[str] = set()

    def _prune(self) -> None:
        now = self.clock()
        self._values = {key: item for key, item in self._values.items() if item.expires > now}
        self._native_intents = {
            key: item for key, item in self._native_intents.items() if item.expires > now
        }
        self._exact_values = {
            key: item for key, item in self._exact_values.items() if item.expires > now
        }

    def pick(self, session_id: str, validate: Callable[[], None]) -> dict:
        validate()
        path = self.picker()
        validate()  # A chooser may remain open after revocation/navigation.
        if path is None:
            return {"status": "cancelled"}
        from row_bot.developer.review import scoped_workspace_path
        path = scoped_workspace_path(Path(path))
        if not path.is_dir():
            raise ClientPlatformError("invalid_resource")
        with self._lock:
            self._prune()
            if len(self._values) >= 128:
                raise ClientPlatformError("rate_limited")
            identity = secrets.token_urlsafe(32)
            self._values[identity] = _Selection(session_id, path, self.clock() + 300)
        return {"status": "selected", "grant_id": identity, "name": path.name}

    def resolve(self, grant_id: str, session_id: str) -> AuthorizedWorkspaceFolder:
        from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder
        from row_bot.developer.review import scoped_workspace_path
        with self._lock:
            grant = self._values.get(grant_id)
            if not grant or grant.session_id != session_id or grant.expires <= self.clock():
                raise ClientPlatformError("capability_revoked")
            path = scoped_workspace_path(grant.path)
            if not path.is_dir():
                raise ClientPlatformError("resource_unavailable")
            return AuthorizedWorkspaceFolder(path, path, grant_id)

    def begin_exact(self, scope: FolderSelectionScope) -> str:
        """Create a short-lived, one-shot native chooser intent.

        This operation only records authority.  It does not open a chooser or
        perform any filesystem mutation.
        """
        if not isinstance(scope, FolderSelectionScope) or not _valid_scope(scope):
            raise ClientPlatformError("invalid_request")
        with self._lock:
            self._prune()
            if len(self._native_intents) >= 128:
                raise ClientPlatformError("rate_limited")
            identity = secrets.token_urlsafe(32)
            self._native_intents[identity] = _NativeIntent(scope, self.clock() + 120)
            return identity

    def cancel_exact(self, intent_id: str, scope: FolderSelectionScope) -> bool:
        """Consume a matching chooser intent without creating a folder grant."""
        with self._lock:
            self._prune()
            current = self._native_intents.get(intent_id)
            if current is None or current.scope != scope:
                return False
            del self._native_intents[intent_id]
            return True

    def complete_exact(
        self,
        intent_id: str,
        scope: FolderSelectionScope,
        path: Path | None,
        validate: Callable[[], None],
    ) -> dict:
        """Consume an exact native intent and mint a one-shot opaque grant.

        The chooser may remain open while its document navigates or its policy
        changes.  Validation therefore happens both before consuming the
        intent and immediately before publishing the grant.
        """
        validate()
        with self._lock:
            self._prune()
            current = self._native_intents.get(intent_id)
            if (current is None or current.scope != scope
                    or intent_id in self._inflight_intents):
                raise ClientPlatformError("capability_revoked")
            self._inflight_intents.add(intent_id)
        try:
            if path is None:
                with self._lock:
                    self._native_intents.pop(intent_id, None)
                return {"status": "cancelled"}
            from row_bot.developer.review import scoped_workspace_path
            selected = scoped_workspace_path(Path(path))
            if not selected.is_dir():
                raise ClientPlatformError("invalid_resource")
            validate()
            with self._lock:
                self._prune()
                current = self._native_intents.get(intent_id)
                if current is None or current.scope != scope:
                    raise ClientPlatformError("capability_revoked")
                del self._native_intents[intent_id]
                if len(self._exact_values) >= 128:
                    raise ClientPlatformError("rate_limited")
                identity = secrets.token_urlsafe(32)
                self._exact_values[identity] = _ExactSelection(scope, selected, self.clock() + 300)
            return {"status": "selected", "grant_id": identity, "name": selected.name}
        finally:
            with self._lock:
                self._inflight_intents.discard(intent_id)
                # Errors consume the intent so an invalid or revoked chooser
                # completion cannot be replayed after its authority changes.
                self._native_intents.pop(intent_id, None)

    def consume_exact(
        self,
        grant_id: str,
        scope: FolderSelectionScope,
        validate: Callable[[], None],
    ) -> AuthorizedWorkspaceFolder:
        """Atomically consume a grant bound to the complete captured scope."""
        from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder
        from row_bot.developer.review import scoped_workspace_path
        validate()
        with self._lock:
            self._prune()
            grant = self._exact_values.get(grant_id)
            if grant is None or grant.scope != scope:
                raise ClientPlatformError("capability_revoked")
            del self._exact_values[grant_id]
        validate()
        selected = scoped_workspace_path(grant.path)
        if not selected.is_dir():
            raise ClientPlatformError("resource_unavailable")
        return AuthorizedWorkspaceFolder(selected, selected, grant_id)

    def consume_exact_resource(
        self,
        grant_id: str,
        session_id: str,
        validate: Callable[[FolderSelectionScope], None],
    ) -> tuple[AuthorizedWorkspaceFolder, FolderSelectionScope] | None:
        """Consume a native workspace grant without trusting renderer scope.

        The complete scope comes from the server-owned grant.  Callers supply
        only the authenticated session and an authority revalidator.  ``None``
        means the identifier belongs to the retained legacy picker path.
        """
        from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder
        from row_bot.developer.review import scoped_workspace_path

        with self._lock:
            self._prune()
            grant = self._exact_values.get(grant_id)
            if grant is None:
                return None
            scope = grant.scope
            if (
                scope.session_id != session_id
                or scope.intent not in {"resource_setup", "resource_continue"}
                or scope.destination
                not in {"workspace:existing_folder", "workspace:empty_folder"}
            ):
                raise ClientPlatformError("capability_revoked")
            validate(scope)
            del self._exact_values[grant_id]
        validate(scope)
        selected = scoped_workspace_path(grant.path)
        if not selected.is_dir():
            raise ClientPlatformError("resource_unavailable")
        return AuthorizedWorkspaceFolder(selected, selected, grant_id), scope

    def revoke_window(self, *, instance_id: str, session_id: str, window_id: str) -> None:
        """Revoke all intents and exact grants owned by one native window."""
        def retained(scope: FolderSelectionScope) -> bool:
            return not (
                scope.instance_id == instance_id
                and scope.session_id == session_id
                and scope.window_id == window_id
            )
        with self._lock:
            self._native_intents = {
                key: value for key, value in self._native_intents.items()
                if retained(value.scope)
            }
            self._inflight_intents.intersection_update(self._native_intents)
            self._exact_values = {
                key: value for key, value in self._exact_values.items()
                if retained(value.scope)
            }
