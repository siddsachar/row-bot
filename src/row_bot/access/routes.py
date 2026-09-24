"""HTTP routes for Row-Bot device invitations and access sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import asyncio
import json
import os
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs

from row_bot.access.access_routes import (
    AccessRouteConfigStore,
    ListenMode,
    apply_listen_mode,
    build_route_inventory,
    discover_private_lan_addresses,
)
from row_bot.access.tailscale import (
    TailscaleOwnershipStore,
    TailscaleServeController,
    process_tailscale_status_cache,
)
from row_bot.app_port import get_app_port

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from row_bot.access.config import AccessConfig
from row_bot.access.cookies import AccessCookieManager
from row_bot.access.models import SessionLifetime
from row_bot.access.request_context import (
    ACCESS_CONTEXT_SCOPE_KEY,
    AccessContext,
    RequestProvenance,
    request_origin_matches,
)
from row_bot.access.service import (
    REMOTE_CLIENT_PATH,
    AccessService,
    InvitationClaimError,
)

ACCESS_REQUEST_BODY_LIMIT = 32 * 1024
_CLAIM_RATE_LIMIT = 20
_MANAGEMENT_RATE_LIMIT = 30
_TAILSCALE_COMMANDS: dict[tuple[str, str, str], dict[str, Any]] = {}
_TAILSCALE_COMMAND_LOCK = threading.RLock()

ACCESS_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "no-referrer",
}
CONNECT_PAGE_HEADERS = {
    **ACCESS_RESPONSE_HEADERS,
    # Same-origin POST navigations must retain an Origin header in Chromium.
    # The invitation is removed from the visible URL before the form renders,
    # and cross-origin referrers remain suppressed by this policy.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}
_TERMINAL_MESSAGES = {
    "already_claimed": (
        "This invitation was already used.",
        "Ask the owner to create a new invitation.",
    ),
    "cancelled": (
        "This invitation was cancelled.",
        "Ask the owner to create a new invitation.",
    ),
    "expired": (
        "This invitation expired.",
        "Invitations last 10 minutes. Ask the owner to create a new one.",
    ),
    "locked": (
        "This invitation is temporarily locked.",
        "Ask the owner to cancel it and create a new invitation.",
    ),
    "invalid_invitation": (
        "This invitation is invalid.",
        "Check the complete link or ask the owner to create a new invitation.",
    ),
    "origin_mismatch": (
        "This invitation is for a different Row-Bot address.",
        "Open the exact address shown by the owner.",
    ),
    "immutable_mismatch": (
        "This invitation could not be used.",
        "Ask the owner to create a new invitation.",
    ),
}


def _context(request: Request) -> AccessContext | None:
    context = request.scope.get(ACCESS_CONTEXT_SCOPE_KEY)
    if isinstance(context, AccessContext):
        return context
    state_context = getattr(request.state, "row_bot_access_context", None)
    return state_context if isinstance(state_context, AccessContext) else None


def _json(
    payload: Mapping[str, Any],
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        dict(payload),
        status_code=status_code,
        headers=ACCESS_RESPONSE_HEADERS,
    )


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return _json(
        {"ok": False, "error": code, "detail": detail},
        status_code=status_code,
    )


def _owner_context(request: Request) -> AccessContext | None:
    context = _context(request)
    if context is None or not context.authenticated:
        return None
    return context


def _origin_ok(request: Request, context: AccessContext | None) -> bool:
    return context is not None and request_origin_matches(context, request.scope)


def _rate_subject(context: AccessContext | None) -> str:
    if context is None:
        return "unidentified"
    return context.session_id or context.effective_client or "unidentified"


def _rate_ok(
    service: AccessService,
    context: AccessContext | None,
    *,
    bucket: str,
    limit: int,
) -> bool:
    return service.consume_request_budget(
        bucket,
        _rate_subject(context),
        limit=limit,
    )


def _too_many_requests() -> JSONResponse:
    response = _error(
        429,
        "rate_limited",
        "Too many access requests. Wait before trying again.",
    )
    response.headers["Retry-After"] = "60"
    return response


def _payload_too_large() -> JSONResponse:
    return _error(
        413,
        "request_too_large",
        "The access request body is too large.",
    )


def _is_json_request(request: Request) -> bool:
    return (
        request.headers.get("content-type", "").lower().startswith("application/json")
    )


class AccessPayloadTooLarge(ValueError):
    """Raised before an access route buffers an oversized request body."""


async def _payload(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length", "").strip()
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            return {}
        if declared_size > ACCESS_REQUEST_BODY_LIMIT:
            raise AccessPayloadTooLarge
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > ACCESS_REQUEST_BODY_LIMIT:
            raise AccessPayloadTooLarge
        chunks.append(bytes(chunk))
    raw = b"".join(chunks)
    if _is_json_request(request):
        try:
            value = json.loads(raw)
        except Exception:
            return {}
        return dict(value) if isinstance(value, Mapping) else {}
    try:
        body = raw.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return {}
    parsed = parse_qs(body, keep_blank_values=True)
    return {name: values[-1] if values else "" for name, values in parsed.items()}


def _safe_script_string(value: str) -> str:
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")


def _page_shell(title: str, content: str, *, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111719; color: #edf5f5; }}
    main {{ width: min(92vw, 460px); padding: 28px; }}
    h1 {{ margin: 0 0 12px; font-size: 1.8rem; }}
    p {{ color: #b3c1c3; line-height: 1.5; }}
    .detail {{ color: #e5eeee; }}
    .warning {{ border-left: 3px solid #f6bf54; padding-left: 12px; }}
    label {{ display: block; margin: 20px 0 8px; }}
    input {{ box-sizing: border-box; width: 100%; padding: 12px; border: 1px solid #3a555a; border-radius: 8px; background: #172225; color: inherit; }}
    button {{ width: 100%; margin-top: 18px; padding: 13px; border: 0; border-radius: 8px; background: #00b6c7; color: #061114; font-weight: 700; }}
    code {{ color: #c6f5f8; }}
  </style>
  {script}
</head>
<body><main>{content}</main></body>
</html>"""


