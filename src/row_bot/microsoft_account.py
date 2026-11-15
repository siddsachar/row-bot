"""Outlook through the person's own Microsoft app: sign-in with the Microsoft identity platform, and Graph.

The person registers a public client in Microsoft Entra (no secret: OAuth 2.0 with PKCE, RFC 7636). Its
Application (client) ID and tenant are kept in the keychain (``microsoft_client``). Signing in on this computer
uses the authorization code flow with a loopback address; from a phone or another computer, the device code
flow (RFC 8628): the person enters a short code at microsoft.com/devicelogin. Only the refresh token and who
signed in are kept, in the keychain (``microsoft``); Microsoft issues a new refresh token with each refresh,
and it replaces the old one there. Access tokens stay in this process's memory. No token is ever in a result,
a log or anything sent to the client.

Every request goes to Microsoft's own hosts (``LOGIN`` and ``GRAPH``) through Row-Bot's cancellable HTTP
client, without following redirects; nothing a person or a page supplies chooses where a request goes.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import logging
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from row_bot import account_tokens

logger = logging.getLogger(__name__)

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
OUTLOOK_WEB = "https://outlook.office.com"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# What Outlook's mail and calendar need, delegated: who signed in, mail (read, move, draft, send) and the calendar.
SCOPES = ("offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite")
NEEDED = ("User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite")
DEFAULT_TENANT = "common"  # Work or school accounts and personal Microsoft accounts.
TIMEOUT = 30
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TENANT = re.compile(r"common|organizations|consumers|" + _GUID.pattern + r"|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_CODE = re.compile(r"[\x21-\x7e]{1,64}")
_LIMIT = 256 * 1024  # A token or Graph answer larger than this is refused.
_REFRESH = threading.Lock()  # One refresh at a time: Microsoft's refresh token changes with each.
_ACCESS: dict[str, tuple[str, float]] = {}  # Refresh-token digest -> (access token, when it runs out).


class SignInError(RuntimeError):
    """A sign-in or refresh Microsoft refused or didn't finish. ``code`` names why: ``denied``, ``expired``,
    ``revoked``, ``admin_consent``, ``scopes``, ``client`` or ``failed``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GraphError(RuntimeError):
    """A Graph request that failed, with what it means in words (``plain``); never a token."""

    def __init__(self, status: int, code: str, plain: str) -> None:
        super().__init__(f"{status} {code}")
        self.status, self.code, self.plain = status, code, plain


@dataclass(frozen=True)
class DeviceCode:
    """A code Microsoft issued for a sign-in finished on another device."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def valid_client_id(value: str) -> bool:
    return bool(_GUID.fullmatch(value or ""))


def valid_tenant(value: str) -> bool:
    return bool(_TENANT.fullmatch(value or ""))


def client() -> dict | None:
    """The person's own app registration (``client_id``, ``tenant``), or None."""
    found = account_tokens.read("microsoft_client")
    if not found or not valid_client_id(str(found.get("client_id") or "")) or not valid_tenant(str(found.get("tenant") or "")):
        return None
    return {"client_id": found["client_id"], "tenant": found["tenant"]}


def save_client(client_id: str, tenant: str = "") -> None:
    """Keep the app registration; raises ValueError for an ID or tenant Microsoft can't have issued."""
    client_id, tenant = client_id.strip(), (tenant.strip().lower() or DEFAULT_TENANT)
    if not valid_client_id(client_id) or not valid_tenant(tenant):
        raise ValueError("invalid_client")
    account_tokens.write("microsoft_client", {"client_id": client_id.lower(), "tenant": tenant})


def _http() -> Any:
    from row_bot.providers.transports.cancellable_http import cancellable_http_client
    return cancellable_http_client(timeout=TIMEOUT, follow_redirects=False)


def _endpoint(found: dict, kind: str) -> str:
    return f"{LOGIN}/{quote(found['tenant'], safe='')}/oauth2/v2.0/{kind}"


def pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(found: dict, redirect_uri: str, state: str, challenge: str) -> str:
    """Microsoft's sign-in page for one sign-in on this computer, returning to ``redirect_uri``."""
    return _endpoint(found, "authorize") + "?" + urlencode({
        "client_id": found["client_id"], "response_type": "code", "redirect_uri": redirect_uri,
        "response_mode": "query", "scope": " ".join(SCOPES), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    })


