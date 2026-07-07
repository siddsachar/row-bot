from __future__ import annotations

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from row_bot.mobile.access_gate import MobileAccessGate
from row_bot.mobile.routes import register_mobile_routes
from row_bot.mobile.store import MobileAuthStore


def _app(tmp_path) -> FastAPI:
    app = FastAPI()
    store = MobileAuthStore(tmp_path / "mobile.db")

    @app.get("/")
    async def root():
        return {"ok": True, "surface": "root"}

    @app.get("/_media/{path:path}")
    async def media(path: str):  # noqa: ARG001
        return JSONResponse({"private": "media"})

    @app.get("/published/{path:path}")
    async def published(path: str):  # noqa: ARG001
        return JSONResponse({"private": "published"})

    @app.post("/api/webhook/{task_id}")
    async def webhook(task_id: str):  # noqa: ARG001
        return {"ok": True}

    @app.post("/api/voice/realtime/client-secret")
    async def voice_secret():
        return {"secret": "should-not-leak"}

    register_mobile_routes(app, store=store)
    app.add_middleware(MobileAccessGate, store=store)
    return app


def test_unpaired_remote_can_only_reach_minimal_mobile_routes(tmp_path) -> None:
    app = _app(tmp_path)
    remote = TestClient(app, client=("192.168.1.25", 50000), follow_redirects=False)

    assert remote.get("/api/mobile/session").status_code == 200
    assert remote.get("/mobile/pair").status_code == 200
    assert remote.get("/", headers={"accept": "text/html"}).status_code == 303
    assert remote.get("/_media/thread/file.png").status_code == 401
    assert remote.get("/published/page.html").status_code == 401
    assert remote.post("/api/webhook/task-1").status_code == 401
    assert remote.post("/api/voice/realtime/client-secret").status_code == 401


def test_forwarded_localhost_cannot_bypass_gate(tmp_path) -> None:
    app = _app(tmp_path)
    client = TestClient(app, client=("127.0.0.1", 50000), follow_redirects=False)

    response = client.get(
        "/published/page.html",
        headers={"x-forwarded-for": "203.0.113.4"},
    )

    assert response.status_code == 401


def test_paired_remote_cookie_allows_app_and_private_routes(tmp_path) -> None:
    app = _app(tmp_path)
    local = TestClient(app, client=("127.0.0.1", 50000))
    code = local.post("/api/mobile/pair/start", json={}).json()["pairing"]["code"]
    remote = TestClient(app, client=("192.168.1.25", 50000), follow_redirects=False)

    confirm = remote.post("/api/mobile/pair/confirm", json={"code": code, "display_name": "Phone"})
    cookie_value = confirm.cookies.get("row_bot_mobile_lan")
    assert cookie_value

    root = remote.get("/", cookies={"row_bot_mobile_lan": cookie_value})
    media = remote.get("/_media/thread/file.png", cookies={"row_bot_mobile_lan": cookie_value})
    published = remote.get("/published/page.html", cookies={"row_bot_mobile_lan": cookie_value})

    assert root.status_code == 200
    assert media.status_code == 200
    assert published.status_code == 200


def test_revoked_cookie_is_blocked_by_gate(tmp_path) -> None:
    app = _app(tmp_path)
    local = TestClient(app, client=("127.0.0.1", 50000))
    code = local.post("/api/mobile/pair/start", json={}).json()["pairing"]["code"]
    remote = TestClient(app, client=("192.168.1.25", 50000), follow_redirects=False)
    confirm = remote.post("/api/mobile/pair/confirm", json={"code": code, "display_name": "Phone"})
    cookie_value = confirm.cookies.get("row_bot_mobile_lan")
    device_id = confirm.json()["device"]["id"]

    local.post(f"/api/mobile/devices/{device_id}/revoke")
    response = remote.get("/published/page.html", cookies={"row_bot_mobile_lan": cookie_value})

    assert response.status_code == 401