def _neutral_connect_page() -> str:
    return _page_shell(
        "Connect to Row-Bot",
        """
<h1>Connect to this Row-Bot</h1>
<p>This Row-Bot requires approval from its owner.</p>
<p class="detail">Open a one-time invitation link in this browser.</p>
<p>Owner of this server? Run <code>row-bot access invite --layout desktop --origin &lt;address&gt;</code>.</p>
""",
    )


def _terminal_connect_page(reason: str) -> str:
    heading, detail = _TERMINAL_MESSAGES.get(
        reason, _TERMINAL_MESSAGES["invalid_invitation"]
    )
    return _page_shell(
        "Invitation unavailable",
        f"""
<h1>{escape(heading)}</h1>
<p>{escape(detail)}</p>
<p><a href="/connect">Return to the connection page</a></p>
""",
    )


def _available_connect_page(
    *,
    token: str,
    lifetime: SessionLifetime,
    next_path: str,
) -> str:
    duration = "30 days" if lifetime is SessionLifetime.TRUSTED else "12 hours"
    layout = "Responsive unified client"
    warning = (
        '<p class="warning">This authenticated browser receives full owner '
        "access to Row-Bot, including files, tools, providers, and settings.</p>"
    )
    script = f"""<script>
(() => {{
  const invitation = {_safe_script_string(token)};
  const clean = new URL(window.location.href);
  clean.searchParams.delete('invitation');
  window.history.replaceState(null, '', clean.pathname + clean.search + clean.hash);
  window.addEventListener('DOMContentLoaded', () => {{
    document.getElementById('invitation').value = invitation;
  }});
}})();
</script>"""
    return _page_shell(
        "Connect to Row-Bot",
        f"""
<h1>Connect this browser to Row-Bot?</h1>
<p class="detail">Access: Full owner access<br>Layout: {layout}<br>Duration: {duration}</p>
{warning}
<form method="post" action="/api/access/invitations/claim">
  <input id="invitation" name="invitation" type="hidden" value="">
  <input name="next" type="hidden" value="{escape(next_path, quote=True)}">
  <label for="display_name">Device name</label>
  <input id="display_name" name="display_name" maxlength="80" autocomplete="nickname" placeholder="This browser">
  <button type="submit">Connect</button>
</form>
""",
        script=script,
    )