def _answer(response: Any) -> dict:
    """A JSON object answer read up to its cap; {} for anything else."""
    body = response.content
    if len(body) > _LIMIT:
        return {}
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _refused(answer: dict) -> SignInError:
    """Why Microsoft's token endpoint refused, from its error and AADSTS code (never its free text in logs)."""
    error, codes = str(answer.get("error") or ""), answer.get("error_codes") or []
    codes = {int(code) for code in codes if isinstance(code, int)}
    if codes & {65001, 90094, 90095}:
        return SignInError("admin_consent")  # The organisation lets only an admin approve this app.
    if error in {"access_denied", "authorization_declined"}:
        return SignInError("denied")
    if error in {"expired_token", "bad_verification_code"}:
        return SignInError("expired")
    if error == "invalid_grant":
        return SignInError("revoked")  # The refresh token was revoked, expired, or belongs to another app.
    if error in {"invalid_client", "unauthorized_client"} or codes & {700016, 7000218, 50011, 700025}:
        return SignInError("client")
    return SignInError("failed")


def _token(found: dict, form: dict, http: Any) -> dict:
    response = http.post(_endpoint(found, "token"), data={"client_id": found["client_id"], **form},
                         headers={"Accept": "application/json"})
    answer = _answer(response)
    if response.status_code != 200 or "error" in answer:
        raise _refused(answer)
    return answer


def exchange(found: dict, code: str, redirect_uri: str, verifier: str, http: Any) -> dict:
    """The tokens for a sign-in on this computer's code (with its PKCE verifier)."""
    return _token(found, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                          "code_verifier": verifier, "scope": " ".join(SCOPES)}, http)


def device_start(found: dict, http: Any) -> DeviceCode:
    """Ask Microsoft for a code to sign in on another device."""
    response = http.post(_endpoint(found, "devicecode"), data={"client_id": found["client_id"], "scope": " ".join(SCOPES)},
                         headers={"Accept": "application/json"})
    answer = _answer(response)
    if response.status_code != 200 or "error" in answer:
        raise _refused(answer)
    page, lasts, every = answer.get("verification_uri"), answer.get("expires_in"), answer.get("interval", 5)
    if (not isinstance(answer.get("device_code"), str) or not 0 < len(answer["device_code"]) <= 4096
            or not isinstance(answer.get("user_code"), str) or not _CODE.fullmatch(answer["user_code"])
            or not isinstance(page, str) or not page.startswith("https://") or len(page) > 512
            or type(lasts) is not int or not 0 < lasts <= 3600 or type(every) is not int or not 0 < every <= 60):
        raise SignInError("failed")
    return DeviceCode(answer["device_code"], answer["user_code"], page, lasts, every)


def device_poll(found: dict, code: DeviceCode, http: Any) -> dict | str:
    """The tokens once the person allowed it, else ``authorization_pending`` or ``slow_down``; raises
    ``SignInError`` when it can't finish (declined, expired)."""
    response = http.post(_endpoint(found, "token"), headers={"Accept": "application/json"}, data={
        "client_id": found["client_id"], "grant_type": DEVICE_GRANT, "device_code": code.device_code})
    answer = _answer(response)
    if response.status_code == 200 and "error" not in answer:
        return answer
    if answer.get("error") in {"authorization_pending", "slow_down"}:
        return answer["error"]
    raise _refused(answer)


def _scopes(answer: dict) -> set[str]:
    granted = str(answer.get("scope") or "").split()
    return {scope.rsplit("/", 1)[-1].casefold() for scope in granted}


