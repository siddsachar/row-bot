"""The route authorization matrix (EN-Q1): every route the app serves, and how the access gate treats it.

`tests/snapshots/security/route_matrix.json` lists each method and path the started app serves (its routers,
the file mounts and the routes channels and plugins add once running) with what `AccessPolicy.classify`
answers: the kind (public, authenticated, delegated, launcher), and whether it changes state, needs the same
origin, or sends a signed-out browser to Connect. Dynamic segments are classified with a fixed sample value.

To change it on purpose, run this file with ROW_BOT_UPDATE_SNAPSHOTS=1 and commit the JSON with the change.
A reviewer reads every changed row and checks that:
- a new "public" row is on PUBLIC_HTTP_ROUTES (or APP_INSTALL_PATHS) and gives nothing away without a session;
- a new "delegated" row's route checks its caller itself (a signature, a webhook secret, an OAuth state);
- a non-GET row with changes_state false is in VIEW_ONLY_CHANGES below and truly changes nothing;
- a row that changed kind or lost require_same_origin did so intentionally, and an allow-list entry
  with no route ("allow_list_entries_without_a_route") is removed or explained.
"""
from __future__ import annotations

import importlib
import json
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.routing import iter_route_contexts
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import BaseRoute, Host, Match, Mount, Route, Router, WebSocketRoute
from starlette.staticfiles import StaticFiles

from row_bot.access.policy import (
    LAUNCHER_ONLY_PATHS,
    PUBLIC_HTTP_ROUTES,
    ROUTE_AUTHENTICATED_HTTP_ROUTES,
    AccessPolicy,
    RouteClassification,
    RouteKind,
    is_safe_method,
)
from tests.helpers.snapshots import assert_matches_snapshot

pytestmark = pytest.mark.subsystem

SNAPSHOT = Path(__file__).resolve().parents[2] / "snapshots" / "security" / "route_matrix.json"
# Callers with no Row-Bot session whose route checks them: task webhooks and plugin webhooks.
DELEGATED_PREFIXES = ("/api/webhook/", "/plugin-webhooks/")
# Reviewed: requests that are not GETs and change nothing (starting a client session, following a
# conversation live, the native window's helper, which checks its own one-time token or grant).
VIEW_ONLY_CHANGES = frozenset({
    ("POST", "/api/v1/handshake"),
    ("POST", "/api/v1/conversations/{conversation_id}/subscriptions"),
    ("DELETE", "/api/v1/subscriptions/{subscription_id}"),
    ("PUT", "/api/v1/subscriptions/{subscription_id}/ack"),
    ("POST", "/api/v1/native/attest"),
    ("POST", "/api/v1/native/authorize"),
    ("POST", "/api/v1/native/revoke"),
    ("POST", "/api/v1/native/selections/complete"),
    ("POST", "/api/v1/native/terminal/open"),
    ("POST", "/api/v1/native/terminal/external"),
    ("POST", "/api/v1/native/attachments/{reference}"),
})
_PARAMETER = re.compile(r"\{([^}:]+)(?::([^}]+))?\}")
_SAMPLES = {None: "sample", "str": "sample", "path": "sample", "int": "1", "float": "1.5",
            "uuid": "00000000-0000-4000-8000-000000000000"}
_FILE_METHODS = frozenset({"GET", "HEAD"})


class Unclassified(AssertionError):
    """A route the matrix cannot place: it would reach the gate untested."""


def _sample_path(template: str) -> str:
    def sample(match: re.Match[str]) -> str:
        if match.group(2) not in _SAMPLES:
            raise Unclassified(f"{template}: no sample value for the {match.group(2)!r} convertor")
        return _SAMPLES[match.group(2)]

    return _PARAMETER.sub(sample, template)


def _served(routes: Sequence[BaseRoute], prefix: str = "") -> Iterator[tuple[str, str, frozenset[str]]]:
    """(scope type, path template, methods) for every route, routers and mounts flattened."""
    for context in iter_route_contexts(routes):
        route = context.original_route
        path = prefix + str(context.path or "")
        if isinstance(route, Route):
            if context.methods is None:
                raise Unclassified(f"{path} accepts every method; name the methods it serves")
            yield "http", path, frozenset(context.methods)
        elif isinstance(route, WebSocketRoute):
            # FastAPI leaves an included WebSocket's context path empty; the route it serves has the full one.
            served = getattr(context, "starlette_route", route)
            yield "websocket", prefix + served.path, frozenset({"WEBSOCKET"})
        elif isinstance(route, Mount) and isinstance(route.app, StaticFiles):
            yield "http", path + "/{path:path}", _FILE_METHODS
        elif isinstance(route, Mount) and isinstance(route.app, Router):
            yield from _served(route.app.routes, path)
        else:
            raise Unclassified(f"{type(route).__name__} at {path or '?'} is not a route the matrix can classify")