def _claim_status(reason: str) -> int:
    if reason == "origin_mismatch":
        return 403
    if reason in {"already_claimed", "immutable_mismatch"}:
        return 409
    if reason in {"expired", "cancelled"}:
        return 410
    if reason == "locked":
        return 429
    return 400


def _invitation_public(invitation) -> dict[str, Any]:
    return invitation.to_public_dict()


def _device_public(service: AccessService, device) -> dict[str, Any]:
    sessions = [
        session.to_public_dict()
        for session in service.list_sessions(
            device_id=device.id,
            include_revoked=True,
        )
    ]
    return {**device.to_public_dict(), "sessions": sessions}


def _route_inventory(request: Request, context: AccessContext):
    """Read current owner-visible routes without probing or changing access."""
    port = get_app_port()
    config = AccessRouteConfigStore().load_or_default()
    ownership_store = TailscaleOwnershipStore()
    verified = process_tailscale_status_cache().get(
        instance_key=str(ownership_store.path.resolve(strict=False)),
        port=port,
        ownership=ownership_store.load(),
    )
    from row_bot.tunnel import tunnel_manager

    return build_route_inventory(
        port=port,
        config=config,
        lan_addresses=discover_private_lan_addresses(),
        tailscale_state=verified.status if verified else None,
        ngrok_url=tunnel_manager.get_url(port),
        reverse_proxy_origins=(
            *config.configured_origins,
            *AccessConfig.from_env().public_origins,
        ),
        current_server_origin=context.origin,
    )


def _route_settings(context: AccessContext) -> dict[str, Any]:
    config = AccessRouteConfigStore().load_or_default()
    return {
        "listen_mode": config.listen_mode.value,
        "configured_origins": list(config.configured_origins),
        "managed_externally": "ROW_BOT_ALLOWED_HOSTS" in os.environ,
        "can_manage_routes": context.is_local_owner,
    }


class AccessSessionAuthenticator:
    """Adapt access cookies and :class:`AccessService` to access middleware."""

    def __init__(
        self,
        service: AccessService,
        cookies: AccessCookieManager,
    ) -> None:
        self.service = service
        self.cookies = cookies

    def authenticate_scope(
        self,
        scope: Mapping[str, object],
        provenance: RequestProvenance,
    ):
        mutable_scope = dict(scope)
        token = self.cookies.extract_from_scope(
            mutable_scope,
            context=provenance,
        )
        if token:
            session = self.service.validate_session(token)
            if session is not None:
                return session
        legacy = self.cookies.extract_legacy_from_scope(
            mutable_scope,
            context=provenance,
        )
        if legacy:
            return self.service.validate_legacy_session(legacy)
        return None


@dataclass(frozen=True, slots=True)
class AccessRouteRegistration:
    service: AccessService
    config: AccessConfig
    cookies: AccessCookieManager
    authenticator: AccessSessionAuthenticator


