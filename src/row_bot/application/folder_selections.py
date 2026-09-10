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


class FolderSelections:
    def __init__(self, *, picker: Callable[[], Path | None] = select_existing_folder,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.picker = picker
        self.clock = clock
        self._lock = threading.RLock()
        self._values: dict[str, _Selection] = {}

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
            self._values = {key: item for key, item in self._values.items() if item.expires > self.clock()}
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