def _scope(scope_type: str, method: str, path: str) -> dict:
    # Accept text/html: browser_navigation then says what a signed-out browser opening the path gets.
    if scope_type == "websocket":
        return {"type": "websocket", "path": path, "headers": []}
    return {"type": "http", "method": method, "path": path, "root_path": "",
            "headers": [(b"accept", b"text/html")]}


def _row(method: str, path: str, classification: RouteClassification, **extra: str) -> dict:
    return {"method": method, "path": path, **extra, "kind": classification.kind.value,
            "changes_state": classification.changes_state,
            "require_same_origin": classification.require_same_origin,
            "browser_navigation": classification.browser_navigation}


def _serving_route(routes: Sequence[BaseRoute], method: str, path: str) -> str | None:
    """The template of the first route that serves *method* *path*, as Starlette picks it."""
    scope = _scope("http", method, path)
    for context in iter_route_contexts(routes):
        match, _child = context.matches(scope)
        if match is Match.FULL:
            route = context.original_route
            if isinstance(route, Mount):
                return str(context.path) + "/{path:path}"
            return str(context.path)
    return None


def build_matrix(routes: Sequence[BaseRoute], policy: AccessPolicy | None = None) -> dict:
    policy = policy or AccessPolicy()
    rows: dict[tuple[str, str], dict] = {}
    for scope_type, template, methods in _served(routes):
        for method in sorted(methods):
            classification = policy.classify(_scope(scope_type, method, _sample_path(template)))
            rows[(template, method)] = _row(method, template, classification)
    # The reviewed exact paths a wider route serves (the install files in /app-v2/, the glyph in /static).
    unserved = []
    for method, path in sorted(PUBLIC_HTTP_ROUTES | ROUTE_AUTHENTICATED_HTTP_ROUTES):
        template = _serving_route(routes, method, path)
        if template is None:
            unserved.append({"method": method, "path": path})
        elif template != path:
            classification = policy.classify(_scope("http", method, path))
            rows[(path, method)] = _row(method, path, classification, served_by=template)
    for path in sorted(LAUNCHER_ONLY_PATHS):
        if not any(_serving_route(routes, method, path) for method in ("GET", "POST")):
            unserved.append({"method": "*", "path": path})
    return {"routes": [rows[key] for key in sorted(rows)], "allow_list_entries_without_a_route": unserved}


def problems(matrix: dict) -> list[str]:
    """Rows the reviewed allow-lists don't account for."""
    found = []
    for row in matrix["routes"]:
        method, path, kind = row["method"], row["path"], RouteKind(row["kind"])
        where = f"{method} {path} ({kind.value})"
        if kind is RouteKind.PUBLIC and (method, path) not in PUBLIC_HTTP_ROUTES:
            found.append(f"{where}: public but not on PUBLIC_HTTP_ROUTES")
        elif kind is RouteKind.DELEGATED and not (
            (method, path) in ROUTE_AUTHENTICATED_HTTP_ROUTES or path.startswith(DELEGATED_PREFIXES)
        ):
            found.append(f"{where}: delegated but not a known callback or webhook")
        elif kind is RouteKind.LAUNCHER and not (path in LAUNCHER_ONLY_PATHS or path.startswith("/api/launcher/")):
            found.append(f"{where}: launcher-only but not a launcher path")
        elif kind is RouteKind.AUTHENTICATED and method == "WEBSOCKET" and not row["require_same_origin"]:
            found.append(f"{where}: a WebSocket must need the same origin")
        elif kind is RouteKind.AUTHENTICATED and method != "WEBSOCKET" and not is_safe_method(method):
            if not row["require_same_origin"]:
                found.append(f"{where}: changes are refused cross-origin, this one is not")
            if not row["changes_state"] and (method, path) not in VIEW_ONLY_CHANGES:
                found.append(f"{where}: not a GET, yet not marked as a change (add it to VIEW_ONLY_CHANGES if reviewed)")
    return found


@pytest.fixture
def served_routes(tmp_path, monkeypatch) -> Iterator[list[BaseRoute]]:
    """The routes the started server serves, in the order it matches them."""
    app_module = importlib.import_module("row_bot.app")
    from row_bot import server
    from row_bot.plugins import webhooks

    # The file mounts the server adds when it starts, with the font cache present.
    (tmp_path / "font_cache").mkdir()
    monkeypatch.setattr(app_module, "get_row_bot_data_dir", lambda: tmp_path)
    files = Starlette()
    app_module.mount_file_routes(files)
    # Routes added while the server runs, recorded instead of added to the shared app.
    late = Router()
    monkeypatch.setattr(server, "add_late_route",
                        lambda path, endpoint, *, methods: late.add_route(path, endpoint, methods=list(methods)))
    monkeypatch.setattr(webhooks, "_route_mounted", False)
    webhooks.register_plugin_webhook("route-matrix", "events", lambda request: None)
    # The SMS channel mounts this once Twilio is connected (channels/sms.py).
    late.add_route("/sms", lambda request: None, methods=["POST"])
    yield [*app_module.app.routes, *files.routes, *late.routes]
    webhooks.unregister_plugin_webhooks("route-matrix")