def build_access_router(
    *,
    service: AccessService,
    cookies: AccessCookieManager,
    tailscale_controller: TailscaleServeController | None = None,
) -> APIRouter:
    router = APIRouter()
    selected_tailscale = tailscale_controller or TailscaleServeController()

    async def connect_page(request: Request) -> HTMLResponse:
        token = str(request.query_params.get("invitation") or "").strip()
        if not token:
            return HTMLResponse(
                _neutral_connect_page(),
                headers=CONNECT_PAGE_HEADERS,
            )
        try:
            context = _context(request)
            inspection = service.inspect_invitation(
                token,
                effective_client=(
                    context.effective_client if context is not None else None
                ),
            )
        except InvitationClaimError:
            return HTMLResponse(
                _terminal_connect_page("invalid_invitation"),
                status_code=400,
                headers=CONNECT_PAGE_HEADERS,
            )
        if inspection.status != "available":
            return HTMLResponse(
                _terminal_connect_page(inspection.status),
                status_code=_claim_status(inspection.status),
                headers=CONNECT_PAGE_HEADERS,
            )
        # Pairing always enters the independent responsive client. The root
        # path remains the default NiceGUI client for ordinary local launches.
        next_path = REMOTE_CLIENT_PATH
        return HTMLResponse(
            _available_connect_page(
                token=token,
                lifetime=inspection.invitation.session_lifetime,
                next_path=next_path,
            ),
            headers=CONNECT_PAGE_HEADERS,
        )

    async def claim_invitation(request: Request) -> Response:
        context = _context(request)
        if context is None:
            return _error(
                503,
                "access_context_unavailable",
                "Access context is unavailable.",
            )
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service,
            context,
            bucket="invitation_claim",
            limit=_CLAIM_RATE_LIMIT,
        ):
            return _too_many_requests()
        try:
            payload = await _payload(request)
        except AccessPayloadTooLarge:
            return _payload_too_large()
        token = str(payload.get("invitation") or "").strip()
        display_name = (
            str(payload.get("display_name") or "").strip() or "Connected browser"
        )
        next_path = REMOTE_CLIENT_PATH
        try:
            claim = service.claim_invitation(
                token,
                intended_origin=context.origin,
                display_name=display_name,
                user_agent=request.headers.get("user-agent"),
                effective_client=context.effective_client,
            )
        except InvitationClaimError as exc:
            status = _claim_status(exc.reason)
            if _is_json_request(request):
                heading, detail = _TERMINAL_MESSAGES.get(
                    exc.reason,
                    _TERMINAL_MESSAGES["invalid_invitation"],
                )
                return _error(status, exc.reason, f"{heading} {detail}")
            return HTMLResponse(
                _terminal_connect_page(exc.reason),
                status_code=status,
                headers=CONNECT_PAGE_HEADERS,
            )
        if _is_json_request(request):
            response: Response = _json(
                {
                    "ok": True,
                    "authenticated": True,
                    "device": claim.device.to_public_dict(),
                    "session": claim.session.to_public_dict(),
                    "next": next_path,
                }
            )
        else:
            response = RedirectResponse(
                next_path,
                status_code=303,
                headers=ACCESS_RESPONSE_HEADERS,
            )
        cookies.set_session(
            response,
            claim.session_token,
            context=context,
            expires_at=claim.session.expires_at,
        )
        return response

    async def current_session(request: Request) -> JSONResponse:
        context = _context(request)
        if context is None or not context.authenticated:
            return _json(
                {
                    "ok": True,
                    "authenticated": False,
                    "device_id": None,
                    "session_id": None,
                }
            )
        return _json(
            {
                "ok": True,
                "authenticated": True,
                "authentication_kind": context.authentication_kind.value,
                "device_id": context.device_id,
                "session_id": context.session_id,
                "presentation": context.presentation.value,
            }
        )

    async def refresh_session(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None or context.session_id is None:
            return _error(401, "authentication_required", "Sign in required.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service,
            context,
            bucket="session_management",
            limit=_MANAGEMENT_RATE_LIMIT,
        ):
            return _too_many_requests()

        current_token = cookies.extract_from_scope(
            dict(request.scope),
            context=context,
        )
        if current_token:
            authenticated = service.validate_session(current_token, touch=False)
        else:
            legacy_token = cookies.extract_legacy_from_scope(
                dict(request.scope),
                context=context,
            )
            authenticated = (
                service.validate_legacy_session(legacy_token, touch=False)
                if legacy_token
                else None
            )
        if authenticated is None or authenticated.session.id != context.session_id:
            response = _error(401, "authentication_required", "Sign in required.")
            cookies.clear(response)
            return response

        result = service.refresh_trusted_session(context.session_id)
        if result.session is None:
            response = _error(401, "authentication_required", "Sign in required.")
            cookies.clear(response)
            return response

        response = _json(
            {
                "ok": True,
                "authenticated": True,
                "renewed": result.renewed,
                "lifetime": result.session.lifetime.value,
                "expires_at": result.session.expires_at.isoformat(),
            }
        )
        if result.renewed and current_token:
            cookies.set_session(
                response,
                current_token,
                context=context,
                expires_at=result.session.expires_at,
            )
        return response

    async def logout(request: Request) -> JSONResponse:
        context = _context(request)
        if context is None or not context.authenticated:
            response = _error(401, "authentication_required", "Sign in required.")
        elif not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        elif not _rate_ok(
            service,
            context,
            bucket="session_management",
            limit=_MANAGEMENT_RATE_LIMIT,
        ):
            return _too_many_requests()
        else:
            revoked = bool(
                context.session_id and service.revoke_session(context.session_id)
            )
            response = _json({"ok": True, "revoked": revoked})
        cookies.clear(response)
        return response

    async def status(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        now = datetime.now(timezone.utc)
        devices = [
            device
            for device in service.list_devices(include_revoked=False)
            if device.revoked_at is None
        ]
        sessions = [
            session
            for session in service.list_sessions(include_revoked=False)
            if session.revoked_at is None and session.expires_at > now
        ]
        invitations = [
            invitation
            for invitation in service.list_invitations()
            if invitation.claimed_at is None
            and invitation.cancelled_at is None
            and invitation.expires_at > now
        ]
        return _json(
            {
                "ok": True,
                "deployment_mode": context.deployment_mode.value,
                "origin": context.origin,
                "devices": len(devices),
                "sessions": len(sessions),
                "active_invitations": len(invitations),
            }
        )

    async def route_inventory(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        return _json(
            {
                "ok": True,
                **_route_inventory(request, context).to_dict(),
                **_route_settings(context),
            }
        )

    async def set_listen_mode(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None or not context.is_local_owner:
            return _error(403, "local_owner_required", "Use the local owner session.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service, context, bucket="access_management", limit=_MANAGEMENT_RATE_LIMIT
        ):
            return _too_many_requests()
        try:
            payload = await _payload(request)
        except AccessPayloadTooLarge:
            return _payload_too_large()
        store = AccessRouteConfigStore()
        current = store.load_or_default()
        if payload.get("expected_mode") != current.listen_mode.value:
            return _error(409, "route_changed", "Listen mode changed. Refresh first.")
        try:
            mode = ListenMode(str(payload.get("listen_mode") or ""))
        except ValueError:
            return _error(400, "invalid_listen_mode", "Choose a supported mode.")
        from row_bot.access.launcher_control import request_launcher_restart

        result = await apply_listen_mode(
            store,
            mode,
            restart_child=lambda: request_launcher_restart().accepted,
        )
        return _json(
            {
                "ok": True,
                "listen_mode": result.config.listen_mode.value,
                "changed": result.changed,
                "restarted": result.restarted,
                "restart_required": result.restart_required,
                "reason": result.reason,
            }
        )

    async def change_trusted_origin(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None or not context.is_local_owner:
            return _error(403, "local_owner_required", "Use the local owner session.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service, context, bucket="access_management", limit=_MANAGEMENT_RATE_LIMIT
        ):
            return _too_many_requests()
        if "ROW_BOT_ALLOWED_HOSTS" in os.environ:
            return _error(
                409,
                "externally_managed",
                "Host admission is managed by deployment configuration.",
            )
        try:
            payload = await _payload(request)
        except AccessPayloadTooLarge:
            return _payload_too_large()
        store = AccessRouteConfigStore()
        previous = store.load_or_default()
        if payload.get("expected_origins") != list(previous.configured_origins):
            return _error(
                409, "route_changed", "Trusted addresses changed. Refresh first."
            )
        policy = getattr(request.app.state, "row_bot_access_runtime_policy", None)
        if policy is None:
            return _error(
                503, "route_policy_unavailable", "Live route policy is unavailable."
            )
        action = payload.get("action")
        origin = payload.get("origin")
        if not isinstance(origin, str) or action not in {"add", "remove"}:
            return _error(400, "invalid_origin", "Choose an exact origin and action.")
        try:
            config = (
                store.add_configured_origin(origin)
                if action == "add"
                else store.remove_configured_origin(origin)
            )
        except ValueError:
            return _error(
                400, "invalid_origin", "Choose one exact HTTP or HTTPS origin."
            )
        policy.set_configured_origins(config.configured_origins)
        return _json(
            {"ok": True, "configured_origins": list(config.configured_origins)}
        )

    def _remember_tailscale(status: object | None) -> None:
        ownership_store = TailscaleOwnershipStore()
        process_tailscale_status_cache().remember(
            instance_key=str(ownership_store.path.resolve(strict=False)),
            port=get_app_port(),
            status=status,
            ownership=ownership_store.load(),
        )

    async def tailscale_status(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        ownership_store = TailscaleOwnershipStore()
        verified = process_tailscale_status_cache().get(
            instance_key=str(ownership_store.path.resolve(strict=False)),
            port=get_app_port(),
            ownership=ownership_store.load(),
        )
        return _json(
            {
                "ok": True,
                "can_manage": context.is_local_owner,
                "status": verified.status.to_public_dict() if verified else None,
                "verified_at": verified.verified_at.isoformat() if verified else None,
            }
        )

    async def tailscale_check(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None or not context.is_local_owner:
            return _error(403, "local_owner_required", "Use the local owner session.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service, context, bucket="access_management", limit=_MANAGEMENT_RATE_LIMIT
        ):
            return _too_many_requests()
        status = await asyncio.to_thread(selected_tailscale.detect, port=get_app_port())
        _remember_tailscale(status)
        return _json({"ok": True, "status": status.to_public_dict()})

    async def tailscale_mutate(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None or not context.is_local_owner:
            return _error(403, "local_owner_required", "Use the local owner session.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service, context, bucket="access_management", limit=_MANAGEMENT_RATE_LIMIT
        ):
            return _too_many_requests()
        try:
            payload = await _payload(request)
        except AccessPayloadTooLarge:
            return _payload_too_large()
        action = payload.get("action")
        command_id = payload.get("command_id")
        if (
            action not in {"enable", "disable"}
            or not isinstance(command_id, str)
            or len(command_id) > 80
            or not command_id
        ):
            return _error(
                400, "invalid_command", "Choose a Tailscale action and command ID."
            )
        key = (service.instance_id, context.session_id or "local_owner", command_id)
        with _TAILSCALE_COMMAND_LOCK:
            prior = _TAILSCALE_COMMANDS.get(key)
            if prior is not None:
                if prior["action"] != action:
                    return _error(
                        409, "command_conflict", "Command ID belongs to another action."
                    )
                if prior.get("receipt") is None:
                    return _error(
                        409, "operation_pending", "Check the original Tailscale action."
                    )
                return _json(prior["receipt"])
            if len(_TAILSCALE_COMMANDS) >= 64:
                completed = next(
                    (
                        key
                        for key, value in _TAILSCALE_COMMANDS.items()
                        if value.get("receipt") is not None
                    ),
                    None,
                )
                if completed is None:
                    return _too_many_requests()
                _TAILSCALE_COMMANDS.pop(completed)
            _TAILSCALE_COMMANDS[key] = {"action": action, "receipt": None}
        try:
            if action == "enable":
                plan = await asyncio.to_thread(
                    selected_tailscale.plan, port=get_app_port()
                )
                _remember_tailscale(plan.status)
                if not plan.can_apply:
                    receipt = {
                        "ok": False,
                        "success": False,
                        "status": plan.status.to_public_dict(),
                        "error": plan.description,
                        "restart_required": False,
                    }
                else:
                    result = await asyncio.to_thread(selected_tailscale.apply, plan)
                    _remember_tailscale(result.status)
                    receipt = {
                        "ok": True,
                        "success": result.success,
                        "status": result.status.to_public_dict(),
                        "error": result.error,
                        "restart_required": result.restart_required,
                    }
            else:
                result = await asyncio.to_thread(selected_tailscale.disable_owned)
                _remember_tailscale(result.status)
                receipt = {
                    "ok": True,
                    "success": result.success,
                    "status": result.status.to_public_dict(),
                    "error": result.error,
                    "restart_required": result.restart_required,
                }
            if receipt["success"]:
                from row_bot.access.launcher_control import request_launcher_restart

                restart = request_launcher_restart()
                receipt["restart_required"] = not restart.accepted
            with _TAILSCALE_COMMAND_LOCK:
                _TAILSCALE_COMMANDS[key]["receipt"] = receipt
            return _json(receipt)
        except Exception:
            receipt = {
                "ok": False,
                "success": False,
                "status": None,
                "error": "The Tailscale outcome is uncertain. Check status before retrying.",
                "restart_required": False,
            }
            with _TAILSCALE_COMMAND_LOCK:
                _TAILSCALE_COMMANDS[key]["receipt"] = receipt
            return _json(receipt, status_code=503)

    async def tailscale_receipt(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None or not context.is_local_owner:
            return _error(403, "local_owner_required", "Use the local owner session.")
        command_id = str(request.path_params.get("command_id") or "")
        if not command_id or len(command_id) > 80:
            return _error(400, "invalid_command", "Invalid Tailscale command ID.")
        key = (service.instance_id, context.session_id or "local_owner", command_id)
        with _TAILSCALE_COMMAND_LOCK:
            entry = _TAILSCALE_COMMANDS.get(key)
            if entry is None:
                return _error(
                    404,
                    "receipt_missing",
                    "Check Tailscale status before another action.",
                )
            if entry["receipt"] is None:
                return _json({"ok": True, "pending": True}, status_code=202)
            return _json(entry["receipt"])

    async def create_invitation(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service,
            context,
            bucket="access_management",
            limit=_MANAGEMENT_RATE_LIMIT,
        ):
            return _too_many_requests()
        try:
            payload = await _payload(request)
        except AccessPayloadTooLarge:
            return _payload_too_large()
        if "profile" in payload or "access_profile" in payload:
            return _error(
                400,
                "invalid_invitation_options",
                "Authorization profiles are no longer supported; choose a layout.",
            )
        layout = str(payload.get("layout") or "desktop").strip().lower()
        lifetime_text = (
            str(payload.get("session_lifetime") or SessionLifetime.TRUSTED.value)
            .strip()
            .lower()
        )
        try:
            if layout not in {"desktop", "compact"}:
                raise ValueError
            lifetime = SessionLifetime(lifetime_text)
            if lifetime is SessionLifetime.MIGRATED:
                raise ValueError
        except ValueError:
            return _error(
                400,
                "invalid_invitation_options",
                "Choose desktop or compact layout and a trusted or temporary session.",
            )
        route_id = str(payload.get("route_id") or "").strip()
        if route_id:
            route = _route_inventory(request, context).resolve_invitation_route(
                route_id
            )
            if route is None:
                return _error(
                    409,
                    "route_changed",
                    "The selected connection route changed. Refresh and try again.",
                )
            intended_origin = route.origin
            access_route = route.kind.value
        else:
            intended_origin = str(payload.get("origin") or context.origin).strip()
            access_route = str(payload.get("access_route") or "")[:80] or None
        try:
            created = service.create_invitation(
                intended_origin=intended_origin,
                session_lifetime=lifetime,
                next_path=REMOTE_CLIENT_PATH,
                created_by=context.device_id or "local_owner",
                access_route=access_route,
            )
        except ValueError:
            return _error(
                400,
                "invalid_origin",
                "Choose an exact HTTP or HTTPS Row-Bot origin.",
            )
        return _json(
            {
                "ok": True,
                "invitation": {
                    **_invitation_public(created.invitation),
                    "layout": layout,
                },
                "invitation_url": created.invitation_url(),
            },
            status_code=201,
        )

    async def list_invitations(request: Request) -> JSONResponse:
        if _owner_context(request) is None:
            return _error(403, "forbidden", "Owner access is required.")
        return _json(
            {
                "ok": True,
                "invitations": [
                    _invitation_public(invitation)
                    for invitation in service.list_invitations()
                ],
            }
        )

    async def cancel_invitation(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service,
            context,
            bucket="access_management",
            limit=_MANAGEMENT_RATE_LIMIT,
        ):
            return _too_many_requests()
        invitation_id = str(request.path_params.get("invitation_id") or "")
        cancelled = service.cancel_invitation(invitation_id)
        return _json(
            {"ok": cancelled, "cancelled": cancelled},
            status_code=200 if cancelled else 404,
        )

    async def list_devices(request: Request) -> JSONResponse:
        if _owner_context(request) is None:
            return _error(403, "forbidden", "Owner access is required.")
        return _json(
            {
                "ok": True,
                "devices": [
                    _device_public(service, device)
                    for device in service.list_devices(include_revoked=True)
                ],
            }
        )

    async def revoke_device(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service,
            context,
            bucket="access_management",
            limit=_MANAGEMENT_RATE_LIMIT,
        ):
            return _too_many_requests()
        device_id = str(request.path_params.get("device_id") or "")
        revoked = service.revoke_device(device_id)
        response = _json(
            {"ok": revoked, "revoked": revoked},
            status_code=200 if revoked else 404,
        )
        if context.device_id == device_id:
            cookies.clear(response)
        return response

    async def revoke_session(request: Request) -> JSONResponse:
        context = _owner_context(request)
        if context is None:
            return _error(403, "forbidden", "Owner access is required.")
        if not _origin_ok(request, context):
            return _error(403, "origin_required", "Exact same origin is required.")
        if not _rate_ok(
            service,
            context,
            bucket="access_management",
            limit=_MANAGEMENT_RATE_LIMIT,
        ):
            return _too_many_requests()
        session_id = str(request.path_params.get("session_id") or "")
        revoked = service.revoke_session(session_id)
        response = _json(
            {"ok": revoked, "revoked": revoked},
            status_code=200 if revoked else 404,
        )
        if context.session_id == session_id:
            cookies.clear(response)
        return response

    router.add_api_route("/connect", connect_page, methods=["GET"])
    router.add_api_route(
        "/api/access/invitations/claim",
        claim_invitation,
        methods=["POST"],
    )
    router.add_api_route("/api/access/session", current_session, methods=["GET"])
    router.add_api_route(
        "/api/access/session/refresh",
        refresh_session,
        methods=["POST"],
    )
    router.add_api_route("/api/access/logout", logout, methods=["POST"])
    router.add_api_route("/api/access/status", status, methods=["GET"])
    router.add_api_route("/api/access/routes", route_inventory, methods=["GET"])
    router.add_api_route("/api/access/routes/listen", set_listen_mode, methods=["POST"])
    router.add_api_route(
        "/api/access/routes/origins", change_trusted_origin, methods=["POST"]
    )
    router.add_api_route("/api/access/tailscale", tailscale_status, methods=["GET"])
    router.add_api_route(
        "/api/access/tailscale/check", tailscale_check, methods=["POST"]
    )
    router.add_api_route(
        "/api/access/tailscale/actions", tailscale_mutate, methods=["POST"]
    )
    router.add_api_route(
        "/api/access/tailscale/actions/{command_id}", tailscale_receipt, methods=["GET"]
    )
    router.add_api_route(
        "/api/access/invitations",
        create_invitation,
        methods=["POST"],
    )
    router.add_api_route(
        "/api/access/invitations",
        list_invitations,
        methods=["GET"],
    )
    router.add_api_route(
        "/api/access/invitations/{invitation_id}/cancel",
        cancel_invitation,
        methods=["POST"],
    )
    router.add_api_route("/api/access/devices", list_devices, methods=["GET"])
    router.add_api_route(
        "/api/access/devices/{device_id}/revoke",
        revoke_device,
        methods=["POST"],
    )
    router.add_api_route(
        "/api/access/sessions/{session_id}/revoke",
        revoke_session,
        methods=["POST"],
    )
    return router


def register_access_routes(
    app,
    *,
    service: AccessService | None = None,
    config: AccessConfig | None = None,
    cookies: AccessCookieManager | None = None,
    tailscale_controller: TailscaleServeController | None = None,
) -> AccessRouteRegistration:
    """Register access routes and return middleware-ready dependencies."""

    selected_service = service or AccessService()
    selected_config = config or AccessConfig.from_env()
    selected_cookies = cookies or AccessCookieManager(selected_service.instance_id)
    registration = AccessRouteRegistration(
        service=selected_service,
        config=selected_config,
        cookies=selected_cookies,
        authenticator=AccessSessionAuthenticator(
            selected_service,
            selected_cookies,
        ),
    )
    app.state.row_bot_access = registration
    app.state.row_bot_access_service = selected_service
    app.state.row_bot_access_cookie_manager = selected_cookies
    app.include_router(
        build_access_router(
            service=selected_service,
            cookies=selected_cookies,
            tailscale_controller=tailscale_controller,
        )
    )
    return registration


__all__ = [
    "ACCESS_RESPONSE_HEADERS",
    "AccessRouteRegistration",
    "AccessSessionAuthenticator",
    "build_access_router",
    "register_access_routes",
]
