from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable


@dataclass(frozen=True)
class DeviceCodePrompt:
    verification_uri: str
    user_code: str
    expires_at: str
    interval_seconds: int = 5


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    refresh_token: str = ""
    expires_at: str = ""
    scopes: tuple[str, ...] = ()


class RefreshDedupe:
    def __init__(self) -> None:
        self._lock = Lock()

    def refresh(self, callback: Callable[[], OAuthToken]) -> OAuthToken:
        with self._lock:
            return callback()


def expiry_from_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, seconds))).isoformat()