def _changes(recorded: dict, live: dict) -> list[str]:
    def keyed(matrix: dict) -> dict[tuple[str, str], dict]:
        return {(row["path"], row["method"]): row for row in matrix["routes"]}

    before, after = keyed(recorded), keyed(live)
    lines = [f"added   {after[key]}" for key in sorted(after.keys() - before.keys())]
    lines += [f"removed {before[key]}" for key in sorted(before.keys() - after.keys())]
    lines += [f"changed {before[key]} -> {after[key]}"
              for key in sorted(before.keys() & after.keys()) if before[key] != after[key]]
    if recorded["allow_list_entries_without_a_route"] != live["allow_list_entries_without_a_route"]:
        lines.append(f"allow-list entries without a route now {live['allow_list_entries_without_a_route']}")
    return lines


def _render(matrix: dict) -> str:
    """JSON with one route per line, so a change to a route is a one-line diff."""
    def rows(items: list[dict]) -> str:
        return ",\n".join("  " + json.dumps(item) for item in items)

    return ('{\n "routes": [\n' + rows(matrix["routes"]) + '\n ],\n "allow_list_entries_without_a_route": [\n'
            + rows(matrix["allow_list_entries_without_a_route"]) + "\n ]\n}\n")


def test_every_served_route_is_on_the_reviewed_matrix(served_routes) -> None:
    live = build_matrix(served_routes)

    if SNAPSHOT.exists() and os.environ.get("ROW_BOT_UPDATE_SNAPSHOTS") != "1":
        changes = _changes(json.loads(SNAPSHOT.read_text(encoding="utf-8")), live)
        assert not changes, (
            "The routes or their authorization changed. Review each line, then record the matrix with "
            "ROW_BOT_UPDATE_SNAPSHOTS=1:\n" + "\n".join(changes)
        )
    assert_matches_snapshot(SNAPSHOT, _render(live))


def test_every_served_route_is_accounted_for_by_the_allow_lists(served_routes) -> None:
    assert problems(build_matrix(served_routes)) == []


def test_a_new_route_shows_up_as_a_change_to_review(served_routes) -> None:
    extra = APIRouter()

    @extra.post("/api/v1/unreviewed/{item_id}")
    async def unreviewed(item_id: str) -> dict:
        return {}

    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    changes = _changes(recorded, build_matrix([*served_routes, *extra.routes]))

    assert changes == [
        "added   " + str({"method": "POST", "path": "/api/v1/unreviewed/{item_id}", "kind": "authenticated",
                          "changes_state": True, "require_same_origin": True, "browser_navigation": False})
    ]


@pytest.mark.parametrize("route", [
    Route("/any-method", PlainTextResponse, methods=None),
    Mount("/sub-app", app=PlainTextResponse("an ASGI app the matrix can't see into")),
    Host("example.test", app=Router()),
], ids=["every-method", "opaque-mount", "host"])
def test_a_route_the_matrix_cannot_classify_fails(route: BaseRoute) -> None:
    with pytest.raises(Unclassified):
        build_matrix([route])


class _LoosePolicy(AccessPolicy):
    """A policy regression: routes off the allow-lists let through, a change not marked as one."""

    def classify(self, scope) -> RouteClassification:
        loosened = {"/api/v1/leak": RouteClassification(RouteKind.PUBLIC),
                    "/api/v1/hook": RouteClassification(RouteKind.DELEGATED),
                    "/api/v1/quiet": RouteClassification(RouteKind.AUTHENTICATED, require_same_origin=True)}
        return loosened.get(str(scope.get("path")), super().classify(scope))


def test_a_route_let_through_off_the_allow_lists_fails() -> None:
    routes = [Route("/api/v1/leak", PlainTextResponse, methods=["GET"]),
              Route("/api/v1/hook", PlainTextResponse, methods=["POST"]),
              Route("/api/v1/quiet", PlainTextResponse, methods=["POST"])]

    found = problems(build_matrix(routes, _LoosePolicy()))

    assert [line.split(":")[0] for line in found] == [
        "POST /api/v1/hook (delegated)",
        "GET /api/v1/leak (public)",
        "HEAD /api/v1/leak (public)",
        "POST /api/v1/quiet (authenticated)",
    ]