def finish_sign_in(found: dict, answer: dict, http: Any) -> dict:
    """What a new sign-in keeps: its refresh token, the app it belongs to, and who signed in (from /me)."""
    if not isinstance(answer.get("access_token"), str) or not isinstance(answer.get("refresh_token"), str):
        raise SignInError("failed")  # Without a refresh token the sign-in wouldn't last (offline_access).
    if {scope.casefold() for scope in NEEDED} - _scopes(answer):
        raise SignInError("scopes")  # Mail or Calendar access wasn't granted: the app's permissions miss one.
    me = _graph_with(answer["access_token"], "GET", "/me", params={"$select": "mail,userPrincipalName,displayName"},
                     http=http)
    address = str(me.get("mail") or me.get("userPrincipalName") or "")[:256]
    saved = {"refresh_token": answer["refresh_token"], "client_id": found["client_id"], "tenant": found["tenant"],
             "address": address, "name": str(me.get("displayName") or "")[:256], "scope": " ".join(sorted(_scopes(answer)))}
    _remember(answer, saved["refresh_token"])
    return saved


def _digest(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _remember(answer: dict, refresh_token: str) -> None:
    lasts = answer.get("expires_in")
    lasts = lasts if type(lasts) is int and lasts > 0 else 3600
    _ACCESS.clear()  # One sign-in at a time.
    _ACCESS[_digest(refresh_token)] = (answer["access_token"], time.monotonic() + lasts - 120)


def signed_in() -> dict | None:
    """The saved sign-in (without its token): ``address``, ``name``, ``client_id``, ``tenant``; or None."""
    saved = account_tokens.read("microsoft")
    if not saved or not isinstance(saved.get("refresh_token"), str):
        return None
    return {key: str(saved.get(key) or "") for key in ("address", "name", "client_id", "tenant")}


def address() -> str:
    return (signed_in() or {}).get("address", "")


def state() -> str:
    """The account's state as Settings shows it: a sign-in made with another app registration needs a new one."""
    found, saved = client(), signed_in()
    if found is None:
        return "not_configured"
    if saved is not None and saved["client_id"] != found["client_id"]:
        return "invalid"
    return account_tokens.state("microsoft")


def access_token(*, force: bool = False) -> str:
    """A current access token, refreshed (once, whoever asks) when it has run out; the new refresh token is
    kept in the keychain. Raises ``SignInError`` (``not_signed_in``, ``revoked``, ...) when it can't be had."""
    found = client()
    saved = account_tokens.read("microsoft")
    if found is None or not saved or not isinstance(saved.get("refresh_token"), str):
        raise SignInError("not_signed_in")
    if saved.get("client_id") != found["client_id"]:
        raise SignInError("revoked")  # Signed in with another app registration.
    held = _ACCESS.get(_digest(saved["refresh_token"]))
    if held and not force and time.monotonic() < held[1]:
        return held[0]
    with _REFRESH:
        saved = account_tokens.read("microsoft") or {}
        refresh_token = str(saved.get("refresh_token") or "")
        held = _ACCESS.get(_digest(refresh_token)) if refresh_token else None
        if held and not force and time.monotonic() < held[1]:
            return held[0]  # Another request refreshed it while this one waited.
        if not refresh_token:
            raise SignInError("not_signed_in")
        with _http() as http:
            answer = _token(found, {"grant_type": "refresh_token", "refresh_token": refresh_token,
                                    "scope": " ".join(SCOPES)}, http)
        if not isinstance(answer.get("access_token"), str):
            raise SignInError("failed")
        renewed = answer.get("refresh_token") if isinstance(answer.get("refresh_token"), str) else refresh_token
        if renewed != refresh_token:
            try:
                account_tokens.write("microsoft", {**saved, "refresh_token": renewed})
            except account_tokens.AccountTokenError:
                logger.warning("The renewed Microsoft sign-in could not be saved; it is used for this session only")
        _remember(answer, renewed)
        return answer["access_token"]


def forget() -> None:
    """Remove the saved sign-in and its access from this process."""
    account_tokens.delete("microsoft")
    _ACCESS.clear()


def check() -> tuple[str, str]:
    """Probe the saved sign-in (refreshing it); the verdict is remembered for Settings and Monitor."""
    if signed_in() is None:
        return "missing", "No saved Microsoft sign-in"
    try:
        access_token(force=True)
        graph("GET", "/me", params={"$select": "id"})
        result = ("refreshed", "Signed in")
    except SignInError as error:
        result = (("expired", "Sign in to Microsoft again") if error.code in {"revoked", "not_signed_in", "client"}
                  else ("error", "Microsoft didn't answer the check"))
    except GraphError as error:
        result = ("expired", "Sign in to Microsoft again") if error.status == 401 else ("error", error.plain)
    except Exception as error:
        result = ("error", f"The check couldn't reach Microsoft ({type(error).__name__})")
    account_tokens.record_check("microsoft", result[0])
    return result


# --- Graph -------------------------------------------------------------------------------------------------

_PLAIN = {
    400: "Microsoft refused the request as written.",
    401: "Microsoft no longer accepts this sign-in. Sign in to Outlook again in Settings › Apps › Outlook.",
    403: "This mailbox doesn't allow that. Its admin may need to approve the app's permissions.",
    404: "Outlook couldn't find that. It may have been moved or deleted.",
    409: "Outlook couldn't do that because the item changed. Read it again and retry.",
    413: "That's too large for Outlook to accept.",
    429: "Outlook is busy. Try again in a minute.",
}


def _graph_error(response: Any) -> GraphError:
    error = _answer(response).get("error")
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    status = response.status_code
    if code in {"MailboxNotEnabledForRESTAPI", "MailboxNotHostedInExchangeOnline"}:
        plain = "This account has no Outlook mailbox Microsoft Graph can reach."
    elif code == "ErrorMessageSizeExceeded":
        plain = _PLAIN[413]
    else:
        plain = _PLAIN.get(status) or ("Outlook had a problem. Try again later." if status >= 500
                                       else "Outlook couldn't do that.")
    return GraphError(status, code[:80], plain)


def _graph_with(token: str, method: str, path: str, *, params: dict | None = None, body: Any = None,
                headers: dict | None = None, http: Any = None) -> dict:
    if not path.startswith("/") or "://" in path or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("graph_path")  # Graph's own paths only; never an address a person or page wrote.
    sent = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
    own = http is None
    http = http or _http()
    try:
        response = http.request(method, GRAPH + path, params=params, json=body, headers=sent)
    finally:
        if own:
            http.close()
    if response.status_code >= 400:
        raise _graph_error(response)
    if response.status_code in {202, 204} or not response.content:
        return {}
    return _answer(response)


def graph(method: str, path: str, *, params: dict | None = None, body: Any = None, headers: dict | None = None) -> dict:
    """One Graph v1.0 request as the signed-in person; a token that stopped working is renewed once."""
    try:
        return _graph_with(access_token(), method, path, params=params, body=body, headers=headers)
    except GraphError as error:
        if error.status != 401:
            raise
    return _graph_with(access_token(force=True), method, path, params=params, body=body, headers=headers)


# Graph takes an upload session's pieces in multiples of 320 KiB.
UPLOAD_PIECE = 10 * 320 * 1024
_UPLOAD_HOSTS = (".office.com", ".office365.com", ".outlook.com")


def upload(url: str, path: Any, *, http: Any = None) -> None:
    """Upload a file to a Graph upload session's own address, in pieces (attachments over 3 MB).

    The address carries its own key, so no token goes with it; only an address on Microsoft's Outlook hosts
    (or Graph's own) is used.
    """
    parts, graph_parts = urlsplit(url), urlsplit(GRAPH)
    own = (parts.scheme, parts.netloc) == (graph_parts.scheme, graph_parts.netloc)
    if not own and not (parts.scheme == "https" and (parts.hostname or "").endswith(_UPLOAD_HOSTS)):
        raise ValueError("upload_url")
    total = path.stat().st_size
    owned = http is None
    http = http or _http()
    try:
        with path.open("rb") as file:
            start = 0
            while start < total:
                piece = file.read(UPLOAD_PIECE)
                end = start + len(piece) - 1
                response = http.request("PUT", url, content=piece, headers={
                    "Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(piece))})
                if response.status_code >= 400:
                    raise _graph_error(response)
                start = end + 1
    finally:
        if owned:
            http.close()


def segment(value: str) -> str:
    """A Graph id as one path segment (message and event ids carry ``/``, ``+`` and ``=``)."""
    if not value or len(value) > 1024 or value in {".", ".."} or any(ord(char) < 33 for char in value):
        raise ValueError("graph_id")
    return quote(value, safe="")
