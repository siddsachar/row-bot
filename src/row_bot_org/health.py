"""Liveness (``/healthz``) and readiness (``/readyz``) with a registry of readiness checks.

There is no database yet, so a server has no check registered and is ready once its settings loaded
(it doesn't start without them). Later parts add theirs, the database first, with ``Readiness.add``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]


class Readiness:
    """The named checks ``/readyz`` runs; the server is ready when every one returns True."""

    def __init__(self) -> None:
        self._checks: dict[str, ReadinessCheck] = {}

    def add(self, name: str, check: ReadinessCheck) -> None:
        if name in self._checks:
            raise ValueError(f"a readiness check named {name!r} is already registered")
        self._checks[name] = check

    async def run(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name, check in self._checks.items():
            try:
                results[name] = await check() is True
            except Exception as error:  # noqa: BLE001 - a failing check means "not ready", never a 500
                # The type only: a check's error can carry a connection string.
                logger.warning("readiness check %s failed: %s", name, type(error).__name__)
                results[name] = False
        return results


def health_router(readiness: Readiness) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz() -> JSONResponse:
        results = await readiness.run()
        ready = all(results.values())
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "checks": {name: "ok" if passed else "failing" for name, passed in results.items()},
            },
            status_code=200 if ready else 503,
        )

    return router


__all__ = ["Readiness", "ReadinessCheck", "health_router"]
